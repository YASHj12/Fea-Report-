#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
FEA Report Maker - local web app.

    python app/server.py            (opens your browser at http://127.0.0.1:5055)

Everything runs on this computer.  Nothing is uploaded anywhere.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import logging
import math
import os
import re
import shutil
import socket
import statistics
import subprocess
import sys
import tempfile
import threading
import time
import urllib.request
import uuid
import webbrowser
from datetime import datetime
from pathlib import Path
from urllib.parse import urlparse

from flask import Flask, abort, jsonify, request, send_file, send_from_directory
from PIL import Image
from werkzeug.utils import secure_filename

APP_DIR = Path(__file__).resolve().parent
ROOT = APP_DIR.parent
sys.path.insert(0, str(APP_DIR))
sys.path.insert(0, str(ROOT / "engine"))

import analyzer            # noqa: E402
import mw_report as mw     # noqa: E402

import pptx_preview as pv  # noqa: E402     (the program's own slide renderer: no LibreOffice needed)

VERSION = "2.3"
IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".bmp", ".tif", ".tiff", ".webp"}
DEFAULTS = {
    "port": 5055,
    "report_no_pattern": "MWI/FEA/ACK-{job}/01",
    "fos": 1.3,
    "mm_format": "sig3",
    "author": "Mech Well",
    "cover_label": "FEA REPORT",
    "units": "Units: Length = mm, Mass = tonnes, Time = second, Force = Newton",
    "material_header": ["Material", "Item", "Young's Modulus\n(MPa)", "Poisson's\nRatio", "Density\n(t/mm\u00b3)",
                        "Yield Stress\n(MPa)", "Allowable Von-Mises\nStress (MPa)"],
    "material": {"material": "IS 2062", "item": "Duct", "E": "191400", "nu": "0.3", "rho": "7.8E-9"},
    # --- engineering judgement (all tunable) -------------------------------------------------------------
    "singularity_factor": 2.0,        # peak stress >= this x YIELD -> "singularity suspected": the engineer must choose how to report it
    "min_legend_pt": 5.0,             # legend text on the slide below this size = hard to read (the delivered template standard is ~5.5 pt)
    "show_utilisation": True,         # "(utilisation 25 %)" in the within-limit sentence
    "keep_copy_in_outputs": True,     # a downloaded report is also copied into the outputs/ folder of the program
    "missing_pictures": "skip",       # a picture that does not exist: "skip" = leave that slide out, "frame" = keep an empty frame
    # ANSYS Meshing Help (current): skewness 0.75-0.9 poor, >= 0.9 bad (sliver); guidance: minimum orthogonal quality > 0.1
    "mesh_limits": {"skew_max_warn": 0.9, "skew_max_info": 0.75, "skew_avg_info": 0.5, "oq_min_warn": 0.1},
    "assumption_library": [
        "Linear elastic, isotropic material behaviour (no plasticity).",
        "Static loading: inertia and dynamic effects are neglected.",
        "Small-deflection theory (geometric nonlinearity is not considered).",
        "Contacts between parts are bonded (no sliding or separation).",
        "Welds are not modelled in detail (parts are fully fused).",
        "Bolts and pins are idealised (no pre-tension, no thread detail).",
        "Pressure is applied uniformly on the loaded surfaces.",
        "Self weight is included (standard earth gravity).",
        "Temperature effects are not considered.",
        "Buckling, fatigue and vibration are not assessed in this report.",
    ],
}


def load_settings() -> dict:
    s = json.loads(json.dumps(DEFAULTS))
    f = ROOT / "settings.json"
    if f.exists():
        try:
            user = json.loads(f.read_text(encoding="utf-8-sig"))
            for k, v in user.items():
                if k.startswith("_"):
                    continue
                if isinstance(v, dict) and isinstance(s.get(k), dict):
                    s[k].update(v)
                else:
                    s[k] = v
        except Exception as e:                                            # noqa: BLE001
            print(f"  WARNING: settings.json could not be read ({e}). Using the built-in defaults.")
    return s


SETTINGS = load_settings()
WORK = ROOT / "work" / "sessions"
OUT = ROOT / "outputs"
for d in (WORK, OUT):
    d.mkdir(parents=True, exist_ok=True)

app = Flask(__name__, static_folder=str(APP_DIR / "static"), static_url_path="/static")
app.config["MAX_CONTENT_LENGTH"] = 600 * 1024 * 1024
app.config["SEND_FILE_MAX_AGE_DEFAULT"] = 0
logging.getLogger("werkzeug").setLevel(logging.WARNING)

BUILD_LOCK = threading.RLock()
PDF_LOCK = threading.Lock()
RENDER_LOCK = threading.Lock()
BUILDS_KEEP = 6
RUNTIME = {"loopback": True}
OCR_MESSAGE = ("The picture reader (Tesseract OCR) is not installed, so the numbers cannot be read from the pictures automatically. "
               "You can carry on: the pictures are sorted by their file names and you type the numbers. "
               "To have them read for you, install Tesseract once (step 1B in README.txt) and restart this program.")


class UserError(Exception):
    """A problem the user can fix - shown as a plain message in the web page."""


@app.errorhandler(UserError)
def _user_error(e):
    return jsonify(error=str(e)), 400


@app.errorhandler(413)
def _too_big(_e):
    return jsonify(error="The pictures are too big (limit 600 MB in total)."), 413


@app.errorhandler(404)
def _not_found(_e):
    if request.path.startswith("/api/"):
        return jsonify(error="Not found (the session may have expired - upload the pictures again)."), 404
    return "Not found", 404


@app.errorhandler(Exception)
def _crash(e):
    from werkzeug.exceptions import HTTPException
    if isinstance(e, HTTPException):
        return e
    import traceback
    traceback.print_exc()
    return jsonify(error=f"Something went wrong: {e}"), 500


@app.before_request
def _same_origin_only():
    """When running on this computer only, refuse requests that come from OTHER websites."""
    if not RUNTIME["loopback"] or request.method != "POST":
        return None
    origin = request.headers.get("Origin")
    if origin and urlparse(origin).netloc != request.host:
        return jsonify(error="Blocked: request from another site."), 403
    return None


def _uploaded_pictures():
    """the multipart 'files' parts that are really pictures (a part without a file name is not a picture)"""
    out, nameless = [], 0
    for f in request.files.getlist("files"):
        if not f:
            continue
        if not f.filename:
            nameless += 1
            continue
        out.append(f)
    return out, nameless


# ───────────────────────────────────────────── helpers ─────────────────────────────────────────────
def to_float(v):
    if v is None:
        return None
    try:
        x = float(str(v).replace(",", ".").strip())
    except ValueError:
        return None
    return x if math.isfinite(x) else None


def ocr_ready() -> bool:
    try:
        return analyzer.pytesseract is not None and analyzer.find_tesseract() is not None
    except Exception:                                                     # noqa: BLE001
        return False


def find_soffice():
    if os.environ.get("FEA_DISABLE_LIBREOFFICE"):           # tests, or a computer where LibreOffice misbehaves
        return None
    for c in (os.environ.get("SOFFICE_CMD"), shutil.which("soffice"), shutil.which("libreoffice"),
              r"C:\Program Files\LibreOffice\program\soffice.exe",
              r"C:\Program Files (x86)\LibreOffice\program\soffice.exe",
              "/Applications/LibreOffice.app/Contents/MacOS/soffice"):
        if c and Path(c).exists():
            return str(c)
    return None


SID_RE = re.compile(r"^[0-9a-f]{12}$")


def sess_dir(sid) -> Path:
    if not isinstance(sid, str) or not SID_RE.match(sid):
        abort(404)
    d = WORK / sid
    if not d.is_dir():
        abort(404)
    return d


def load_infos(sid):
    return json.loads((sess_dir(sid) / "session.json").read_text(encoding="utf-8"))


def save_infos(sid, infos):
    (sess_dir(sid) / "session.json").write_text(json.dumps(infos), encoding="utf-8")


def public(sid, i):
    d = {k: v for k, v in i.items() if k not in ("path", "_done", "hash")}
    d["url"] = f"/api/img/{sid}/{i['id']}"
    return d


def payload(sid, infos, draft, cover=None):
    out = {"sid": sid, "images": [public(sid, i) for i in infos], "cases": draft["cases"], "geometry": draft["geometry"],
           "meshes": draft["meshes"], "warnings": draft["warnings"],
           "match_pending": [i["id"] for i in infos if i.get("match_pending")]}
    if cover is not None:
        out["cover"] = cover
    return out


def cleanup_old_sessions(days=7):
    now = time.time()
    WORK.mkdir(parents=True, exist_ok=True)
    for d in WORK.iterdir():
        try:
            if d.is_dir() and now - d.stat().st_mtime > days * 86400:
                shutil.rmtree(d, ignore_errors=True)
        except OSError:
            pass


def run_analysis(paths, names, sid=None):
    t0 = time.time()
    analyzer.MATCH_DEADLINE["at"] = time.monotonic() + analyzer.MATCH_SYNC     # the page must not wait for pixel matching
    try:
        res = analyzer.analyze(paths, SETTINGS, names=names)
    finally:
        analyzer.MATCH_DEADLINE["at"] = None
    pend = [i["id"] for i in res["images"] if i.get("match_pending")]
    res["match_pending"] = pend
    if sid and pend:
        _start_matcher(sid, pend)
    return res, round(time.time() - t0, 1)


def _start_matcher(sid, ids):
    """finish, in the background, the view-matching that the sync budget left behind; the page polls /api/matches"""
    def work():
        try:
            analyzer.MATCH_DEADLINE["at"] = None
            infos = load_infos(sid)
            analyzer.finish(infos, SETTINGS)
            done = {i["id"]: i for i in infos}
            cur = load_infos(sid)                                   # re-read: never clobber what the engineer changed meanwhile
            for i in cur:
                g = done.get(i["id"])
                if not g or not (i.get("match_pending") and i["id"] in ids):
                    continue
                i["match"] = g.get("match")
                i["match_ambiguous"] = g.get("match_ambiguous")
                i["match_pending"] = False
                if g.get("match") and g.get("parent") is not None:
                    i["parent"], i["parent_why"] = g.get("parent"), g.get("parent_why")
            save_infos(sid, cur)
        except Exception:                                          # noqa: BLE001
            logging.exception("background matching failed")
    threading.Thread(target=work, daemon=True).start()


# ───────────────────────────────────────────── pages & status ─────────────────────────────────────────────
_STATIC_TAG = {}


def static_tag(name: str) -> str:
    """content hash in the URL: a browser tab can never run yesterday's app.js against today's program"""
    path = Path(app.static_folder) / name
    try:
        mtime = path.stat().st_mtime_ns
    except OSError:
        return VERSION
    hit = _STATIC_TAG.get(name)
    if not hit or hit[0] != mtime:
        hit = _STATIC_TAG[name] = (mtime, f"{VERSION}-{hashlib.md5(path.read_bytes()).hexdigest()[:10]}")
    return hit[1]


@app.get("/")
def index():
    html = (Path(app.static_folder) / "index.html").read_text(encoding="utf-8")
    for name in ("app.js", "style.css"):                              # a new version never uses an old, cached file
        html = html.replace(f'/static/{name}"', f'/static/{name}?v={static_tag(name)}"')
    resp = app.response_class(html, mimetype="text/html")
    resp.headers["Cache-Control"] = "no-store"
    return resp


@app.get("/api/status")
def api_status():
    soffice = find_soffice()
    return jsonify(
        app="FEA Report Maker", version=VERSION, root=str(ROOT), loopback=bool(RUNTIME["loopback"]),
        can_open_folder=can_open_folder(),
        ocr=ocr_ready(), ocr_message=OCR_MESSAGE, preview=True,
        pdf=True, pdf_method="libreoffice" if soffice else "images", outputs=str(OUT),
        has_examples=any((ROOT / "examples").glob("*.png")),
        settings={"fos": SETTINGS["fos"], "units": SETTINGS["units"], "material": SETTINGS["material"],
                  "material_header": SETTINGS["material_header"], "assumption_library": SETTINGS["assumption_library"],
                  "show_utilisation": SETTINGS["show_utilisation"], "min_legend_pt": SETTINGS["min_legend_pt"],
                  "singularity_factor": SETTINGS["singularity_factor"],
                  "missing_pictures": "frame" if SETTINGS.get("missing_pictures") == "frame" else "skip"},
        result_kinds=[{"key": k, "label": v["label"], "short": v["short"], "unit": v["unit"], "group": v["group"]}
                      for k, v in analyzer.RESULT_KINDS.items()],
        view_kinds=["section", "detail"])


# ───────────────────────────────────────────── step 1: read the pictures ─────────────────────────────────────────────
def _new_session():
    sid = uuid.uuid4().hex[:12]
    (WORK / sid / "images").mkdir(parents=True)
    return sid, WORK / sid / "images"


@app.post("/api/analyze")
def api_analyze():
    files, nameless = _uploaded_pictures()
    if not files:
        raise UserError("The list arrived but the pictures themselves did not (a part without a file name is not a "
                        "picture). This is usually an OLD tab: reload the page once (Ctrl+R), add the pictures again "
                        "and press Next." if nameless else "No pictures received.")
    sid, folder = _new_session()
    paths, names, skipped = [], [], []
    for f in files:
        ext = Path(f.filename).suffix.lower()
        if ext not in IMAGE_EXTS:
            skipped.append(f.filename)
            continue
        dest = folder / f"{len(paths):02d}_{secure_filename(f.filename) or 'picture' + ext}"
        f.save(dest)
        try:
            with Image.open(dest) as im:
                im.verify()
        except Exception:                                                 # noqa: BLE001
            dest.unlink(missing_ok=True)
            skipped.append(f.filename)
            continue
        paths.append(dest)
        names.append(Path(f.filename).name)
    if not paths:
        shutil.rmtree(WORK / sid, ignore_errors=True)
        raise UserError("None of the files could be opened as pictures (use PNG or JPG).")
    res, took = run_analysis(paths, names, sid)
    save_infos(sid, res["images"])
    out = payload(sid, res["images"], res, res["cover"])
    out["match_pending"] = res.get("match_pending") or []
    out["took"] = took
    out["skipped"] = skipped
    out["manual"] = bool(res.get("manual"))
    return jsonify(out)


@app.post("/api/analyze_example")
def api_analyze_example():
    src = sorted(p for p in (ROOT / "examples").glob("*") if p.suffix.lower() in IMAGE_EXTS)
    if not src:
        raise UserError("The examples folder is empty.")
    sid, folder = _new_session()
    paths = []
    for k, p in enumerate(src):
        dest = folder / f"{k:02d}_{p.name}"
        shutil.copyfile(p, dest)
        paths.append(dest)
    res, took = run_analysis(paths, [p.name for p in src], sid)
    save_infos(sid, res["images"])
    out = payload(sid, res["images"], res, res["cover"])
    out["match_pending"] = res.get("match_pending") or []
    out["took"] = took
    out["skipped"] = []
    out["manual"] = bool(res.get("manual"))
    return jsonify(out)


@app.post("/api/analyze_more")
def api_analyze_more():
    """More pictures for the SAME report - the engineer keeps them in different folders and adds them one folder at a
    time (council item U2).  Everything already typed stays; the new pictures are read and folded into the draft."""
    files = [f for f in request.files.getlist("files") if f and f.filename]
    sid = request.form.get("sid") or (request.args.get("sid") or "")
    if not files:
        raise UserError("No pictures received.")
    infos = load_infos(sid)
    folder = sess_dir(sid) / "images"
    seen = {i.get("hash") for i in infos}
    paths, names, skipped, added = [], [], [], 0
    base = max([i["id"] for i in infos] or [-1]) + 1
    import hashlib as _h
    for f in files:
        ext = Path(f.filename).suffix.lower()
        if ext not in IMAGE_EXTS:
            skipped.append(f.filename)
            continue
        dest = folder / f"{len(infos) + added:02d}_{secure_filename(f.filename) or 'picture' + ext}"
        f.save(dest)
        try:
            with Image.open(dest) as im:
                im.verify()
            if _h.md5(dest.read_bytes()).hexdigest() in seen:
                dest.unlink(missing_ok=True)
                skipped.append(f"{f.filename} (already in this report)")
                continue
        except Exception:                                                 # noqa: BLE001
            dest.unlink(missing_ok=True)
            skipped.append(f.filename)
            continue
        paths.append(dest)
        names.append(Path(f.filename).name)
        added += 1
    if not paths:
        raise UserError("None of these files could be added (not pictures, or already in this report).")
    res, took = run_analysis(paths, names)
    for k, i in enumerate(res["images"]):
        i["id"] = base + k
    infos.extend(res["images"])
    analyzer.MATCH_DEADLINE["at"] = time.monotonic() + analyzer.MATCH_SYNC
    try:
        draft = analyzer.finish(infos, SETTINGS)
    finally:
        analyzer.MATCH_DEADLINE["at"] = None
    pend = [i["id"] for i in infos if i.get("match_pending")]
    if pend:
        _start_matcher(sid, pend)
    save_infos(sid, infos)
    out = payload(sid, infos, draft)
    out["match_pending"] = pend
    out["took"] = took
    out["skipped"] = skipped
    out["added"] = added
    out["manual"] = bool(res.get("manual"))
    return jsonify(out)


@app.post("/api/regroup")
def api_regroup():
    p = request.get_json(force=True)
    sid = p.get("sid")
    infos = load_infos(sid)
    draft = analyzer.regroup(infos, p.get("assign", []), SETTINGS)
    save_infos(sid, infos)
    return jsonify(payload(sid, infos, draft))


@app.get("/api/matches")
def api_matches():
    """the page polls this while background view-matching runs: which views are still being placed, and what was found"""
    infos = load_infos(request.args.get("sid", ""))
    return jsonify({"pending": [i["id"] for i in infos if i.get("match_pending")],
                    "matches": {str(i["id"]): {"match": i.get("match"), "parent": i.get("parent"),
                                               "parent_why": i.get("parent_why"),
                                               "match_ambiguous": i.get("match_ambiguous")}
                                for i in infos if i.get("match") or i.get("parent") is not None}})


@app.get("/api/img/<sid>/<int:pid>")
def api_img(sid, pid):
    infos = load_infos(sid)
    info = next((i for i in infos if i["id"] == pid), None)
    if not info:
        abort(404)
    w = request.args.get("w", type=int)
    if not w:
        return send_file(info["path"], max_age=3600)
    w = max(60, min(w, 1600))
    thumb = sess_dir(sid) / "thumbs" / f"{pid}_{w}.jpg"
    if not thumb.exists():
        thumb.parent.mkdir(exist_ok=True)
        im = analyzer.load_rgb(info["path"])
        im.thumbnail((w, w * 3))
        im.save(thumb, "JPEG", quality=82)
    return send_file(thumb, max_age=3600)


@app.get("/api/crop/<sid>/<int:pid>/<kind>")
def api_crop(sid, pid, kind):
    """The part of a picture a number was read from - shown next to the number as evidence."""
    if kind not in ("legend", "bc", "header"):
        abort(404)
    infos = load_infos(sid)
    info = next((i for i in infos if i["id"] == pid), None)
    if not info:
        abort(404)
    w = max(120, min(request.args.get("w", 360, type=int), 1200))
    f = sess_dir(sid) / "thumbs" / f"crop_{pid}_{kind}_{w}.png"
    if not f.exists():
        f.parent.mkdir(exist_ok=True)
        im = analyzer.crop_for(info["path"], kind, info.get("crop_" + kind))
        if im.width < w:
            im = im.resize((w, max(1, round(im.height * w / im.width))), Image.LANCZOS)
        elif im.width > w * 1.5:
            im.thumbnail((w * 1.5, 4000))
        im.save(f, "PNG")
    return send_file(f, max_age=3600)


# ───────────────────────────────────────────── FEA judgement: wording, verdict, checks ─────────────────────────────────────────────
TIERS = ("pass", "exceeds", "beyond_yield", "suspect")
BASIS_TEXT = {"asis": "reported as it is (peak stress compared with the allowable)",
              "singularity": "local singularity - the stress away from the peak is reported",
              "refine": "not concluded - the model must be refined"}


def make_ctx(p: dict) -> dict:
    fos = to_float(p.get("fos")) or SETTINGS["fos"]
    allow, sy = to_float(p.get("allowable")), to_float(p.get("yield"))
    allow = allow if (allow is not None and allow > 0) else None
    sy = sy if (sy is not None and sy > 0) else None
    return {"allow": allow, "sy": sy, "fos": fos,
            "short": (p.get("short") or "").strip() or "structure",
            "util": bool(p.get("show_utilisation", SETTINGS["show_utilisation"])),
            "lim": to_float(p.get("def_limit")), "k": float(SETTINGS["singularity_factor"])}


def tier_of(sx, ctx):
    allow, sy = ctx["allow"], ctx["sy"]
    if sx is None or allow is None:
        return None
    if sx <= allow:
        return "pass"
    if sy is None or sx <= sy:
        return "exceeds"
    return "beyond_yield" if sx < ctx["k"] * sy else "suspect"


def _clean(s):
    return re.sub(r"\s+", " ", str(s or "")).strip().rstrip(".")


def compose_case(c: dict, ctx: dict) -> dict:
    """All the wording of one load case + the verdict.  The template's sentences (mw.derive_case_texts) are the starting
    point, so an ordinary job gets exactly the template wording; extras are added only when the engineer asked for them.
    A missing number never stops anything: its sentence is left out; without an allowable stress the wording is neutral and
    the verdict is a bracketed placeholder that the engineer fills in."""
    d, sx = to_float(c.get("def_max")), to_float(c.get("stress_max"))
    allow, sy, obj, lim = ctx["allow"], ctx["sy"], ctx["short"], ctx["lim"]
    tier = tier_of(sx, ctx)
    basis = (c.get("basis") or "").strip() if tier in ("exceeds", "beyond_yield", "suspect") else ""
    if basis not in ("", "asis", "singularity", "refine"):
        basis = ""
    texts = {"deformation_caption": "", "stress_caption": "", "observations": [], "conclusion": ""}
    res = {"texts": texts, "tier": tier, "exceeds": None, "needs_basis": tier == "suspect" and basis == "", "basis": basis,
           "cell_def": "", "cell_stress": "", "def_fail": False, "util": None, "adequate": None}
    mw._MM_MODE["mode"] = SETTINGS["mm_format"]
    loc_def, loc_str = _clean(c.get("loc_def")), _clean(c.get("loc_stress"))
    b1 = None
    if d is not None:
        texts["deformation_caption"] = f"Maximum Total Deformation \u2013 {mw.fmt_mm(d)} mm"
        res["cell_def"] = f"{mw.fmt_mm(d)} mm"
        b1 = f"Total deformation in the {obj} is {mw.fmt_mm(d)} mm."
        if lim is not None:
            res["def_fail"] = d > lim
            if res["def_fail"]:
                res["cell_def"] = "Exceeding Limit"
            b1 = b1[:-1] + f", which is {'exceeding' if d > lim else 'within'} the allowable deformation limit of {lim:g} mm."
    first = [b1] if b1 else []
    if b1 and loc_def:
        first.append(f"The maximum deformation occurs at {loc_def}.")
    if sx is None:                                         # no stress number: only the deformation sentence can be written
        texts["observations"] = first
        return res
    fs, fm = mw.fmt_mpa(sx), mw.fmt_mpa
    if allow is None:                                      # no allowable stress: neutral wording, the verdict is left to the engineer
        texts["stress_caption"] = f"Maximum Von-Mises Stress \u2013 {fs} MPa"
        res["cell_stress"] = f"{fs} MPa"
        bl = first + [f"Von-Mises stress in the {obj} is {fs} MPa."]
        if loc_str:
            bl.append(f"The maximum von-Mises stress occurs at {loc_str}.")
        bl.append("[Conclusion - please confirm after entering the yield stress.]")
        texts["observations"] = bl
        return res
    cc = {"max_stress_mpa": sx, "max_deformation_mm": d if d is not None else 0.0}
    mw.derive_case_texts({"object_short": obj, "allowable_mpa": allow}, cc)
    texts["stress_caption"] = cc["stress_caption"]
    res["exceeds"] = tier != "pass"
    _b1, b2, b3 = cc["observations"]                       # the deformation sentence (b1) was written above; it is dropped if d is missing
    conclusion = cc.get("conclusion") or ""
    mid, adequate = [], tier == "pass"
    res["cell_stress"] = f"{fs} MPa" if tier == "pass" else "Exceeding Limit"

    if tier == "pass":
        pct = sx / allow * 100
        res["util"] = pct
        if ctx["util"]:
            b2 = b2[:-1] + (f" (utilisation {pct:.1f} %)." if pct < 10 else f" (utilisation {pct:.0f} %).")
        if res["def_fail"]:
            b3 = f"Therefore, the {obj} is structurally adequate in strength, but the deformation exceeds the allowable limit."
            conclusion, adequate = "Need to take corrective actions.", False
    elif basis == "singularity":
        loc = _clean(c.get("sing_loc")) or analyzer.PLACEHOLDER
        away = to_float(c.get("stress_away"))
        b2 = (f"The peak von-Mises stress of {fs} MPa occurs locally at {loc} and is a stress singularity of the idealisation "
              f"(mesh-dependent), not a representative stress.")
        if away is None:
            mid.append("Away from this region the von-Mises stress is [value - please confirm] MPa.")
            b3, conclusion, adequate = "[Conclusion - please confirm after entering the stress away from the peak.]", "", False
            res["cell_stress"] = "[value - please confirm]"
            texts["stress_caption"] = "The peak stress is a local singularity; the stress away from it is [value - please confirm] MPa."
        elif away <= allow:
            mid.append(f"Away from this region the von-Mises stress is {fm(away)} MPa, which is within the materials allowable stress limit of {allow:g} MPa.")
            b3 = f"Therefore, the {obj} is structurally adequate to withstand the applied load under this load case, based on the stress away from the singularity."
            conclusion, adequate = "", not res["def_fail"]
            res["cell_stress"] = f"{fm(away)} MPa (away from local peak)"
            texts["stress_caption"] = f"The peak stress is a local singularity; the stress away from it is within the materials allowable limit of {allow:g} MPa."
        else:
            mid.append(f"Away from this region the von-Mises stress is {fm(away)} MPa, which exceeds the materials allowable stress limit of {allow:g} MPa.")
            b3 = f"Therefore, the {obj} is structurally inadequate to withstand the applied load under this load case."
            conclusion, adequate = "Need to take corrective actions.", False
            res["cell_stress"] = "Exceeding Limit"
            texts["stress_caption"] = f"The peak stress is a local singularity; the stress away from it exceeds the materials allowable limit of {allow:g} MPa."
        loc_str = ""                                       # the location sentence above already says where
    elif basis == "refine":
        loc = _clean(c.get("sing_loc")) or analyzer.PLACEHOLDER
        b2 = (f"The peak von-Mises stress of {fs} MPa ({sx / allow:.1f} times the allowable limit of {allow:g} MPa) occurs at {loc} "
              f"and is mesh-dependent.")
        b3 = "A conclusion on the structural adequacy cannot be drawn until the model is refined at this location."
        conclusion, adequate = "Model refinement required.", None
        res["cell_stress"] = "Under review"
        texts["stress_caption"] = "The peak stress is mesh-dependent: the model must be refined before a conclusion is drawn."
        loc_str = ""
    else:                                                  # "" or "asis": the template wording
        adequate = False
        if tier in ("beyond_yield", "suspect"):
            mid.append(f"The peak stress ({fs} MPa) is above the yield strength of {sy:g} MPa; at that location the linear-elastic result is indicative only.")

    bullets = list(first)
    bullets.append(b2)
    if loc_str:
        bullets.append(f"The maximum von-Mises stress occurs at {loc_str}.")
    bullets += mid
    ra, rs = to_float(c.get("react_applied")), to_float(c.get("react_sum"))
    if ra and rs is not None:
        bullets.append(f"Reaction force check: the sum of the reactions is {rs:,.0f} N against an applied load of {ra:,.0f} N "
                       f"(difference {abs(rs - ra) / abs(ra) * 100:.1f} %).")
    bullets.append(b3)
    texts["observations"] = bullets
    texts["conclusion"] = conclusion
    res["adequate"] = adequate
    return res


def summary_rows(cases, comps, ctx):
    hdr = ["Load Case", "Max. Deformation", "Max. Von-Mises Stress", "Allowable Stress"]
    if ctx["lim"] is not None:
        hdr.append("Allowable Deformation")
    allow_txt = f"{ctx['allow']:g} MPa" if ctx["allow"] is not None else "[value - please confirm]"
    rows = []
    for c, cp in zip(cases, comps):
        nm = (c.get("short_name") or c.get("name") or "").strip() or f"Load Case {c.get('n')}"
        row = [f"Case \u2013 {nm}", cp["cell_def"] or "\u2013", cp["cell_stress"] or "\u2013", allow_txt]
        if ctx["lim"] is not None:
            row.append(f"{ctx['lim']:g} mm")
        rows.append(row)
    return {"header": hdr, "rows": rows}


def mesh_line(m: dict):
    m = m or {}
    def num(v, fmt):
        x = to_float(v)
        return fmt(x) if x is not None else str(v).strip()
    parts = []
    if str(m.get("type") or "").strip():
        parts.append(str(m["type"]).strip())
    if str(m.get("elements") or "").strip():
        parts.append("Elements: " + num(m["elements"], lambda x: f"{int(round(x)):,}"))
    if str(m.get("nodes") or "").strip():
        parts.append("Nodes: " + num(m["nodes"], lambda x: f"{int(round(x)):,}"))
    if str(m.get("size") or "").strip():
        parts.append("Element size: " + num(m["size"], lambda x: f"{x:g}") + " mm")
    if str(m.get("skew_avg") or "").strip():
        parts.append("Avg. skewness " + num(m["skew_avg"], lambda x: f"{x:.2f}"))
    if str(m.get("skew_max") or "").strip():
        parts.append("Max. skewness " + num(m["skew_max"], lambda x: f"{x:.2f}"))
    if str(m.get("oq_min") or "").strip():
        parts.append("Min. orthogonal quality " + num(m["oq_min"], lambda x: f"{x:.2f}"))
    return "  |  ".join(parts) or None


# ---------- layout planning: how big will the legend text be on the slide? ----------
_SIZE_CACHE: dict = {}


def img_size(path):
    key = (path, os.path.getmtime(path))
    if key not in _SIZE_CACHE:
        _SIZE_CACHE[key] = mw.image_size(path)
    return _SIZE_CACHE[key]


def usable(info):
    """path of a picture that exists and can be read, else None - a missing or broken picture never stops a report"""
    try:
        path = info.get("path") if info else None
        if not path or not Path(path).exists():
            return None
        img_size(path)
        return path
    except Exception:                                                     # noqa: BLE001
        return None


def choose_mode(opts, min_pt, order):
    """first layout (template look first) whose legend text is big enough; else the one with the biggest text"""
    for m in order:
        if m in opts and opts[m]["pt"] >= min_pt - 1e-9:
            return m
    return max((m for m in order if m in opts), key=lambda m: (round(opts[m]["pt"], 1), -order.index(m)))


def plan_layouts(p: dict, ctx: dict, infos: list) -> dict:
    """How the pictures can be laid out and how readable the screenshot's own text (legend) is in each layout."""
    by_id = {i["id"]: i for i in infos}
    min_pt = float(SETTINGS["min_legend_pt"])
    choice = {**{"results": "auto", "geometry": "auto", "bc": "auto", "extras": "auto"}, **(p.get("layouts") or {})}
    cases = p.get("cases") or []
    pxs = [c.get("legend_px") for c in cases if c.get("legend_px")]
    job_px = statistics.median(pxs) if pxs else 9.6

    def path_of(pid):
        return usable(by_id.get(pid))

    plan = {"min_pt": min_pt, "cases": {}}
    # --- results (deformation + stress)
    res_opts = {m: {"pt": 99.0, "slides": 2 if m == "separate" else 1} for m in ("side", "stack", "separate")}
    for c in cases:
        pd, ps = path_of(c.get("def_id")), path_of(c.get("stress_id"))
        if not (pd and ps):
            continue
        sz = [img_size(pd), img_size(ps)]
        px = (c.get("legend_px") or job_px)
        comp = compose_case(c, ctx)
        caps = [comp["texts"]["deformation_caption"] or None, comp["texts"]["stress_caption"] or None]
        att = [e for e in (c.get("extras") or []) if e.get("attach")][: mw.MAX_INSETS]
        per = {}
        for m in res_opts:
            sc = mw.panel_scales("results", m, [s[:2] for s in sz], caps)
            pt = round(min(s * z[2] * px / 0.72 for s, z in zip(sc, sz)), 1)
            if att and m in ("side", "stack"):                # views on the same slide take a column away: show the honest legend size
                panels = [dict(image=pd, caption=caps[0], csize=16), dict(image=ps, caption=caps[1], csize=14)]
                sides = [{"side": (e.get("pos") if e.get("pos") in ("left", "right")
                                   else (e.get("match") or {}).get("side") or "right")} for e in att]
                sd = mw.panel_scales_detail(panels, sides, m)
                pt = min(pt, round(min(s * z[2] * px / 0.72 for s, z in zip(sd, sz)), 1))
            per[m] = {"pt": pt, "slides": res_opts[m]["slides"]}
            res_opts[m]["pt"] = min(res_opts[m]["pt"], per[m]["pt"])
        plan["cases"][str(c["n"])] = {"results": per}
    # --- views (geometry + mesh)
    views_opts = {m: {"pt": 99.0, "slides": 0} for m in ("side", "stack", "separate")}
    for key in ("geometry", "meshes"):
        pics = [path_of(v["id"]) for v in (p.get(key) or [])]
        pics = [x for x in pics if x]
        if len(pics) < 2:
            continue
        sz = [img_size(x) for x in pics]
        for m in views_opts:
            sc = mw.panel_scales("views", m, [s[:2] for s in sz])
            views_opts[m]["pt"] = min(views_opts[m]["pt"], round(min(s * z[2] * job_px / 0.72 for s, z in zip(sc, sz)), 1))
            views_opts[m]["slides"] += (len(pics) if m == "separate" else (len(pics) + 1) // 2)
    # --- boundary-condition picture
    bc_opts = {m: {"pt": 99.0, "slides": 1} for m in ("side", "stack")}
    bc_overflow = {m: False for m in bc_opts}
    for c in cases:
        pb = path_of(c.get("bc_id"))
        if not pb:
            continue
        w, h, k = img_size(pb)
        px = (c.get("legend_px") or job_px)
        items = [t for t in (c.get("bc_items") or []) if str(t).strip()]
        notes = [t for t in (c.get("notes") or []) if str(t).strip()]
        per = {}
        for m in bc_opts:
            inf = mw.bc_layout_info((w, h), items, notes, m)
            per[m] = {"pt": round(inf["scale"] * k * px / 0.72, 1), "slides": 1, "overflow": inf["overflow"]}
            bc_opts[m]["pt"] = min(bc_opts[m]["pt"], per[m]["pt"])
            bc_overflow[m] = bc_overflow[m] or inf["overflow"]
        plan["cases"].setdefault(str(c["n"]), {})["bc"] = per
    # --- additional views
    ext_opts = {m: {"pt": 99.0, "slides": 0} for m in ("side", "stack", "separate")}
    for c in cases:
        for kind in ("bc", "deformation", "stress", "other"):
            ex = [e for e in (c.get("extras") or []) if e.get("kind") == kind]
            pics = [path_of(e["id"]) for e in ex]
            pics = [x for x in pics if x]
            if not pics:
                continue
            sz = [img_size(x) for x in pics]
            for m in ext_opts:
                sc = mw.panel_scales("views", m, [s[:2] for s in sz])
                ext_opts[m]["pt"] = min(ext_opts[m]["pt"], round(min(s * z[2] * job_px / 0.72 for s, z in zip(sc, sz)), 1))
                ext_opts[m]["slides"] += (len(pics) if m == "separate" else (len(pics) + 1) // 2)

    def finish(name, opts, order, thr):
        valid = {m: o for m, o in opts.items() if o["pt"] < 90}
        if not valid:
            return None
        auto = choose_mode(valid, thr, order)
        want = choice.get(name, "auto")
        return {"auto": auto, "choice": want, "effective": want if want in valid else auto, "options": valid, "thr": thr,
                "best_pt": max(o["pt"] for o in valid.values())}

    # A plain drawing (geometry, mesh) has no legend to read, so it may be a little smaller than a plot with a legend.
    soft = round(0.7 * min_pt, 2)
    has_legend_extras = any(e.get("kind") in ("deformation", "stress", "other") for c in cases for e in (c.get("extras") or []))
    res_order = ("side", "stack", "separate")
    if any(e.get("attach") for c in cases for e in (c.get("extras") or [])):
        res_order = ("side", "stack")          # attached views live ON the combined results slide: auto may not split it
    plan["results"] = finish("results", res_opts, res_order, min_pt)
    plan["geometry"] = finish("geometry", views_opts, ("side", "stack", "separate"), soft)
    plan["bc"] = finish("bc", bc_opts, ("side", "stack"), min_pt)
    plan["extras"] = finish("extras", ext_opts, ("side", "stack", "separate"), min_pt if has_legend_extras else soft)
    plan["bc_overflow"] = bc_overflow
    for c in cases:                                         # per-case effective layouts (a per-case override wins)
        e = plan["cases"].get(str(c["n"]), {})
        e["effective_results"] = (c.get("layout") if c.get("layout") in ("side", "stack", "separate") else
                                  (plan["results"]["effective"] if plan["results"] else "side"))
        e["effective_bc"] = (c.get("bc_layout") if c.get("bc_layout") in ("side", "stack") else
                             (plan["bc"]["effective"] if plan["bc"] else "side"))
        plan["cases"][str(c["n"])] = e
    return plan


def layout_checks(plan, ctx):
    out = []
    names = {"results": "result plots", "geometry": "geometry / mesh views", "bc": "setup picture", "extras": "additional views"}
    for key in ("results", "geometry", "bc", "extras"):
        pl = plan.get(key)
        if not pl:
            continue
        mp = pl["thr"]
        eff = pl["effective"]
        pt = pl["options"][eff]["pt"]
        if pt < mp:
            if pl["best_pt"] < mp:
                out.append(("warn", f"The text inside the {names[key]} (legend, axes) will be only about {pt:g} pt on the slide, "
                                    f"and no layout does better than {pl['best_pt']:g} pt. Export the pictures larger or cropped closer "
                                    f"to the model, or add zoomed views as additional views."))
            else:
                better = choose_mode(pl["options"], mp, ("side", "stack", "separate"))
                out.append(("warn", f"The text inside the {names[key]} will be only about {pt:g} pt with the layout you chose; "
                                    f"'{better}' gives {pl['options'][better]['pt']:g} pt."))
        elif key != "extras" and pl["auto"] != "side" and pl["choice"] == "auto":
            out.append(("info", f"The {names[key]} are large / wide, so they are placed '{eff}' to keep the text readable "
                                f"(about {pt:g} pt; side by side would give {pl['options']['side']['pt']:g} pt)."))
    for m, flag in plan.get("bc_overflow", {}).items():
        if flag and plan.get("bc") and plan["bc"]["effective"] == m:
            out.append(("warn", "The list of loads and supports is long - it may run off the setup slide. Shorten the sentences or move some to Notes."))
    return out


def case_checks(c, cp, ctx):
    n = c.get("n")
    out = []
    d, sx = to_float(c.get("def_max")), to_float(c.get("stress_max"))
    su, du = (c.get("stress_unit") or "MPa"), (c.get("def_unit") or "mm")
    if su.lower() != "mpa":
        out.append(("warn", f"Case {n}: the stress picture says its unit is \"{su}\", not MPa - check the number."))
    if du.lower() != "mm":
        out.append(("warn", f"Case {n}: the deformation picture says its unit is \"{du}\", not mm - check the number."))
    g = c.get("gravity")
    if g and to_float(g.get("value")) is not None:
        v, u = to_float(g["value"]), str(g.get("unit") or "")
        if "mm" in u and abs(v - 9806.65) > 0.01 * 9806.65:
            out.append(("warn", f"Case {n}: gravity is {v:g} {u} - in a mm-tonne-second model it must be 9806.65 mm/s\u00b2."))
        elif u and "mm" not in u and "m/s" in u and abs(v - 9.80665) > 0.01 * 9.80665:
            out.append(("warn", f"Case {n}: gravity is {v:g} {u} - it should be 9.80665 m/s\u00b2."))
    if sx is None:
        return out
    allow, sy = ctx["allow"], ctx["sy"]
    tier = cp["tier"]
    legend = [v for v in (c.get("legend") or []) if isinstance(v, (int, float))]
    cap = legend_cap(legend)
    if allow is None:
        return out
    if tier == "pass":
        out.append(("ok", f"Case {n}: maximum stress {sx:g} MPa is within the allowable {allow:g} MPa (utilisation {sx / allow * 100:.0f} %)."))
    elif tier == "exceeds":
        out.append(("bad", f"Case {n}: maximum stress {sx:g} MPa is {sx / allow:.1f}x the allowable {allow:g} MPa (below the yield strength). "
                           f"The report will say it exceeds the limit and needs corrective action."))
    elif tier == "beyond_yield":
        out.append(("bad", f"Case {n}: maximum stress {sx:g} MPa is above the yield strength ({sy:g} MPa, {sx / sy:.1f}x). The linear-elastic "
                           f"result is only indicative there; a note to that effect is added to the observations."))
    elif tier == "suspect":
        if cp["basis"]:
            out.append(("info", f"Case {n}: peak stress {sx:g} MPa ({sx / sy:.0f}x yield) - reported as: {BASIS_TEXT[cp['basis']]}."))
        else:
            out.append(("bad", f"Case {n}: maximum stress {sx:g} MPa is {sx / sy:.0f}x the YIELD strength - far beyond what a real structure "
                               f"carries. This is almost always a stress singularity (point support, sharp corner, contact edge) or a load / unit "
                               f"error. Decide how to report it (box below the results of this case)."))
        if cp["basis"] == "singularity" and to_float(c.get("stress_away")) is not None and to_float(c.get("stress_away")) > sy:
            out.append(("warn", f"Case {n}: even the stress 'away from the peak' ({c['stress_away']} MPa) is above the yield strength - is it really away from the singularity?"))
    if tier != "pass":
        red = c.get("red_px")
        if red is not None and red < 5 and not cp["basis"]:
            out.append(("warn", f"Case {n}: the stress is over the limit, but I cannot see any red zone in the stress picture (the hot spot may be "
                                f"tiny or hidden). A zoomed picture of the hot spot would support the statement."))
        if cap is None:
            out.append(("warn", f"Case {n}: the stress legend is not stretched to the allowable stress, so its colours do not show where the limit "
                                f"is exceeded. Consider setting the ANSYS legend so the top band starts at {allow:g} MPa."))
        elif abs(cap - allow) > max(0.02 * allow, 0.5):
            out.append(("warn", f"Case {n}: the top colour band of the stress legend starts at {cap:g} MPa, but the allowable stress is {allow:g} MPa. "
                                f"Red in the picture does not then mean \"over the limit\" - check the yield stress / FOS."))
        else:
            out.append(("ok", f"Case {n}: the legend's top band starts at {cap:g} MPa = the allowable stress, so red = over the limit."))
    if cp["def_fail"]:
        out.append(("bad", f"Case {n}: deformation {d:g} mm exceeds the allowable deformation of {ctx['lim']:g} mm."))
    for e in (c.get("extras") or []):                      # a section / detail view cannot show more than the model maximum
        em = to_float(e.get("max"))
        main = sx if e.get("kind") == "stress" else (d if e.get("kind") == "deformation" else None)
        if em is not None and main is not None and em > main * 1.001:
            out.append(("warn", f"Case {n}: the additional {e['kind']} view '{e.get('heading', '')}' shows Max {em:g} - larger than the main plot's "
                                f"Max {main:g}. A section cannot exceed the model maximum: check both readings."))
    ra, rs = to_float(c.get("react_applied")), to_float(c.get("react_sum"))
    if ra and rs is not None:
        diff = abs(rs - ra) / abs(ra) * 100
        out.append(("warn" if diff > 2.0 else "ok", f"Case {n}: reactions {rs:,.0f} N vs applied {ra:,.0f} N - difference {diff:.1f} %"
                                                  + (" (more than 2 %: the load path or a constraint needs a look)." if diff > 2.0 else ".")))
    return out


def legend_cap(vals):
    """ANSYS 'custom' legend: the top colour band is stretched up to Max, so its lower bound (2nd value) is far from
    the regular step.  That bound is normally set to the allowable stress.  Returns None for an automatic legend."""
    if len(vals) < 5:
        return None
    step, gap = vals[2] - vals[3], vals[0] - vals[1]
    return vals[1] if step > 0 and gap > 2 * step else None


def material_checks(p, ctx):
    out = []
    m = p.get("material") or {}
    E, nu, rho = to_float(m.get("E")), to_float(m.get("nu")), to_float(m.get("rho"))
    if ctx["fos"] < 1.0:
        out.append(("warn", f"The factor of safety is {ctx['fos']:g} - below 1 the allowable stress is higher than the yield strength."))
    if rho is not None:
        if rho >= 100:
            out.append(("warn", f"Density {rho:g} looks like kg/m\u00b3 (steel 7850). In tonne/mm\u00b3 steel is 7.85E-9 - a unit mix-up here is a classic blunder."))
        elif 1e-7 <= rho <= 1e-4:
            out.append(("warn", f"Density {rho:g} looks like kg/mm\u00b3 (steel 7.85E-6). In tonne/mm\u00b3 steel is 7.85E-9."))
        elif not (5e-10 <= rho <= 5e-8):
            out.append(("warn", f"Density {rho:g} is outside the usual range for tonne/mm\u00b3 (steel 7.85E-9, aluminium 2.7E-9) - check the units."))
    if E is not None:
        if E < 1000:
            out.append(("warn", f"Young's modulus {E:g} looks like GPa - the table is in MPa (steel about 200000)."))
        elif E > 2e6:
            out.append(("warn", f"Young's modulus {E:g} is very high for MPa - is it in Pa or psi?"))
    if nu is not None and not (0.0 <= nu < 0.5):
        out.append(("warn", f"Poisson's ratio {nu:g} must lie between 0 and 0.5."))
    return out


def mesh_checks(ms):
    out = []
    lim = SETTINGS["mesh_limits"]
    ms = ms or {}
    smax, savg, oq = to_float(ms.get("skew_max")), to_float(ms.get("skew_avg")), to_float(ms.get("oq_min"))
    if smax is not None:
        if smax >= lim["skew_max_warn"]:
            out.append(("warn", f"Maximum skewness {smax:g} is in ANSYS's 'bad (sliver)' range (>= {lim['skew_max_warn']:g}). Check where these elements are, "
                                f"especially if they sit in the high-stress region."))
        elif smax >= lim["skew_max_info"]:
            out.append(("info", f"Maximum skewness {smax:g} is in ANSYS's 'poor' range ({lim['skew_max_info']:g}-{lim['skew_max_warn']:g}) - acceptable only away from the hot spots."))
    if savg is not None and savg > lim["skew_avg_info"]:
        out.append(("info", f"Average skewness {savg:g} is high (a good mesh averages below about {lim['skew_avg_info']:g})."))
    if oq is not None and oq < lim["oq_min_warn"]:
        out.append(("warn", f"Minimum orthogonal quality {oq:g} is below {lim['oq_min_warn']:g} (ANSYS guidance: keep it above 0.1)."))
    return out


def derive(p: dict) -> dict:
    ctx = make_ctx(p)
    cases = p.get("cases", [])
    out, checks, caps = {}, [], []
    comps = []
    with BUILD_LOCK:
        for c in cases:
            cp = compose_case(c, ctx)
            comps.append(cp)
            out[str(c.get("n"))] = {"texts": cp["texts"], "tier": cp["tier"], "exceeds": cp["exceeds"], "needs_basis": cp["needs_basis"],
                                    "util": cp["util"], "adequate": cp["adequate"]}
            checks += case_checks(c, cp, ctx)
            legend = [v for v in (c.get("legend") or []) if isinstance(v, (int, float))]
            cap = legend_cap(legend)
            if cap is not None:
                caps.append(cap)
    suggest = None
    if caps and max(caps) - min(caps) <= 0.02 * max(caps):
        cap = sorted(caps)[len(caps) // 2]
        suggest = {"allowable": cap, "yield": math.ceil(cap * ctx["fos"] - 1e-9)}
    if ctx["allow"] is None:
        checks.insert(0, ("info", "Enter the yield stress in the Material table - the pass / fail wording is written from it."))
    checks += material_checks(p, ctx)
    checks += mesh_checks(p.get("mesh_stats"))
    layout = None
    if p.get("sid"):
        try:
            layout = plan_layouts(p, ctx, load_infos(p["sid"]))
            checks += layout_checks(layout, ctx)
        except Exception as e:                                            # noqa: BLE001 - a layout hint must never break the page
            print("layout planning failed:", e)
    has_ev = (mesh_line(p.get("mesh_stats")) or p.get("assumptions") or any(c.get("loc_stress") or c.get("loc_def") for c in cases))
    if cases and not has_ev:
        checks.append(("info", "Reviewers usually ask for the mesh statistics, the location of the maximum stress and the modelling assumptions. "
                               "All optional - see \"Engineering evidence\"."))
    return {"cases": out, "checks": [{"level": a, "text": b} for a, b in checks], "suggest": suggest, "layout": layout}


@app.post("/api/derive")
def api_derive():
    return jsonify(derive(request.get_json(force=True)))


# ───────────────────────────────────────────── step 3: make the PowerPoint ─────────────────────────────────────────────
_XML_BAD = re.compile("[^\u0009\u000a\u000d\u0020-\ud7ff\ue000-\ufffd\U00010000-\U0010ffff]")
_PATH_KEYS = {"image", "bc_image", "deformation_image", "stress_image", "output"}
PLACEHOLDER_VALUE = "[value - please confirm]"


def xml_safe(o, key=None):
    """control characters (e.g. pasted from a PDF) cannot be stored in a PowerPoint file - they are removed"""
    if isinstance(o, str):
        return o if key in _PATH_KEYS else _XML_BAD.sub("", o)
    if isinstance(o, list):
        return [xml_safe(x, key) for x in o]
    if isinstance(o, dict):
        return {k: xml_safe(v, k) for k, v in o.items()}
    return o


def build_cfg(p: dict, infos: list, out_path: Path) -> dict:
    """Everything the report engine needs.  NOTHING here raises because something is missing: a picture that does not
    exist is left out (or replaced by an empty frame - the 'missing' option), a missing number leaves out its sentence,
    a missing yield stress gives neutral wording and a bracketed placeholder.  The engine lists what it did in cfg['_notes']."""
    by_id = {i["id"]: i for i in infos}

    def pic(pid):
        return usable(by_id.get(pid))

    ctx = make_ctx(p)
    cover = p.get("cover") or {}
    mat = p.get("material") or {}
    n_cols = len(SETTINGS["material_header"])
    rows = []
    for r in (mat.get("rows") or []):
        r = list(r) if isinstance(r, (list, tuple)) else []
        rows.append([str("" if x is None else x).strip() for x in (r + [""] * n_cols)[:n_cols]])
    if not rows:
        m0 = SETTINGS["material"]
        rows = [[str(m0.get(k, "")) for k in ("material", "item", "E", "nu", "rho")] + ["", ""]]
    for r in rows:                                          # a blank cell would be silent - a placeholder is visible
        for k, v in enumerate(r):
            if not v:
                r[k] = PLACEHOLDER_VALUE

    def views(lst):
        """flat list first; then every view that was attached to another one is nested inside its parent, so
        build_view_slides draws it on the SAME slide, next to it (council item A4)."""
        flat = []
        for k, v in enumerate(lst or [], 1):
            path = pic(v.get("id"))
            if not path:
                continue
            d = {"heading": (v.get("heading") or "").strip() or f"View {k}", "image": path, "id": v.get("id")}
            if (v.get("caption") or "").strip():
                d["caption"] = v["caption"].strip()
            flat.append(d)
        parent_of = {v.get("id"): v.get("parent") for v in (lst or [])}
        attach_of = {v.get("id"): bool(v.get("attach")) for v in (lst or [])}

        def root(pid, seen=()):
            while attach_of.get(pid) and parent_of.get(pid) is not None and pid not in seen:
                seen = seen + (pid,)
                pid = parent_of[pid]
            return pid

        top = [d for d in flat if not (attach_of.get(d["id"]) and root(d["id"]) != d["id"])]
        top_ids = [d["id"] for d in top]
        for d in flat:
            if d["id"] in top_ids:
                continue
            t = next((x for x in top if x["id"] == root(d["id"])), None)
            if t is None:
                top.append(d)
                top_ids.append(d["id"])
            elif len(t.setdefault("insets", [])) < mw.MAX_INSETS:
                t["insets"].append({k2: v2 for k2, v2 in d.items() if k2 != "id"})
            else:
                top.append(d)
        for d in top:
            d.pop("id", None)
        return top

    def view_block(vs):
        if not vs:
            return {}
        return {"image": vs[0]["image"]} if len(vs) == 1 else {"images": vs}

    geometry = view_block(views(p.get("geometry")))
    mesh = view_block(views(p.get("meshes")))
    ml = mesh_line(p.get("mesh_stats"))
    if ml:
        mesh["stats"] = ml

    plan = plan_layouts(p, ctx, infos)
    cases, comps, src_cases = [], [], (p.get("cases") or [])
    for c in src_cases:
        n = c.get("n")
        d, sx = to_float(c.get("def_max")), to_float(c.get("stress_max"))
        name = (c.get("name") or "").strip() or f"Load Case {n}"
        cp = compose_case(c, ctx)
        comps.append(cp)
        case = {
            "name": name, "subtitle": (c.get("subtitle") or "").strip(),
            "short_name": (c.get("short_name") or "").strip() or name,
            "bc_items": [t.strip() for t in (c.get("bc_items") or []) if str(t).strip()],
            "notes": [t.strip() for t in (c.get("notes") or []) if str(t).strip()],
            "layout": plan["cases"][str(n)]["effective_results"], "bc_layout": plan["cases"][str(n)]["effective_bc"],
            "deformation_caption": cp["texts"]["deformation_caption"], "stress_caption": cp["texts"]["stress_caption"],
            "observations": cp["texts"]["observations"], "conclusion": cp["texts"]["conclusion"] or None,
        }
        for key, pid in (("bc_image", c.get("bc_id")), ("deformation_image", c.get("def_id")), ("stress_image", c.get("stress_id"))):
            path = pic(pid)
            if path:
                case[key] = path
        if d is not None:
            case["max_deformation_mm"] = d
        if sx is not None:
            case["max_stress_mpa"] = sx
        extras, insets = [], []
        slot_of = {c.get("def_id"): 0, c.get("stress_id"): 1}
        per_slot = {0: 0, 1: 0}
        for e in c.get("extras") or []:
            path = pic(e.get("id"))
            if e.get("kind") not in ("bc", "deformation", "stress", "other") or not path:
                continue
            ex = {"kind": e["kind"], "heading": (e.get("heading") or "").strip() or "Additional view", "image": path}
            if (e.get("caption") or "").strip():
                ex["caption"] = e["caption"].strip()
            slot = slot_of.get(e.get("parent"))
            m = e.get("match") or {}
            pos = e.get("pos") or "auto"
            side = pos if pos in ("left", "right") else (m.get("side") or "right")
            att = e.get("attach")
            att = True if att is None and e.get("view_kind") in ("section", "detail") else bool(att)
            if att and slot is not None and per_slot[slot] < mw.MAX_INSETS:
                per_slot[slot] += 1
                ins = {"image": path, "heading": ex["heading"], "parent_slot": slot, "side": side}
                if ex.get("caption"):
                    ins["caption"] = ex["caption"]
                if m.get("box") and pos != "nomark":
                    ins["region"] = m["box"]
                insets.append(ins)
            else:
                extras.append(ex)
        if extras:
            case["extras"] = extras
        if insets:
            case["insets"] = insets
        t = c.get("texts") or {}                                          # only what the engineer edited by hand wins
        if "deformation_caption" in t:
            case["deformation_caption"] = str(t["deformation_caption"]).strip()
        if "stress_caption" in t:
            case["stress_caption"] = str(t["stress_caption"]).strip()
        if "observations" in t:
            obs = [str(x).strip() for x in t["observations"] if str(x).strip()]
            if obs:
                case["observations"] = obs
        if "conclusion" in t:
            case["conclusion"] = str(t["conclusion"]).strip() or None
        cases.append(case)

    assumptions = [str(a).strip() for a in (p.get("assumptions") or []) if str(a).strip()]
    cfg = {
        "output": str(out_path), "author": SETTINGS["author"], "autocrop": True, "mm_format": SETTINGS["mm_format"],
        "missing": "frame" if p.get("missing") == "frame" else "skip",
        "object_short": ctx["short"], "allowable_mpa": ctx["allow"],
        "cover": {"label": SETTINGS["cover_label"], "title": (cover.get("title") or "").strip(),
                  "report_no": (cover.get("report_no") or "").strip(), "date": (cover.get("date") or "").strip(),
                  "client": (cover.get("client") or "").strip(), "revision": (cover.get("revision") or "").strip(),
                  "prepared_by": (cover.get("prepared_by") or "").strip(), "checked_by": (cover.get("checked_by") or "").strip(),
                  "ref": (cover.get("ref") or "").strip()},
        "material": {"header": SETTINGS["material_header"], "rows": rows,
                     "units": (mat.get("units") or SETTINGS["units"]).strip(), "fos": f"FOS = {ctx['fos']:g}"},
        "geometry": geometry, "mesh": mesh, "cases": cases,
        "layouts": {"geometry": (plan["geometry"] or {}).get("effective", "side"), "extras": (plan["extras"] or {}).get("effective", "side"),
                    "results": (plan["results"] or {}).get("effective", "side"), "bc": (plan["bc"] or {}).get("effective", "side")},
        "summary": summary_rows(src_cases, comps, ctx),
    }
    if assumptions:
        cfg["assumptions"] = assumptions
    return xml_safe(cfg)


# ───────────────────────────────────────────── building, previewing, downloading ─────────────────────────────────────────────
def download_base(p: dict) -> str:
    base = re.sub(r"[^A-Za-z0-9._-]+", "-", str((p.get("cover") or {}).get("report_no", "") or "")).strip("-")
    return f"FEA_Report_{base or 'report'}"


def build_key(p: dict) -> str:
    body = json.dumps({k: v for k, v in p.items() if k != "make_pdf"}, sort_keys=True, ensure_ascii=False, default=str)
    return hashlib.sha1((VERSION + "|" + body).encode("utf-8")).hexdigest()[:12]


KEY_RE = re.compile(r"^[0-9a-f]{12}$")


def build_dir(sid, key) -> Path:
    if not isinstance(key, str) or not KEY_RE.match(key):
        abort(404)
    d = sess_dir(sid) / "builds" / key
    if not (d / "report.pptx").exists():
        abort(404)
    return d


def _strip_optional(p: dict) -> dict:
    """the same job without the optional extras - used only if the full build fails for an unexpected reason"""
    q = json.loads(json.dumps(p, default=str))
    q["assumptions"] = []
    q["mesh_stats"] = {}
    for c in q.get("cases") or []:
        c["extras"] = []
        c["texts"] = {}
    return q


def _shorten(o, limit=400):
    """last resort: every text longer than `limit` characters is cut (the report is still better than an error)"""
    if isinstance(o, str):
        return o if len(o) <= limit else o[:limit].rstrip() + "..."
    if isinstance(o, list):
        return [_shorten(x, limit) for x in o]
    if isinstance(o, dict):
        return {k: _shorten(v, limit) for k, v in o.items()}
    return o


def ensure_build(sid, p: dict) -> dict:
    """Build (or reuse) the report for exactly this state of the page.  Never raises because something is missing."""
    infos = load_infos(sid)
    key = build_key(p)
    d = sess_dir(sid) / "builds" / key
    meta_f = d / "meta.json"
    with BUILD_LOCK:
        if meta_f.exists() and (d / "report.pptx").exists():
            try:
                os.utime(d, None)                                        # "used just now": the oldest builds are removed first
            except OSError:
                pass
            return json.loads(meta_f.read_text(encoding="utf-8"))
        shutil.rmtree(d, ignore_errors=True)
        d.mkdir(parents=True, exist_ok=True)
        out = d / "report.pptx"
        extra_note, cfg, err = None, None, None
        ladder = (lambda: p, lambda: _strip_optional(p), lambda: _shorten(_strip_optional(p)))
        for attempt, make in enumerate(ladder):
            try:
                cfg = build_cfg(make(), infos, out)
                mw.build_report(cfg)
                if attempt == 1:
                    extra_note = "Some optional parts (additional views, assumptions, hand-edited wording) could not be built and were left out."
                elif attempt == 2:
                    extra_note = "Some very long texts had to be shortened, and optional parts were left out, so that the report could be built."
                break
            except Exception as e:                                        # noqa: BLE001
                import traceback
                traceback.print_exc()
                err, cfg = e, None
        if cfg is None:
            raise UserError(f"The report could not be built ({type(err).__name__}: {err}). Please tell the person who maintains this program.")
        deck = pv.get_deck(out)
        notes = list(cfg.get("_notes") or []) + ([extra_note] if extra_note else [])
        meta = {"key": key, "slides": [{"title": deck.title(i)} for i in range(deck.count())], "notes": notes,
                "name": download_base(p), "built_at": time.time(), "pdf_method": None,
                "not_drawn": sorted(set(deck.unsupported))}
        meta_f.write_text(json.dumps(meta), encoding="utf-8")
        builds = sess_dir(sid) / "builds"                                 # keep the last few builds only
        old = sorted((x for x in builds.iterdir() if x.is_dir() and x.name != key), key=lambda x: x.stat().st_mtime, reverse=True)
        for x in old[BUILDS_KEEP - 1:]:
            shutil.rmtree(x, ignore_errors=True)
        return meta


def make_pdf(pptx: Path):
    """PDF through LibreOffice -> (path, message); (None, reason) when LibreOffice is missing or fails"""
    exe = find_soffice()
    if not exe:
        return None, "LibreOffice was not found."
    prof = tempfile.mkdtemp(prefix="fea_lo_")
    try:
        r = subprocess.run([exe, f"-env:UserInstallation={Path(prof).as_uri()}", "--headless", "--convert-to", "pdf",
                            "--outdir", str(pptx.parent), str(pptx)], capture_output=True, text=True, timeout=300)
        pdf = pptx.with_suffix(".pdf")
        if pdf.exists():
            return pdf, ""
        return None, "LibreOffice could not make the PDF: " + ((r.stderr or r.stdout or "unknown error").strip()[-200:])
    except subprocess.TimeoutExpired:
        return None, "The PDF conversion took too long and was stopped."
    except Exception as e:                                                # noqa: BLE001
        return None, f"LibreOffice could not be started ({e})."
    finally:
        shutil.rmtree(prof, ignore_errors=True)


def ensure_pdf(sid, key):
    """-> (method, message).  LibreOffice when it is installed, otherwise the program's own renderer (picture-based PDF)."""
    d = build_dir(sid, key)
    meta_f = d / "meta.json"
    with PDF_LOCK:
        meta = json.loads(meta_f.read_text(encoding="utf-8"))
        pdf = d / "report.pdf"
        if pdf.exists() and meta.get("pdf_method"):
            return meta["pdf_method"], meta.get("pdf_message", "")
        pdf.unlink(missing_ok=True)
        got, why = make_pdf(d / "report.pptx")
        method, msg = "libreoffice", ""
        if not got:
            pv.images_to_pdf(pv.get_deck(d / "report.pptx"), pdf)
            method = "images"
            msg = ("The PDF was made from pictures of the slides (the text in it cannot be selected or searched). "
                   "Install LibreOffice for a PDF with real text." if "not found" in why else
                   f"{why} The PDF was made from pictures of the slides instead (its text cannot be selected).")
        meta["pdf_method"], meta["pdf_message"] = method, msg
        meta_f.write_text(json.dumps(meta), encoding="utf-8")
        return method, msg


def public_meta(meta: dict) -> dict:
    return {"key": meta["key"], "slides": meta["slides"], "notes": meta["notes"], "name": meta["name"],
            "pdf_method": meta.get("pdf_method"), "not_drawn": meta.get("not_drawn", [])}


def copy_to_outputs(src: Path, name: str):
    """a copy in the outputs/ folder of the program (the browser's own download goes to the Downloads folder)"""
    if not SETTINGS.get("keep_copy_in_outputs", True):
        return None
    try:
        OUT.mkdir(parents=True, exist_ok=True)
        dst = OUT / name
        if dst.exists():
            if dst.stat().st_size == src.stat().st_size and hashlib.sha1(dst.read_bytes()).digest() == hashlib.sha1(src.read_bytes()).digest():
                return dst.name                                           # the same report again
            dst = OUT / f"{Path(name).stem}_{datetime.now():%H%M%S}{Path(name).suffix}"
        shutil.copyfile(src, dst)
        return dst.name
    except OSError:
        return None


@app.post("/api/build")
def api_build():
    """Build the report for the current state of the page (cached by content) -> the slide list for the preview."""
    p = request.get_json(force=True)
    return jsonify(ok=True, **public_meta(ensure_build(p.get("sid"), p)))


@app.get("/api/slide/<sid>/<key>/<int:n>")
def api_slide(sid, key, n):
    """One slide of a build as a picture (drawn by the program itself - no LibreOffice, no PDF needed)."""
    d = build_dir(sid, key)
    w = max(160, min(request.args.get("w", 960, type=int), 2600))
    cache = d / (f"s{n}_{w}." + ("jpg" if w < 700 else "png"))
    if not cache.exists():
        with RENDER_LOCK:                                   # one writer per file: Windows cannot replace a file that another request is reading
            if not cache.exists():
                deck = pv.get_deck(d / "report.pptx")
                if n < 0 or n >= deck.count():
                    abort(404)
                im = deck.render(n, w)
                tmp = cache.with_name("tmp_" + uuid.uuid4().hex[:6] + cache.suffix)
                if cache.suffix == ".jpg":
                    im.save(tmp, "JPEG", quality=88, subsampling=0)
                else:
                    im.save(tmp, "PNG")
                tmp.replace(cache)
    return send_file(cache, max_age=3600)


@app.post("/api/pdf")
def api_pdf():
    p = request.get_json(force=True)
    method, msg = ensure_pdf(p.get("sid"), p.get("key"))
    return jsonify(ok=True, method=method, message=msg)


def downloads_folder() -> Path:
    return Path.home() / "Downloads"


def can_open_folder() -> bool:
    """The page may offer 'Open the Downloads folder' only when the program runs on the computer of the user."""
    if not RUNTIME["loopback"]:
        return False
    if os.environ.get("FEA_TEST_OPEN_LOG"):                           # tests: pretend, and write a file instead
        return True
    return os.name == "nt" or sys.platform == "darwin" or bool(shutil.which("xdg-open"))


@app.post("/api/open_downloads")
def api_open_downloads():
    """Opens the Downloads folder in File Explorer / Finder.  No path comes from the page, so nothing can be abused."""
    if not can_open_folder():
        return jsonify(ok=False, message="This only works when the program runs on your own computer."), 200
    log = os.environ.get("FEA_TEST_OPEN_LOG")
    if log:
        Path(log).write_text("opened", encoding="utf-8")
        return jsonify(ok=True)
    try:
        if os.name == "nt":
            try:
                os.startfile("shell:Downloads")                       # the real Downloads folder, also when OneDrive moved it
            except OSError:
                os.startfile(str(downloads_folder()))
        elif sys.platform == "darwin":
            subprocess.Popen(["open", str(downloads_folder())])
        else:
            subprocess.Popen(["xdg-open", str(downloads_folder())])
        return jsonify(ok=True)
    except Exception as e:                                            # noqa: BLE001
        return jsonify(ok=False, message=f"Could not open it ({type(e).__name__}).")


MIME = {"pptx": "application/vnd.openxmlformats-officedocument.presentationml.presentation", "pdf": "application/pdf"}


@app.get("/download/<sid>/<key>/<kind>")
def download(sid, key, kind):
    """A normal browser download (Content-Disposition: attachment): the file lands in the browser's Downloads folder."""
    if kind not in MIME:
        abort(404)
    d = build_dir(sid, key)
    f = d / f"report.{kind}"
    if not f.exists():
        abort(404)
    name = json.loads((d / "meta.json").read_text(encoding="utf-8"))["name"] + "." + kind
    if request.method == "GET":
        copy_to_outputs(f, name)
    resp = send_file(f, as_attachment=True, download_name=name, mimetype=MIME[kind], max_age=0)
    resp.headers["Cache-Control"] = "no-store"
    return resp


@app.post("/api/generate")
def api_generate():
    """Kept for scripts and tests: build + (optionally) PDF, with copies in outputs/.  The web page uses /api/build instead."""
    p = request.get_json(force=True)
    sid = p.get("sid")
    meta = ensure_build(sid, p)
    d = build_dir(sid, meta["key"])
    pptx_name = copy_to_outputs(d / "report.pptx", meta["name"] + ".pptx")
    pdf_name, pdf_msg = None, ""
    if p.get("make_pdf", True):
        method, msg = ensure_pdf(sid, meta["key"])
        pdf_name = copy_to_outputs(d / "report.pdf", meta["name"] + ".pdf")
        pdf_msg = msg
    return jsonify(pptx=pptx_name, pdf=pdf_name, pdf_message=pdf_msg, slides=len(meta["slides"]), folder=str(OUT),
                   key=meta["key"], notes=meta["notes"])


# ───────────────────────────────────────────── auto-save / resume ─────────────────────────────────────────────
@app.post("/api/state")
def api_state_save():
    """The page saves everything the engineer typed - so a refresh or a closed tab does not lose the work."""
    p = request.get_json(force=True)
    d = sess_dir(p.get("sid"))
    (d / "ui_state.json").write_text(json.dumps({"saved_at": time.time(), "state": p.get("state")}), encoding="utf-8")
    return jsonify(ok=True)


@app.get("/api/last")
def api_last():
    WORK.mkdir(parents=True, exist_ok=True)              # the folder may have been deleted while the program runs
    best = None
    for d in WORK.iterdir():
        f = d / "ui_state.json"
        if d.is_dir() and SID_RE.match(d.name) and f.exists() and (best is None or f.stat().st_mtime > best[1]):
            best = (d, f.stat().st_mtime)
    if not best:
        return jsonify(found=False)
    try:
        data = json.loads((best[0] / "ui_state.json").read_text(encoding="utf-8"))
        infos = load_infos(best[0].name)
        st = data["state"] or {}
        return jsonify(found=True, sid=best[0].name, saved_at=data["saved_at"], pictures=len(infos),
                       title=(st.get("cover") or {}).get("report_no") or (st.get("cover") or {}).get("title") or "",
                       images=[public(best[0].name, i) for i in infos], state=st)
    except Exception:                                                     # noqa: BLE001
        return jsonify(found=False)


@app.post("/api/discard")
def api_discard():
    p = request.get_json(force=True)
    f = sess_dir(p.get("sid")) / "ui_state.json"
    f.unlink(missing_ok=True)
    return jsonify(ok=True)


# ───────────────────────────────────────────── start-up ─────────────────────────────────────────────
def port_in_use(host, port):
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(0.5)
        return s.connect_ex((host, port)) == 0


def probe_instance(port: int):
    """What answers on 127.0.0.1:<port>?  The /api/status dict if it is a FEA Report Maker, otherwise None."""
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/api/status", timeout=2.5) as r:
            j = json.loads(r.read().decode("utf-8", "replace"))
        return j if isinstance(j, dict) and "version" in j and ("outputs" in j or "ocr" in j) else None
    except Exception:                                                 # noqa: BLE001
        return None


def same_folder(other_root) -> bool:
    try:
        return bool(other_root) and Path(other_root).resolve() == ROOT.resolve()
    except OSError:
        return False


def main():
    ap = argparse.ArgumentParser(description="FEA Report Maker")
    ap.add_argument("--host", default="127.0.0.1", help="127.0.0.1 = this computer only (default)")
    ap.add_argument("--port", type=int, default=int(SETTINGS["port"]))
    ap.add_argument("--no-browser", action="store_true")
    a = ap.parse_args()
    for stream in (sys.stdout, sys.stderr):                           # never crash on a special character in a folder name
        try:
            stream.reconfigure(errors="replace")
        except Exception:                                             # noqa: BLE001
            pass
    RUNTIME["loopback"] = a.host in ("127.0.0.1", "localhost", "::1")
    url = f"http://127.0.0.1:{a.port}"

    print("=" * 62)
    print(f"  FEA Report Maker {VERSION}")
    print("=" * 62)
    print(f"  Picture reader (Tesseract) : {'ready' if ocr_ready() else 'NOT FOUND - see README.txt, step 1B'}")
    print(f"  PDF export                 : {'ready (LibreOffice)' if find_soffice() else 'ready (made from slide pictures; install LibreOffice for selectable text)'}")
    print(f"  Slide preview              : ready")
    print(f"  Downloads                  : your browser's Downloads folder (a copy is also kept in {OUT})")

    if port_in_use("127.0.0.1", a.port):
        other = probe_instance(a.port)
        if other and other.get("version") == VERSION and same_folder(other.get("root")):
            print(f"\n  The program is already running - opening {url}")
            if not a.no_browser:
                webbrowser.open(url)
            return
        # An OLDER copy (or another folder, or another program) is using this port. Do NOT open that one by mistake.
        who = f"FEA Report Maker {other.get('version')}" if other else "another program"
        where = f" (from the folder {other.get('root')})" if other and other.get("root") else ""
        free = next((q for q in range(a.port + 1, a.port + 40) if not port_in_use("127.0.0.1", q)), None)
        if free is None:
            print(f"\n  Port {a.port} is used by {who}{where} and no other port is free.\n  Close the other black window and start again.")
            sys.exit(1)
        print(f"\n  NOTE: {who}{where} is still running on port {a.port}, in another black window.")
        print(f"        THIS copy is version {VERSION}. It starts on port {free}, so that you do not open the other copy by mistake.")
        print("        You can close the other black window.")
        a.port = free
        url = f"http://127.0.0.1:{a.port}"
    cleanup_old_sessions()
    print(f"\n  Open this address in your browser:  {url}")
    print("  (leave this window open while you work - close it to stop the program)\n")
    if not a.no_browser:
        threading.Timer(1.2, lambda: webbrowser.open(url)).start()
    try:
        app.run(host=a.host, port=a.port, threaded=True, debug=False)
    except OSError as e:
        print(f"\n  Could not start on port {a.port}: {e}\n  Change \"port\" in settings.json and try again.")
        sys.exit(1)


if __name__ == "__main__":
    main()
