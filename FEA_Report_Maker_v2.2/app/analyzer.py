#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
analyzer.py  -  reads ANSYS screenshots (offline OCR) and drafts the report content.

For every picture it works out
    role    : geometry | mesh | bc (setup) | deformation | stress   (the main pictures)
              x_bc | x_deformation | x_stress | x_other             (additional views: section, detail, other result types)
    case    : 1, 2, ... (from the letter ANSYS prints in the title; separate projects that both say "A:" are told apart)
    numbers : Max / Min of the colour legend (read TWICE), unit, boundary-condition legend lines, legend text size
and then drafts the wording (case headline, boundary-condition sentences, ...).

Only free offline tools are used: Tesseract OCR + Pillow + NumPy.  Nothing leaves the computer.
Everything is a *draft*: the web page lets the user check and edit every value.
"""
from __future__ import annotations

import datetime as _dt
import difflib
import hashlib
import os
import re
import shutil
import statistics
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import numpy as np
from PIL import Image

try:
    import pytesseract
except ImportError:                                   # reported to the user by /api/status
    pytesseract = None

NUM = r"[-+]?(?:\d+\.?\d*|\.\d+)(?:[eE][-+]?\d+)?"     # also accepts ANSYS style "1.e+006"
PLACEHOLDER = "[location - please confirm]"
MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "June", "July", "Aug", "Sept", "Oct", "Nov", "Dec"]
LOAD_WORDS = {"pressure", "moment", "force", "gravity", "load", "self", "weight", "temperature", "torque"}

MESH_EDGE_THRESHOLD = 0.075       # fraction of edge pixels. Measured on 2 jobs: plain geometry 0.004-0.021, mesh 0.227-0.411
HEADER_BOX = (0.0, 0.0, 0.46, 0.215)      # where ANSYS prints the title block (fractions of the picture)
LEGEND_BOX = (0.0, 0.12, 0.27, 0.62)      # colour legend of a result plot (the red "Max" callout on the model is outside)
BC_BOX = (0.028, 0.07, 0.36, 0.46)        # load / support legend of the setup picture
HEADER_PX, LEGEND_PX, BC_PX = (0, 0, 520, 170), (0, 66, 270, 340), (15, 38, 355, 253)   # same regions in pixels of a ~1000 x 550 screenshot
ANALYSIS_MAX_SIDE = 3000                  # bigger screenshots are scaled down for analysis only (memory)
CASE_ROLES = {"bc", "deformation", "stress", "x_bc", "x_deformation", "x_stress", "x_other"}
MAIN_SLOTS = (("bc", "bc_id"), ("deformation", "def_id"), ("stress", "stress_id"))


# ═════════════════════════════════════════════ OCR plumbing ═════════════════════════════════════════════
# Nothing may wait for ever: one Tesseract call and one whole picture each have a time limit. A picture that
# runs out of time is treated like "not read" (manual mode for that picture) and all the others carry on.
OCR_CALL_TIMEOUT = 30          # seconds for ONE Tesseract call (a normal call takes 0.3 - 3 s)
OCR_PICTURE_BUDGET = 60        # seconds for ALL the Tesseract calls of ONE picture
_TLS = threading.local()


def _ocr_timeout() -> float:
    """seconds that the next Tesseract call of THIS picture may take (raises when the picture's time is used up)"""
    dl = getattr(_TLS, "deadline", None)
    if dl is None:
        return float(OCR_CALL_TIMEOUT)
    left = dl - time.monotonic()
    if left <= 1:
        raise TimeoutError("reading this picture took too long")
    return float(min(OCR_CALL_TIMEOUT, left))


def find_tesseract():
    """Locate tesseract (PATH or the usual install folders) and tell pytesseract about it."""
    if os.environ.get("FEA_DISABLE_OCR"):                    # tests: behave like a computer without Tesseract
        return None
    if pytesseract is None:
        return None
    cands = [os.environ.get("TESSERACT_CMD"), shutil.which("tesseract"),
             r"C:\Program Files\Tesseract-OCR\tesseract.exe",
             r"C:\Program Files (x86)\Tesseract-OCR\tesseract.exe",
             os.path.expandvars(r"%LOCALAPPDATA%\Programs\Tesseract-OCR\tesseract.exe"),
             "/opt/homebrew/bin/tesseract", "/usr/local/bin/tesseract", "/usr/bin/tesseract"]
    for c in cands:
        if c and Path(c).exists():
            pytesseract.pytesseract.tesseract_cmd = str(c)
            return str(c)
    return None




def load_rgb(path) -> Image.Image:
    im = Image.open(path)
    im.load()
    if im.mode in ("RGBA", "LA", "P"):
        im = im.convert("RGBA")
        im = Image.alpha_composite(Image.new("RGBA", im.size, (255, 255, 255, 255)), im)
    return im.convert("RGB")




def _prep(img: Image.Image, box, target_w: int):
    """Crop a region given as FRACTIONS of the picture (works for any screenshot size) and enlarge it for OCR."""
    fx0, fy0, fx1, fy1 = box
    W, H = img.size
    x0, y0 = int(fx0 * W), int(fy0 * H)
    x1, y1 = max(int(fx1 * W), x0 + 10), max(int(fy1 * H), y0 + 10)
    crop = img.crop((x0, y0, min(x1, W), min(y1, H)))
    scale = min(5.0, max(1.5, target_w / crop.width))
    return crop.resize((int(crop.width * scale), int(crop.height * scale)), Image.LANCZOS).convert("L"), scale


def ocr_region(img: Image.Image, fx0, fy0, fx1, fy1, target_w=1500, psm=6) -> str:
    g, _ = _prep(img, (fx0, fy0, fx1, fy1), target_w)
    return pytesseract.image_to_string(g, config=f"--psm {psm}", timeout=_ocr_timeout())


def text_boxes(img: Image.Image, box, target_w=1500):
    """OCR word boxes of a region -> [{t, x0, y0, x1, y1, conf, line}] in pixels of `img`"""
    g, f = _prep(img, box, target_w)
    d = pytesseract.image_to_data(g, config="--psm 6", output_type=pytesseract.Output.DICT, timeout=_ocr_timeout())
    W, H = img.size
    ox, oy = int(box[0] * W), int(box[1] * H)
    out = []
    for i, t in enumerate(d["text"]):
        t = t.strip()
        if not t:
            continue
        try:
            conf = float(d["conf"][i])
        except (TypeError, ValueError):
            conf = 0.0
        x0, y0 = ox + d["left"][i] / f, oy + d["top"][i] / f
        out.append({"t": t, "x0": x0, "y0": y0, "x1": x0 + d["width"][i] / f, "y1": y0 + d["height"][i] / f, "conf": conf,
                    "line": (d["block_num"][i], d["par_num"][i], d["line_num"][i])})
    return out


def measure_legend(img: Image.Image, box, kind: str):
    """-> (digit_px, crop) : the median height (px of THIS picture) of the numbers in a legend = how big the screenshot's own
    text is, and a tight crop box (fractions) around the legend - shown next to the numbers as evidence."""
    try:
        words = text_boxes(img, box)
    except Exception:                                                          # noqa: BLE001
        return None, None
    W, H = img.size
    nums = [w for w in words if re.fullmatch(r"[-+]?\d+\.\d+|\d{2,}", w["t"]) and w["conf"] > 40]
    px = statistics.median(w["y1"] - w["y0"] for w in nums) if len(nums) >= 3 else None
    if kind == "legend":
        sel = [w for w in words if w["conf"] > 30 and (re.fullmatch(NUM, w["t"]) or re.fullmatch(r"(?i)ma?x|mi?n", w["t"]))]
        pad, need = (26, 10, 8, 8), 4              # colour bar sits left of the numbers
    else:
        lines = {}
        for w in words:
            lines.setdefault(w["line"], []).append(w)
        sel = [w for ln in lines.values() if BC_WORDS.search(" ".join(x["t"] for x in ln)) for w in ln]
        pad, need = (34, 8, 10, 8), 2              # coloured letter boxes sit left of the text
    crop = None
    if len(sel) >= need:
        x0, y0 = min(w["x0"] for w in sel) - pad[0], min(w["y0"] for w in sel) - pad[1]
        x1, y1 = max(w["x1"] for w in sel) + pad[2], max(w["y1"] for w in sel) + pad[3]
        crop = [round(max(0.0, x0) / W, 4), round(max(0.0, y0) / H, 4), round(min(float(W), x1) / W, 4), round(min(float(H), y1) / H, 4)]
    return px, crop


def crop_for(path, kind: str, box=None) -> Image.Image:
    """The part of a picture a value was read from ('legend' | 'bc' | 'header') - shown next to the number as evidence."""
    img = load_rgb(path)
    box = box or {"legend": LEGEND_BOX, "bc": BC_BOX, "header": HEADER_BOX}[kind]
    W, H = img.size
    return img.crop((int(box[0] * W), int(box[1] * H), int(box[2] * W), int(box[3] * H)))


def candidate_boxes(img: Image.Image, frac, px):
    """Regions to try, best first.  Normal screenshots: the fractional box.  Big / tall / wide pictures (a bigger ANSYS window
    keeps its text at the SAME pixel size, so a fraction of the picture lands in the wrong place): boxes anchored at the
    top-left in real pixels, for screen scales 1x, 1.5x and 2x."""
    out = [tuple(frac)]
    W, H = img.size
    if W > 1300 or H > 800:
        for u in (1.0, 1.5, 2.0):
            b = (min(1.0, px[0] * u / W), min(1.0, px[1] * u / H), min(1.0, px[2] * u / W), min(1.0, px[3] * u / H))
            if b[2] - b[0] > 0.02 and b[3] - b[1] > 0.02 and all(sum(abs(x - y) for x, y in zip(b, o)) > 0.03 for o in out):
                out.append(b)
    return out


def _lines(text: str):
    return [re.sub(r"\s+", " ", l).strip() for l in text.splitlines() if l.strip()]


# ═════════════════════════════════════════════ pixel checks ═════════════════════════════════════════════
def edge_density(img: Image.Image) -> float:
    """Mesh pictures are covered in fine dark lines -> far more edges than a plain CAD picture."""
    g = img.convert("L")
    g = g.resize((640, max(1, int(640 * g.height / g.width))))
    a = np.asarray(g, dtype=np.int16)
    gx = np.abs(np.diff(a, axis=1))[:-1, :]
    gy = np.abs(np.diff(a, axis=0))[:, :-1]
    return float(((gx + gy) > 60).mean())


def z_axis_direction(img: Image.Image):
    """'up' / 'down' / None - where the blue Z arrow of the ANSYS axis triad points (tells top view from bottom view)."""
    a = np.asarray(img).astype(int)
    H, W = a.shape[:2]
    reg = a[int(0.55 * H):, int(0.55 * W):]
    blue = (reg[..., 2] > 110) & (reg[..., 0] < 60) & (reg[..., 1] < 60)
    cyan = (reg[..., 0] < 90) & (reg[..., 1] > 150) & (reg[..., 2] > 150)
    if blue.sum() < 30 or cyan.sum() < 8:
        return None
    by, cy = np.where(blue)[0].mean(), np.where(cyan)[0].mean()
    return "up" if by < cy - 3 else ("down" if by > cy + 3 else None)


def red_pixels(img: Image.Image) -> int:
    """Count of pure-red contour pixels OUTSIDE the legend and the axis triad (=is there a visible red zone?)."""
    a = np.asarray(img).astype(int)
    H, W = a.shape[:2]
    m = (a[..., 0] > 200) & (a[..., 1] < 50) & (a[..., 2] < 50)
    m[: int(0.62 * H), : int(0.27 * W)] = False          # legend
    m[int(0.55 * H):, int(0.80 * W):] = False            # axis triad (red X arrow)
    return int(m.sum())


# ═══════════════════════════ where is this detail inside its parent picture? ═══════════════════════════
# A zoomed / detail view is normally a magnified crop of one of the other pictures of the same load case.  Rather than
# trusting the file name alone (council item A4: "you should take effort to see in which image specifically that zoomed
# view can be fitted"), the model viewport of the detail is searched for inside the model viewport of every candidate
# parent picture with a coarse-to-fine normalised cross-correlation.  That gives
#   * WHICH picture it belongs to (the best match wins, a weak match is reported as "not found" instead of guessed), and
#   * WHERE inside that picture it sits - used to draw the marking rectangle + leader line on the slide and to put the
#     detail next to its parent instead of on a slide of its own.
# Everything is offline NumPy on downscaled greyscale copies, so it costs a fraction of a second per pair.
MODEL_BOX = (0.26, 0.20, 1.0, 1.0)      # the model viewport of an ANSYS screenshot (legend + title chrome excluded)
MATCH_MIN = 0.50                        # below this the two pictures are not recognisably the same region
MATCH_LEVELS = (112, 240)               # coarse -> fine pyramid width of the parent's model viewport
MATCH_RATIOS = (0.10, 0.14, 0.20, 0.28, 0.38, 0.50, 0.65, 0.85)   # detail width as a fraction of the parent's width
MATCH_BUDGET = 5.0                      # seconds for ONE parent / detail pair - never block the page
MIN_WIN = 14                            # px - a smaller window is mostly background and matches anything
_MATCH_CACHE: dict = {}
_RGB_CACHE: dict = {}


def _rgb(path):
    try:
        key = (str(path), os.stat(path).st_mtime_ns)
    except OSError:
        return load_rgb(path)
    if key not in _RGB_CACHE:
        if len(_RGB_CACHE) > 24:
            _RGB_CACHE.clear()
        _RGB_CACHE[key] = load_rgb(path)
    return _RGB_CACHE[key]


def _grey_view(path, box, width):
    """NumPy copy of one region of a picture, `width` px wide, as (H, W, 3) float.

    COLOUR, not greyscale: a total-deformation plot and a von-Mises plot of the same model have the same outlines, so a
    greyscale match cannot tell a zoom of one from a zoom of the other (red-team finding D3).  The contour colours can."""
    img = _rgb(path)
    W, H = img.size
    x0, y0 = int(box[0] * W), int(box[1] * H)
    x1, y1 = max(int(box[2] * W), x0 + 8), max(int(box[3] * H), y0 + 8)
    v = img.crop((x0, y0, min(x1, W), min(y1, H)))
    k = width / v.width
    v = v.resize((width, max(8, round(v.height * k))), Image.BILINEAR)
    return np.asarray(v, dtype=np.float32)


def _box_sums(P):
    """zero-padded integral images of P and P^2 -> every window sum in four look-ups (fast NCC)"""
    h, w = P.shape
    ii = np.zeros((h + 1, w + 1), dtype=np.float64)
    jj = np.zeros((h + 1, w + 1), dtype=np.float64)
    ii[1:, 1:] = P.cumsum(axis=0).cumsum(axis=1)
    jj[1:, 1:] = (P.astype(np.float64) ** 2).cumsum(axis=0).cumsum(axis=1)
    return ii, jj


def _window_sums(ii, jj, th, tw):
    s = ii[th:, tw:] - ii[:-th, tw:] - ii[th:, :-tw] + ii[:-th, :-tw]
    q = jj[th:, tw:] - jj[:-th, tw:] - jj[th:, :-tw] + jj[:-th, :-tw]
    return s, q


def _ncc_map(P, T, contrast=0.45):
    """normalised cross-correlation of template T at every position of P -> score array (-1 where unusable).

    A plain NCC gives a confident answer for a FLAT window too: a white area matches a white area with score 1.0, and
    every ANSYS screenshot has large white margins, so an unrelated picture would "match" (red-team finding D2).
    A position is only scored when the window there carries at least `contrast` x the template's own contrast."""
    th, tw = T.shape
    if P.shape[0] < th or P.shape[1] < tw:
        return None
    Tn = T - T.mean()
    tn = float(np.sqrt((Tn * Tn).sum()))
    if tn < 1e-6:
        return None
    sd_t = float(T.std())
    if sd_t < 3.0:                                          # the detail itself is flat - nothing to match on
        return None
    Tn /= tn
    ii, jj = _box_sums(P)
    s, q = _window_sums(ii, jj, th, tw)
    n = float(th * tw)
    mean = s / n
    var = np.maximum(q / n - mean * mean, 0.0)
    sd = np.sqrt(var)
    den = sd * np.sqrt(n)
    W = np.lib.stride_tricks.sliding_window_view(P, (th, tw))
    num = np.einsum("ijkl,kl->ij", W, Tn, optimize=True)
    ok = (den > 1e-3) & (sd >= contrast * sd_t)
    return np.where(ok, num / np.where(ok, den, 1.0), -1.0)


def _ncc_map_rgb(P, T, contrast=0.45):
    """score map averaged over the three colour channels (-1 where no channel is usable)"""
    maps = []
    for c in range(P.shape[2]):
        m = _ncc_map(P[:, :, c], T[:, :, c], contrast)
        if m is not None:
            maps.append(m)
    if not maps:
        return None
    if len(maps) == 1:
        return maps[0]
    good = np.min(np.stack([m > -0.999 for m in maps]), axis=0)
    avg = np.mean(np.stack(maps), axis=0)
    return np.where(good, avg, -1.0)


def _best_at(P, T):
    m = _ncc_map_rgb(P, T)
    if m is None:
        return None
    iy, ix = np.unravel_index(int(np.argmax(m)), m.shape)
    return float(m[iy, ix]), int(ix), int(iy)


def match_region(parent_path, detail_path, budget: float = MATCH_BUDGET):
    """-> {'score': 0..1, 'box': [fx0, fy0, fx1, fy1] (fractions of the PARENT picture), 'cx','cy', 'side', 'row',
           'ratio'} or None when the detail is not recognisably a magnified part of the parent.

    Coarse-to-fine: the whole model viewport is searched once at 112 px wide for eight zoom ratios, then only the
    neighbourhood of the two best answers is searched again at full detail.  A pair costs well under a second."""
    try:
        key = (str(parent_path), os.stat(parent_path).st_mtime_ns, str(detail_path), os.stat(detail_path).st_mtime_ns)
    except OSError:
        return None
    if key in _MATCH_CACHE:
        return _MATCH_CACHE[key]
    t0, res = time.monotonic(), None
    try:
        Dfull = _grey_view(detail_path, MODEL_BOX, MATCH_LEVELS[-1])
        d_aspect = Dfull.shape[0] / max(1, Dfull.shape[1])
        cands = []
        for lvl, width in enumerate(MATCH_LEVELS):
            P = _grey_view(parent_path, MODEL_BOX, width)
            D = _grey_view(detail_path, MODEL_BOX, width)
            ph, pw = P.shape[:2]
            best = []

            def search(ratio, x0, y0, x1, y1):
                """best NCC of the detail at `ratio` of the parent's width, inside P[y0:y1, x0:x1]"""
                ww = max(MIN_WIN, min(pw - 2, round(pw * ratio)))
                wh = max(MIN_WIN // 2, min(ph - 2, round(ww * d_aspect)))
                if x1 - x0 < ww + 2 or y1 - y0 < wh + 2:
                    return None
                sub = P[y0:y1, x0:x1]
                T = np.asarray(Image.fromarray(D.astype(np.uint8)).resize((ww, wh), Image.BILINEAR), dtype=np.float32)
                got = _best_at(sub, T)
                if not got:
                    return None
                return {"score": got[0], "x": x0 + got[1], "y": y0 + got[2], "ww": ww, "wh": wh,
                        "ratio": ratio, "pw": pw, "ph": ph}

            if lvl == 0:                                    # coarse: the whole viewport, every zoom ratio
                for ratio in MATCH_RATIOS:
                    if time.monotonic() - t0 > budget:
                        break
                    got = search(ratio, 0, 0, pw, ph)
                    if got:
                        best.append(got)
            else:                                           # fine: only around the coarse answers
                for c in cands[:2]:
                    for f in (0.86, 1.0, 1.16):
                        if time.monotonic() - t0 > budget:
                            break
                        ratio = c["ratio"] * f
                        ww = max(MIN_WIN, round(pw * ratio))
                        wh = max(MIN_WIN // 2, round(ww * d_aspect))
                        sx, sy = pw / float(c["pw"]), ph / float(c["ph"])
                        cx, cy = int(round(c["x"] * sx)), int(round(c["y"] * sy))
                        mx, my = max(8, ww // 2), max(8, wh // 2)
                        got = search(ratio, max(0, cx - mx), max(0, cy - my),
                                     min(pw, cx + ww + mx), min(ph, cy + wh + my))
                        if got:
                            best.append(got)
            if not best:
                break
            best.sort(key=lambda c: -c["score"])
            cands = best[:3]
            if lvl == 0 and cands[0]["score"] < 0.20:        # nothing remotely similar: skip the fine pass
                break
        if cands and cands[0]["score"] >= MATCH_MIN:
            c = cands[0]
            bx0, by0, bx1, by1 = MODEL_BOX
            fw, fh = bx1 - bx0, by1 - by0
            x0 = min(1.0, max(0.0, bx0 + c["x"] / c["pw"] * fw))
            y0 = min(1.0, max(0.0, by0 + c["y"] / c["ph"] * fh))
            x1 = min(1.0, max(x0, bx0 + (c["x"] + c["ww"]) / c["pw"] * fw))
            y1 = min(1.0, max(y0, by0 + (c["y"] + c["wh"]) / c["ph"] * fh))
            res = {"score": round(c["score"], 3), "box": [round(x0, 4), round(y0, 4), round(x1, 4), round(y1, 4)],
                   "cx": round((x0 + x1) / 2, 4), "cy": round((y0 + y1) / 2, 4),
                   "side": "left" if (x0 + x1) / 2 < 0.5 else "right",
                   "row": "top" if (y0 + y1) / 2 < 0.5 else "bottom",
                   "ratio": round((x1 - x0) / max(1e-9, fw), 3)}
    except Exception:                                          # noqa: BLE001 - matching is a hint, never a hard failure
        res = None
    if time.monotonic() - t0 > budget:
        res = None
    if len(_MATCH_CACHE) > 400:
        _MATCH_CACHE.clear()
    _MATCH_CACHE[key] = res
    return res


def match_parents(detail_path, candidates, budget: float = MATCH_BUDGET):
    """best parent for one detail picture among [{'id','path','name'}, ...]
    -> (best_match_or_None, [{'id','score'} ...], ambiguous:bool)"""
    scores = []
    t0 = time.monotonic()
    for c in candidates:
        m = match_region(c["path"], detail_path, budget=max(1.0, budget - (time.monotonic() - t0)))
        if m:
            scores.append({"id": c["id"], "score": m["score"], "box": m["box"], "side": m["side"], "row": m["row"],
                           "cx": m["cx"], "cy": m["cy"], "name": c.get("name", "")})
    scores.sort(key=lambda s: -s["score"])
    amb = bool(len(scores) > 1 and scores[0]["score"] - scores[1]["score"] < 0.08 and scores[1]["score"] >= MATCH_MIN)
    return (scores[0] if scores else None), scores, amb


# ═════════════════════════════════════════════ parsing helpers ═════════════════════════════════════════════
def _clean_kind(txt: str) -> str:
    t = re.sub(r"[^A-Za-z0-9 ()/.+\-]", "", txt)
    return re.sub(r"\s+", " ", t).strip(" -.")[:60]


def parse_header(lines):
    """ANSYS title block -> letter, model, deck, role, unit, result kind, headline.

    The headline is the WHOLE title block ("A: 2401_KCP_MLD_Blade | Static Structural | Type: ...").  It is what decides
    which load case - and which ANSYS deck - a picture belongs to (council item A3): the letter alone is not enough when
    two different ANSYS projects both call their case "A", and the model name alone is not enough when one project has
    several cases.  Both are kept and both are compared fuzzily, because OCR and different ANSYS versions spell them
    slightly differently.

    Result plots carry 'Type:' and 'Unit:' lines, the setup picture does not.  Only 'Total Deformation' and
    'Equivalent (von-Mises) Stress' are the MAIN plots; every other result type (principal stress, shear stress,
    directional deformation, safety factor, strain ...) becomes an additional view that KEEPS ITS OWN NAME -
    it must never be mistaken for the von-Mises plot."""
    out = {"letter": None, "letter_conf": "", "model": None, "role": None, "unit": None, "has_header": False,
           "is_result": False, "result_kind": "", "rkind": "", "headline": "", "deck": "", "analysis": ""}
    head = [l for l in lines[:6]]
    out["headline"] = "  |  ".join(head)[:220]
    letter = model = None
    for l in head[:3]:
        m = re.match(r"^\W*([A-Z])\s*[:;.|\-)]\s*(\S.*)$", l)
        if m:
            letter, model = m.group(1), re.sub(r"(\s+[a-z]{1,2})+$", "", m.group(2).strip())
            out["letter_conf"] = "high"
            break
    if letter is None:                                    # the colon was not read: "A 2401_KCP_MLD_Blade"
        for l in head[:3]:
            m = re.match(r"^\W*([A-Z])\s+(\S.*)$", l)
            if m and ("_" in m.group(2) or len(m.group(2).split()) >= 2):
                letter, model = m.group(1), re.sub(r"(\s+[a-z]{1,2})+$", "", m.group(2).strip())
                out["letter_conf"] = "low"
                break
    out["letter"], out["model"] = letter, model
    if letter is not None:
        out["has_header"] = True
    joined = " ".join(lines)
    type_txt = ""
    for l in head[:7]:
        m = re.match(r"^\W*Type\s*[:;]\s*(.+)$", l, re.I)
        if m:
            type_txt = m.group(1).strip()
            break
    mu = re.search(r"Unit\s*[:;]\s*([A-Za-z/\u00b2\u00b5^0-9]+)", joined)
    out["unit"] = mu.group(1) if mu else None
    out["is_result"] = bool(type_txt or mu)
    name_line = lines[1] if len(lines) > 1 else ""
    src = f"{type_txt} | {name_line}".lower()
    low_all = " ".join(head[:3]).lower()
    am = re.search(r"(static structural|transient structural|steady-state thermal|explicit dynamics|modal|harmonic|"
                   r"buckling|linear buckling|fatigue|random vibration|response spectrum|thermal|creep)", low_all)
    out["analysis"] = am.group(1).title() if am else ""
    deform = "total deformation" in src
    vm = bool(re.search(r"von[- ]?mises|equivalent\s*(\(.*?\))?\s*stress", src))
    is_analysis_line = bool(ANALYSIS_NAME_RE.search(name_line.lower()))
    rk = classify_result_kind(type_txt) if type_txt else (None if is_analysis_line else classify_result_kind(name_line))
    out["rkind"] = rk or ""
    other = re.search(r"deformation|stress|strain|safety factor|displacement|reaction|energy|fatigue|damage|shear|"
                      r"principal|intensity|contact|bolt|temperature|creep|error", src)
    static = re.search(r"static structural|transient structural|steady-state|modal|harmonic|buckling", low_all)
    if deform or rk == "total_deformation":
        out["role"], out["rkind"] = "deformation", "total_deformation"
        out["result_kind"] = RESULT_KINDS["total_deformation"]["label"]
    elif vm or rk == "von_mises":
        out["role"], out["rkind"] = "stress", "von_mises"
        out["result_kind"] = RESULT_KINDS["von_mises"]["label"]
    elif out["is_result"] or (other and not static) or rk:
        out["role"] = "x_other"
        out["result_kind"] = rkind_label(rk, _clean_kind(type_txt or name_line) or "Other result")
    elif static:
        out["role"] = "bc"
    out["deck"] = deck_key(model or "", out["analysis"])
    return out


def deck_key(model: str, analysis: str = "") -> str:
    """normalised signature of the ANSYS deck (project / model) a picture was taken from.  Two screenshots of the same
    deck give the same key even when one of them was read with a small OCR mistake."""
    m = _norm_name(model)
    an = _norm_name(analysis)
    if an:
        m = re.sub(rf"\b{re.escape(an)}\b", " ", m)
    return re.sub(r"\s+", " ", m).strip()


def deck_sim(a: str, b: str) -> float:
    """how strongly two deck signatures say 'the same ANSYS project'.  Tolerant of OCR / spelling mistakes and of one
    name being a longer version of the other ("2401 kcp mld blade" vs "kcp mld blade")."""
    a, b = _norm_name(a), _norm_name(b)
    if not a or not b:
        return 0.0
    if a == b:
        return 1.0
    s = sim(a, b)
    ta = {t for t in a.split() if len(t) >= 4}             # a shared job number / long token is strong evidence
    tb = {t for t in b.split() if len(t) >= 4}
    if ta & tb:
        s = max(s, 0.86)
    return s


def file_hint(name: str):
    """'section' / 'detail' / None - from the file name only (there is no reliable cue in the pixels).
    Deliberately tolerant of the way engineers really name files: 'sectional stress view', 'sec_stress', 'zoomed vm',
    'CROSS SECTION AA', 'stress_zooom', 'hotspot weld'.  Everything is compared fuzzily, so a spelling mistake or an
    unusual word order still lands on the right view kind (see _norm_name / _fuzzy_in)."""
    n = _norm_name(Path(name).stem)
    best = None
    for kind, words in VIEW_WORDS.items():
        for w in words:
            if _fuzzy_in(w, n):
                best = kind
                break
        if best:
            break
    return best


# ═════════════════════════════ fuzzy text matching (file names, ANSYS headlines) ═════════════════════════════
# OCR and human typing both make mistakes, so nothing here is matched character by character: strings are normalised
# (case, punctuation, the classic OCR letter mix-ups), then compared by token overlap and by sequence similarity.
OCR_MIXUPS = str.maketrans({"0": "o", "1": "l", "|": "l", "!": "i", "5": "s", "8": "b"})


def _norm_name(s: str) -> str:
    """lowercase, punctuation -> space, whitespace collapsed.  Digits stay digits - a job number is a job number."""
    s = re.sub(r"[^A-Za-z0-9]+", " ", str(s or "").lower())
    return re.sub(r"\s+", " ", s).strip()


def _ocr_fold(s: str) -> str:
    """the same, plus the classic OCR confusions folded - only ever used as a SECOND comparison variant, so that an
    OCR'd '24O1' can still meet a typed '2401' without ever rewriting a real number."""
    return _norm_name(s).translate(OCR_MIXUPS)


def _tokens(s: str):
    return [t for t in _norm_name(s).split() if t]


def _sim1(a, b) -> float:
    if not a or not b:
        return 0.0
    if a == b:
        return 1.0
    seq = difflib.SequenceMatcher(None, a, b).ratio()
    ta, tb = set(a.split()), set(b.split())
    jac = len(ta & tb) / len(ta | tb) if (ta and tb) else 0.0
    cont = 1.0 if (a in b or b in a) else 0.0        # a short token inside a long name is a real hit
    return max(seq, jac, cont * 0.92)


def sim(a, b) -> float:
    """0..1 similarity of two strings, tolerant of spelling and OCR mistakes: the better of the plain and the
    OCR-folded comparison."""
    return max(_sim1(_norm_name(a), _norm_name(b)), _sim1(_ocr_fold(a), _ocr_fold(b)))


def _fuzzy_in(word: str, text: str, thr: float = 0.8) -> bool:
    """True when `word` occurs in `text` - as a token, as a stem, or with up to a couple of typing mistakes.
    Short words (aa, cut, vm) must match a WHOLE token, otherwise every name containing them would be a hit."""
    t = _norm_name(text)
    w = _norm_name(word)
    if not w:
        return False
    if len(w) < 5:
        return w in t.split()
    if re.search(rf"(?<![a-z]){re.escape(w)}", t):
        return True
    if re.search(rf"(?<![a-z]){re.escape(w[:len(w) - 2])}", t):     # "sectiona", "deformatio"
        return True
    return any(sim(w, tok) >= thr and len(tok) >= 4 for tok in t.split())


# view-kind words.  "section" wins over "detail" when both appear ("section detail of the weld" is a section).
VIEW_WORDS = {
    "section": ["section", "sectional", "sectn", "sect", "cut", "cutaway", "cross", "crosssection", "plane", "linearized",
                "linearisation", "path", "aa", "bb", "cc"],
    "detail": ["detail", "zoom", "zoomed", "zoomin", "closeup", "close", "enlarged", "enlarge", "magnified", "macro",
               "hotspot", "local", "blowup", "inset", "crop", "cropped", "weld", "junction", "corner"],
}
# words in a file name that say WHICH picture a section / detail belongs to
TARGET_WORDS = {
    "stress": ["stress", "vonmises", "von", "mises", "vm", "eqv", "equivalent", "sigma", "principal", "shear"],
    "deformation": ["deformation", "deform", "displacement", "disp", "deflection", "usum", "td"],
    "bc": ["setup", "bc", "bcs", "boundary", "support", "supports", "load", "loads", "constraint"],
    "mesh": ["mesh", "element", "elements", "grid"],
    "geometry": ["geometry", "geom", "model", "cad", "assembly"],
}


def name_view_kind(name: str):
    """'section' / 'detail' / None from the file name (fuzzy - see file_hint)."""
    return file_hint(name)


def name_target(name: str):
    """which picture a section / detail belongs to, from the file name: 'stress' | 'deformation' | 'bc' | 'mesh' |
    'geometry' | None.  'sectional stress view', 'stress_zoom', 'zoom of von mises' all give 'stress'."""
    toks = _tokens(name)
    hits = {}
    for k, words in TARGET_WORDS.items():
        for w in words:
            if any(_fuzzy_in(w, t) for t in toks) or _fuzzy_in(w, name):
                hits[k] = max(hits.get(k, 0.0), max(sim(w, t) for t in toks) if toks else 0.0)
    if not hits:
        return None
    return max(hits, key=lambda k: (round(hits[k], 2), -list(TARGET_WORDS).index(k)))


# ═════════════════════════════ result-type catalogue (ANSYS Structural results) ═════════════════════════════════
# Named after the ANSYS Mechanical result objects ("Results and Result Tools": equivalent / principal / shear / intensity
# stress, deformation, elastic-plastic-creep strain, strain energy, stress-tool safety factor, contact, bolt, fatigue,
# linearized stress, temperature ...).  Anything in this list can be chosen in the "What is it?" column and keeps its own
# name and unit in the report.  Only `total_deformation` and `von_mises` are the MAIN plots of a load case; everything
# else is an additional view (never mistaken for the von-Mises plot - that was council attack A2).
#   group: stress | deformation | strain | energy | tool | thermal | other    (used for the sanity checks and the units)
RK_ORDER = [
    # key                 label                                       short                        unit      group          matched against the ANSYS "Type:" line
    ("von_mises",        "Equivalent (von-Mises) Stress",             "Von-Mises stress",          "MPa",    "stress",       r"von[\s-]*mises|equivalent\s*\(?\s*von|equivalent\s+stress|\bseqv\b"),
    ("max_principal",    "Maximum Principal Stress",                  "Max principal stress",      "MPa",    "stress",       r"max(?:imum)?\s+principal|principal\s*1|\bs1\b"),
    ("min_principal",    "Minimum Principal Stress",                  "Min principal stress",      "MPa",    "stress",       r"min(?:imum)?\s+principal|principal\s*3|\bs3\b"),
    ("mid_principal",    "Middle Principal Stress",                   "Middle principal stress",   "MPa",    "stress",       r"mid(?:dle)?\s+principal|principal\s*2|\bs2\b"),
    ("vector_principal", "Vector Principal Stress",                   "Vector principal stress",   "MPa",    "stress",       r"vector\s+principal"),
    ("max_shear",        "Maximum Shear Stress (Tresca)",             "Max shear stress",          "MPa",    "stress",       r"max(?:imum)?\s+shear\s+stress|tresca|\btmax\b"),
    ("stress_intensity", "Stress Intensity",                          "Stress intensity",          "MPa",    "stress",       r"stress\s+intensity"),
    ("normal_stress",    "Normal Stress (X / Y / Z)",                 "Normal stress",             "MPa",    "stress",       r"normal\s+stress"),
    ("shear_stress",     "Shear Stress (XY / YZ / XZ)",               "Shear stress",              "MPa",    "stress",       r"shear\s+stress"),
    ("membrane",         "Membrane Stress",                           "Membrane stress",           "MPa",    "stress",       r"membrane\s+stress"),
    ("linearized",       "Linearized Stress (through thickness)",     "Linearized stress",         "MPa",    "stress",       r"linear[i]?z"),
    ("shell_stress",     "Shell Bending / Membrane Stress",           "Shell stress",              "MPa",    "stress",       r"shell\s+(bending|membrane|top|bottom)"),
    ("beam_stress",      "Beam Tool Stress (axial / bending)",        "Beam stress",               "MPa",    "stress",       r"(beam\s+tool|bending\s+stress|axial\s+force|combined\s+stress|torsional\s+moment|shear\s+force)"),
    ("total_deformation", "Total Deformation",                        "Total deformation",         "mm",     "deformation",  r"total\s+deformation|\busum\b"),
    ("dir_deformation",  "Directional Deformation",                   "Directional deformation",   "mm",     "deformation",  r"directional\s+deformation"),
    ("elastic_strain",   "Equivalent Elastic Strain",                 "Elastic strain",            "mm/mm",  "strain",       r"elastic\s+strain"),
    ("plastic_strain",   "Equivalent Plastic Strain",                 "Plastic strain",            "mm/mm",  "strain",       r"plastic\s+strain|\beppl\b"),
    ("total_strain",     "Equivalent Total Strain",                   "Total strain",              "mm/mm",  "strain",       r"total\s+strain"),
    ("shear_strain",     "Maximum Shear Elastic Strain",              "Shear strain",              "mm/mm",  "strain",       r"shear\s+(elastic\s+)?strain"),
    ("principal_strain", "Maximum Principal Elastic Strain",          "Principal strain",          "mm/mm",  "strain",       r"principal\s+(elastic\s+)?strain"),
    ("thermal_strain",   "Thermal Strain",                            "Thermal strain",            "mm/mm",  "strain",       r"thermal\s+strain"),
    ("creep_strain",     "Equivalent Creep Strain",                   "Creep strain",              "mm/mm",  "strain",       r"creep\s+strain"),
    ("strain_energy",    "Strain Energy",                             "Strain energy",             "mJ",     "energy",       r"(strain\s+energy|structural\s+strain\s+energy|energy\b)"),
    ("safety_factor",    "Safety Factor (Stress Tool)",               "Safety factor",             "",       "tool",         r"safety\s+(factor|margin)|stress\s+ratio|stress\s+tool|mohr[\s-]*coulomb"),
    ("contact",          "Contact Status / Contact Tool",             "Contact result",            "",       "tool",         r"contact\s+(status|tool|misc|pressure|gap|penetration)"),
    ("bolt",             "Bolt Pretension / Bolt Tool",               "Bolt result",               "N",      "tool",         r"bolt\s+(tool|pretension|series)"),
    ("fatigue",          "Fatigue (Life / Damage / Safety Factor)",   "Fatigue result",            "",       "tool",         r"fatigue|biaxiality|\blife\b"),
    ("damage",           "Damage / Failure Criterion",                "Damage result",             "",       "tool",         r"damage|failure\s+criter|hashin|tsai|hill"),
    ("structural_error", "Structural Error (mesh quality)",           "Structural error",          "",       "tool",         r"structural\s+error"),
    ("temperature",      "Temperature",                               "Temperature",               "°C",     "thermal",      r"temperature"),
    ("acceleration",     "Total / Directional Acceleration",          "Acceleration",              "mm/s²",  "other",        r"acceleration"),
    ("velocity",         "Total / Directional Velocity",              "Velocity",                  "mm/s",   "other",        r"velocity"),
    ("reaction",         "Reaction Force / Moment",                   "Reaction",                  "N",      "other",        r"reaction\s+(force|moment)?"),
]
RESULT_KINDS = {}
for _k, _label, _short, _unit, _group, _rx in RK_ORDER:
    RESULT_KINDS[_k] = {"key": _k, "label": _label, "short": _short, "unit": _unit, "group": _group, "rx": re.compile(_rx, re.I)}
RK_BY_RX = [(RESULT_KINDS[k], RESULT_KINDS[k]["rx"]) for k, *_ in RK_ORDER]
MAIN_RKINDS = {"total_deformation": "deformation", "von_mises": "stress"}        # the only two MAIN plots
RK_GROUPS = ("stress", "deformation", "strain", "energy", "tool", "thermal", "other")
ANALYSIS_NAME_RE = re.compile(r"static structural|transient structural|steady[- ]state|explicit dynamics|modal|harmonic|"
                              r"buckling|response spectrum|random vibration|\bthermal\b|\bfatigue\b|\bcreep\b")


def classify_result_kind(text: str):
    """the catalogue key for an ANSYS 'Type:' line / title / file name (None when nothing matches).
    'Maximum Shear Stress' -> max_shear,  'equivalent plastic strain' -> plastic_strain,  'sectioanl stress' -> stress."""
    t = _norm_name(text)
    if not t:
        return None
    for rk, rx in RK_BY_RX:                                    # the catalogue order is the priority order
        if rx.search(t):
            return rk["key"]
    toks = t.split()
    for rk, _rx in RK_BY_RX:                                   # second pass: tolerant of a typing / OCR mistake
        if any(sim(w, tok) >= 0.86 for w in rk["short"].lower().split() for tok in toks if len(tok) >= 4):
            return rk["key"]
    return None


def rkind(key):
    return RESULT_KINDS.get(key or "") or None


def rkind_label(key, default="Other result"):
    rk = rkind(key)
    return rk["label"] if rk else (default or "Other result")


def rkind_unit(key):
    rk = rkind(key)
    return rk["unit"] if rk else None


def rkind_group(key):
    rk = rkind(key)
    return rk["group"] if rk else "other"


def parse_choice(value: str):
    """'role|view_kind|rkind' (what the web page sends) -> (role, view_kind, rkind).  Also accepts a plain role."""
    parts = [p.strip() for p in str(value or "").split("|")]
    role = parts[0] or "unknown"
    vk = parts[1] if len(parts) > 1 and parts[1] in ("section", "detail") else ""
    rk = parts[2] if len(parts) > 2 and parts[2] in RESULT_KINDS else ""
    if role == "x_other" and not rk:
        rk = "other"
    if vk and role in ("deformation", "stress", "bc", "geometry", "mesh"):      # a view kind always makes it an extra
        role = "x_" + role
    return role, vk, rk


def make_choice(info: dict) -> str:
    """the reverse of parse_choice - pre-selects the right entry of the 'What is it?' list"""
    return f"{info.get('role', 'unknown')}|{info.get('view_kind') or ''}|{info.get('rkind') or ''}"


def parse_result_legend(text: str):
    """-> (max, min, values[]) from OCR text of an ANSYS contour legend.
    The colour swatch in front of each number is often read as junk ("me 292", "| 1 256"), so the number at the END
    of the line is used."""
    text = re.sub(r"(?<=\d),(?=\d)", ".", text)          # "24,7" -> "24.7"
    mx = mn = None
    vals = []
    for line in _lines(text):
        mmax = re.search(rf"({NUM})\s*M[aeo]\s*x\b", line, re.I)
        mmin = re.search(rf"({NUM})\s*M[il1I]\s*n\b", line, re.I)
        if mmax and mx is None:
            mx = float(mmax.group(1))
            vals.append(mx)
        elif mmin:
            mn = mmin.group(1)
            if mx is not None:
                break
        elif mx is not None:
            m = re.search(rf"(?<![\d.])({NUM})\s*$", line)
            if m and len(line[:m.start()].strip()) <= 5:
                vals.append(float(m.group(1)))
    return mx, mn, vals


def repair_legend(vals):
    """The legend values must fall from top to bottom.  Where two neighbours break that, the culprit is the one that a power
    of ten can put right (OCR dropped a decimal point: '1.88' read as '188').  If neither can be repaired, the later one is
    dropped.  The MAX itself (first value) is never touched here.  -> (values, notes)"""
    v, notes = list(vals), []
    for _ in range(60):
        bad = next((i for i in range(1, len(v)) if v[i] >= v[i - 1]), None)
        if bad is None:
            break
        done = False
        for k in (bad, bad - 1):
            if k == 0:
                continue
            for f in (0.1, 0.01, 10.0, 100.0, 0.001, 1000.0):
                c = float(f"{v[k] * f:.6g}")
                if c > 0 and c < v[k - 1] and (k + 1 >= len(v) or c > v[k + 1]):
                    notes.append(f"legend value {v[k]:g} repaired to {c:g}")
                    v[k] = c
                    done = True
                    break
            if done:
                break
        if not done:
            notes.append(f"legend value {v[bad]:g} ignored")
            del v[bad]
    return v, notes


def read_result_legend(img: Image.Image):
    """Max / Min / legend values - READ TWICE with different OCR settings.  Two independent readings must agree
    on the Max; if they do not, the picture is flagged so the engineer checks it against the evidence crop."""
    best = None
    for box in candidate_boxes(img, LEGEND_BOX, LEGEND_PX):
        a = parse_result_legend(ocr_region(img, *box, target_w=1500, psm=6))
        b = parse_result_legend(ocr_region(img, *box, target_w=1800, psm=11))
        (mx_a, mn_a, va), (mx_b, mn_b, vb) = a, b
        conf, note, mx = "high", None, mx_a
        if mx_a is not None and mx_b is not None:
            if abs(mx_a - mx_b) > 1e-6 * max(abs(mx_a), abs(mx_b), 1e-12):
                conf = "low"
                second = (va[1] if len(va) > 1 else (vb[1] if len(vb) > 1 else None))
                fits = [m for m in (mx_a, mx_b) if second is None or m > second]
                mx = fits[0] if fits else mx_a
                note = f"two readings of the Max value disagree ({mx_a:g} and {mx_b:g}) - check it against the legend picture"
        elif mx_a is None and mx_b is None:
            mx, conf = None, "low"
        else:
            mx = mx_a if mx_a is not None else mx_b
            conf, note = "medium", "the Max value could only be read once - please check it"
        vals = va if len(va) >= len(vb) else vb
        if vals and mx is not None and vals[0] != mx:
            vals = [mx] + vals[1:]
        notes = []
        if len(vals) > 1:
            vals, notes = repair_legend(vals)
            if vals and mx is not None and vals[1:] and mx < vals[1]:
                conf, note = "low", f"the Max ({mx:g}) is smaller than the next legend value ({vals[1]:g}) - the reading is probably wrong"
        res = dict(max=mx, min=mn_a or mn_b, vals=vals, conf=conf, note=note, repairs=notes, box=box)
        if mx is not None:
            return res
        best = best or res
    return best


BC_WORDS = re.compile(r"support|pressure|gravity|force|moment|displacement|load|temperature|fixed|cylindrical|"
                      r"frictionless|convection|heat|bearing|remote|torque|acceleration|velocity|rotation|spring|pretension",
                      re.I)


def parse_bc_legend(text: str):
    out = []
    for l in _lines(text):
        l = re.sub(r"^[\(\[\{]\s*[A-Za-z]{1,2}\s*[\)\]\}]?\s+", "", l)    # "(Ay Fixed Support", "[Bl Pressure" - the coloured letter box
        l = re.sub(r"^\s*[A-Za-z]{0,2}[\]\)\}\|]\s*", "", l)       # "i] Fixed Support" - the coloured letter box read as text
        l = re.sub(r"\s+[A-Za-z]?[\]\}]$", "", l)                   # trailing "r]"
        l = re.sub(r"^[^A-Za-z]+", "", l)                   # junk left over from the coloured letter box
        l = re.sub(r"\s+['`\"|,.;:]+$", "", l)
        if BC_WORDS.search(l) and not re.match(r"^(static|transient|time)\b", l, re.I):
            out.append(l)
    return out


def _value_unit(text: str):
    if ":" not in text:
        return None, ""
    m = re.match(rf"\s*({NUM})\s*(.*)$", text.split(":", 1)[1])
    return (float(m.group(1)), m.group(2).strip()) if m else (None, "")


def _sig(x: float, n: int = 3) -> str:
    return np.format_float_positional(x, precision=n, unique=False, fractional=False, trim="-")


def bc_sentence(legend: str):
    """One ANSYS legend line -> (draft sentence in the template's style, needs_input)."""
    t = legend.lower()
    val, unit = _value_unit(legend)
    u = re.sub(r"[\s\-*·•.]+", "", unit).lower()
    if "gravity" in t or "acceleration" in t and "standard" in t:
        return "Self weight considered.", False
    if "pressure" in t and val is not None:
        mpa = val * {"mpa": 1.0, "pa": 1e-6, "kpa": 1e-3, "bar": 0.1, "psi": 0.00689476, "mmwc": 9.80665e-6}.get(u, 1.0)
        mm = mpa * 1e6 / 9.80665
        mm_txt = f"{round(mm):d}" if abs(mm - round(mm)) < 0.002 * mm else f"{mm:.2f}"
        return f"Design pressure {mm_txt} mmWC applied which is {_sig(mpa)} MPa.", False
    if "moment" in t or "torque" in t:
        if val is not None:
            nmm = val * (1000.0 if u == "nm" else 1e6 if u == "knm" else 1.0)
            return f"Moment of {nmm:,.0f} N·mm ({nmm / 1000:g} N·m) applied at {PLACEHOLDER}.", True
        return f"Moment applied at {PLACEHOLDER}.", True
    if "force" in t and val is not None:
        n = val * (1000.0 if u == "kn" else 1.0)
        return f"Force of {n:,.0f} N applied at {PLACEHOLDER}.", True
    if "cylindrical" in t:
        return f"Cylindrical support applied on {PLACEHOLDER}.", True
    if "frictionless" in t:
        return f"Frictionless support applied on {PLACEHOLDER}.", True
    if "remote displacement" in t:
        return f"Remote displacement applied at {PLACEHOLDER}.", True
    if "displacement" in t:
        return "Free displacement of [x] mm in [axis] direction.", True
    if "fixed" in t:
        return f"Fixed support applied on {PLACEHOLDER}.", True
    if "temperature" in t and val is not None:
        return f"Temperature of {val:g} °C applied.", False
    return f"[{legend} - please describe]", True


def case_headline(legend_lines):
    """('Pressure Only', '(Self Weight + Pressure)') built from the LOADS found in the BC legend."""
    loads, self_weight = [], False
    for l in legend_lines:
        t = l.lower()
        if "gravity" in t:
            self_weight = True
        elif "pressure" in t:
            loads.append("Pressure")
        elif "moment" in t or "torque" in t:
            loads.append("Moment")
        elif "force" in t:
            loads.append("Force")
        elif "temperature" in t:
            loads.append("Temperature")
    loads = list(dict.fromkeys(loads))
    if not loads:
        return "", ""
    name = f"{loads[0]} Only" if len(loads) == 1 else " + ".join(loads)
    return name, "(" + " + ".join((["Self Weight"] if self_weight else []) + loads) + ")"




def gravity_of(legend_lines):
    """('Standard Earth Gravity: 9806.6 mm/s²') -> {'value': 9806.6, 'unit': 'mm/s²'} or None"""
    for l in legend_lines:
        if "gravity" in l.lower() or "acceleration" in l.lower():
            val, unit = _value_unit(l)
            if val is not None:
                return {"value": val, "unit": re.sub(r"[?*\"'`]+$", "²", unit.strip())}
    return None


# ═════════════════════════════════════════════ one picture ═════════════════════════════════════════════
def extract_role_data(img: Image.Image, info: dict, role: str, k_an: float = 1.0):
    """Read the role-specific details (legend numbers, boundary-condition lines, view direction ...).
    Also used when the user re-assigns a picture to another role.  k_an = analysis scale (img px / original px)."""
    base = role[2:] if role.startswith("x_") else role
    done = info.setdefault("_done", [])
    if base in done:
        return
    done.append(base)
    info["notes"] = [n for n in info["notes"] if "recognised" not in n and "readings" not in n and "Max value" not in n]
    if base in ("deformation", "stress", "other"):
        rl = read_result_legend(img)
        info.update(max=rl["max"], min=rl["min"], legend_values=rl["vals"])
        if rl["max"] is None:
            info["notes"].append("Could not read the Max value from the legend - please type it in.")
            info["confidence"] = "low"
        elif rl["note"]:
            info["notes"].append(rl["note"][0].upper() + rl["note"][1:] + ".")
            info["confidence"] = rl["conf"]
        if rl["repairs"]:
            info["repairs"] = rl["repairs"]
        px, crop = measure_legend(img, rl["box"], "legend")
        if px:
            info["legend_px"] = round(px / k_an, 2)
        if crop:
            info["crop_legend"] = crop
        if info["unit"] is None:
            info["unit"] = "mm" if base == "deformation" else "MPa"
        if base == "stress":
            info["red_px"] = red_pixels(img)
    elif base == "bc":
        legend, bc_box = [], BC_BOX
        for box in candidate_boxes(img, BC_BOX, BC_PX):               # big pictures: keep the region that reads the MOST lines
            lines = parse_bc_legend(ocr_region(img, *box, psm=6))
            if len(lines) > len(legend):
                legend, bc_box = lines, box
        drafts = [bc_sentence(l) for l in legend]
        info.update(bc_legend=legend, bc_draft=[dict(legend=l, text=t, needs_input=n) for l, (t, n) in zip(legend, drafts)],
                    headline_bc=list(case_headline(legend)), gravity=gravity_of(legend))
        px, crop = measure_legend(img, bc_box, "bc")
        if px:
            info["legend_px"] = round(px / k_an, 2)
        if crop:
            info["crop_bc"] = crop
        if not legend:
            info["notes"].append("Could not read the boundary-condition legend - please type the lines.")
            info["confidence"] = "low"
    elif base in ("geometry", "mesh"):
        if info.get("edge_density") is None:
            info["edge_density"] = round(edge_density(img), 3)
        info["z_dir"] = z_axis_direction(img)


def ocr_available() -> bool:
    try:
        return pytesseract is not None and find_tesseract() is not None
    except Exception:                                              # noqa: BLE001
        return False


# What a file name can tell (used when the picture reader is not installed, or failed on a picture).  Order matters.
NAME_ROLES = (
    ("bc", r"(?<![a-z])(bc|bcs|boundary|support|supports|setup|set[-_ ]?up|constraints?|loads?|loading)(?![a-z])"),
    ("deformation", r"deform|displac|(?<![a-z])(td|def|usum|disp)(?![a-z])"),
    ("stress", r"stress|von|mises|equiv|(?<![a-z])(vm|eqv|sigma)(?![a-z])"),
    ("mesh", r"mesh|element|grid"),
    ("geometry", r"geom|model|cad|(?<![a-z])(top|bottom|iso|isometric|front|rear|plan)(?![a-z])"),
)
NAME_CASE = re.compile(r"(?<![a-z])(?:load[-_ ]?case|case|lc|c)[-_ ]?(\d{1,2})(?!\d)")


def guess_from_name(name: str):
    """(role or None, case number or None) from the file name only"""
    n = Path(name).stem.lower()
    role = next((r for r, rx in NAME_ROLES if re.search(rx, n)), None)
    m = NAME_CASE.search(n)
    return role, (int(m.group(1)) if m else None)


def guess_detail_from_name(name: str):
    """what a file name says about an ADDITIONAL view: (view_kind, target kind, result-catalogue key).
    'sectional stress view' -> ('section', 'stress', 'von_mises' when nothing better is named)
    'zoomed max principal'   -> ('detail', 'stress', 'max_principal')
    'mesh_detail_2'          -> ('detail', 'mesh', None)"""
    vk = name_view_kind(name)
    tgt = name_target(name)
    rk = classify_result_kind(Path(name).stem)
    if rk in MAIN_RKINDS and not vk:                     # a plain "stress.png" is a main plot, not a catalogue extra
        rk = None
    return vk, tgt, rk


def analyze_manual_one(path, idx: int, name=None, why: str = "ocr") -> dict:
    """A picture that is NOT read by the picture reader (Tesseract missing, or the reading failed on this picture).
    The role is guessed from the file name, every number is left to the engineer.  Never raises for a readable picture."""
    nm = name or Path(path).name
    try:
        with Image.open(path) as im:
            W0, H0 = im.size
    except Exception:                                              # noqa: BLE001
        W0 = H0 = 0
    role, case_no = guess_from_name(nm)
    vk, tgt, rk = guess_detail_from_name(nm)
    if role in ("bc", "deformation", "stress") and vk:              # "..._section" / "..._detail": an additional view
        role = "x_" + role
    if role is None and rk:                                         # "max principal.png" -> an additional result view
        role, vk = "x_other", (vk or "")
    info = {"id": idx, "name": nm, "path": str(path), "width": W0, "height": H0, "role": role or "unknown",
            "confidence": "medium" if role else "low", "letter": (chr(64 + case_no) if (case_no and role in CASE_ROLES) else None),
            "letter_conf": "name" if case_no else "", "model": None, "deck": "", "headline": "", "analysis": "",
            "header": [], "notes": [], "unit": None, "max": None, "min": None, "legend_values": [],
            "bc_legend": [], "bc_draft": [], "headline_bc": ["", ""], "z_dir": None, "red_px": None, "edge_density": None,
            "result_kind": rkind_label(rk) if rk else "", "rkind": rk or "", "view_kind": vk or "", "name_target": tgt or "",
            "parent": None, "attach": None, "pos": "auto", "match": None, "match_ambiguous": False,
            "legend_px": None, "gravity": None, "dup_of": None, "user_set": False, "manual": True,
            "hash": hashlib.md5(Path(path).read_bytes()).hexdigest()}
    if role is None:
        info["notes"].append("I could not tell what this picture is - please choose it in the list." if why == "ocr"
                             else f"This picture could not be read automatically ({why}) - please choose what it is.")
    elif why != "ocr":
        info["notes"].append(f"This picture could not be read automatically ({why}) - its type was guessed from the file name; please check it and type the numbers.")
    if role and info["unit"] is None:
        info["unit"] = rkind_unit(rk) if rk else None
    try:                                                           # the view direction needs no OCR
        if role in ("geometry", "mesh"):
            img = load_rgb(path)
            info["edge_density"] = round(edge_density(img), 3)
            info["z_dir"] = z_axis_direction(img)
    except Exception:                                              # noqa: BLE001
        pass
    return info


def analyze_one(path, idx: int, name=None) -> dict:
    img = load_rgb(path)
    W0, H0 = img.size
    k_an = 1.0
    if max(img.size) > ANALYSIS_MAX_SIDE:                      # huge screenshot: analyse a smaller copy (memory)
        k_an = ANALYSIS_MAX_SIDE / max(img.size)
        img = img.resize((max(1, round(W0 * k_an)), max(1, round(H0 * k_an))), Image.LANCZOS)
    info = {"id": idx, "name": name or Path(path).name, "path": str(path), "width": W0, "height": H0, "role": "unknown",
            "confidence": "high", "letter": None, "letter_conf": "", "model": None, "deck": "", "headline": "",
            "analysis": "", "header": [], "notes": [], "unit": None, "max": None, "min": None, "legend_values": [],
            "bc_legend": [], "bc_draft": [], "headline_bc": ["", ""], "z_dir": None, "red_px": None, "edge_density": None,
            "result_kind": "", "rkind": "", "view_kind": "", "name_target": "", "parent": None, "attach": None,
            "pos": "auto", "match": None, "match_ambiguous": False, "legend_px": None, "gravity": None,
            "hash": hashlib.md5(Path(path).read_bytes()).hexdigest(), "dup_of": None, "user_set": False}

    hdr_lines = []
    for hbox in candidate_boxes(img, HEADER_BOX, HEADER_PX):
        hdr_lines = _lines(ocr_region(img, *hbox))
        h = parse_header(hdr_lines)
        if h["has_header"] or h["role"]:
            break
    info.update(letter=h["letter"], letter_conf=h["letter_conf"], model=h["model"], header=hdr_lines[:5], unit=h["unit"],
                result_kind=h["result_kind"], rkind=h["rkind"], headline=h["headline"], deck=h["deck"], analysis=h["analysis"])

    vk, tgt, rk_name = guess_detail_from_name(info["name"])       # the file name says "section" / "zoom" / a result type
    info["view_kind"], info["name_target"] = vk or "", tgt or ""
    if vk and info["role"] in ("bc", "deformation", "stress"):    # a section / zoom of a main plot is an additional view
        info["role"] = "x_" + info["role"]
    if rk_name and info["role"] == "x_other" and not info["rkind"]:
        info["rkind"] = rk_name
        info["result_kind"] = rkind_label(rk_name)

    if h["role"]:
        info["role"] = h["role"] if not (vk and h["role"] in ("bc", "deformation", "stress")) else "x_" + h["role"]
        if info["role"] == "x_other":
            info["confidence"] = "medium"
            info["notes"].append(f"Result type '{info['result_kind'] or 'not recognised'}' is not Total Deformation or Equivalent "
                                 f"(von-Mises) Stress, so it is used as an additional view. Change it in the table if that is wrong.")
    elif h["has_header"]:
        info["role"] = "unknown"
        info["notes"].append("Title not recognised - please choose what this picture is.")
    else:                                              # no ANSYS title -> plain model picture
        ed = edge_density(img)
        info["edge_density"] = round(ed, 3)
        info["role"] = "mesh" if ed > MESH_EDGE_THRESHOLD else "geometry"
        info["confidence"] = "medium"
    if vk and info["role"] in ("bc", "deformation", "stress"):
        info["role"] = "x_" + info["role"]
    if info["letter_conf"] == "low":
        info["notes"].append("The load-case letter in the title was read without its colon - please check the load case of this picture.")
    extract_role_data(img, info, info["role"], k_an)
    if info.get("unit") is None and info.get("rkind"):
        info["unit"] = rkind_unit(info["rkind"])
    return info


def regroup(infos, assignments, settings=None):
    """Apply the user's corrections {id: role, case, view_kind, rkind, parent, attach, pos} and return the new draft."""
    by_id = {i["id"]: i for i in infos}
    for a in assignments:
        i = by_id.get(a["id"])
        if not i:
            continue
        role = a.get("role") or i["role"]
        if "|" in str(role):                                       # the web page sends "role|view_kind|rkind"
            role, vk, rk = parse_choice(role)
            i["view_kind"], i["rkind"] = vk, rk
            if rk:
                i["result_kind"] = rkind_label(rk)
        case = a.get("case") if role in CASE_ROLES else None
        if case is None and role in CASE_ROLES:
            case = i.get("case") or 1
        for k in ("view_kind", "rkind", "parent", "attach", "pos"):
            if k in a:
                v = a[k]
                nv = (v if k != "view_kind" else (v if v in ("section", "detail") else ""))
                if k == "parent" and nv != i.get("parent"):
                    i["match"] = None                       # a new parent means the old region match is meaningless
                i[k] = nv
        if i.get("rkind"):
            i["result_kind"] = rkind_label(i["rkind"])
        if role != i["role"] or case != i.get("case"):
            i["user_set"] = True
        if role != i["role"]:
            base = role[2:] if role.startswith("x_") else role
            i["match"] = None                                      # a new role invalidates the old region match
            if base in ("deformation", "stress", "other", "bc", "geometry", "mesh"):
                try:
                    if base in ("geometry", "mesh") or ocr_available():
                        extract_role_data(load_rgb(i["path"]), i, role)
                    else:
                        i["notes"] = [n for n in i["notes"] if "type" not in n]
                        i["notes"].append("The picture reader is not installed - type the numbers (and the lines of the setup list) yourself.")
                        i["confidence"] = "low"
                except Exception as e:                                         # noqa: BLE001 - a failed reading never stops the work
                    i["notes"].append(f"Could not read this picture automatically ({type(e).__name__}) - please type the numbers.")
                    i["confidence"] = "low"
            if base == "other" and not i.get("rkind"):
                i["rkind"] = classify_result_kind(i["name"]) or ""
                i["result_kind"] = rkind_label(i["rkind"]) if i["rkind"] else ""
            if i.get("unit") is None and i.get("rkind"):
                i["unit"] = rkind_unit(i["rkind"])
            i["notes"] = [n for n in i["notes"] if "recognised" not in n and "additional view" not in n]
            i["confidence"] = "high" if i["confidence"] != "low" else "low"
        i["role"], i["case"] = role, case
    return build_draft(infos, settings)


# ═════════════════════════════════ whole set: decks, cases, views ═════════════════════════════════
DECK_MERGE = 0.62          # two model names at least this similar are the same ANSYS deck
DECK_SAME = 0.90           # ... and this similar means one of them is just a longer / better-read version


def _norm_model(m):
    return re.sub(r"[_\s]+", " ", (m or "").lower()).strip()


def _common_prefix(a, b):
    n = 0
    for x, y in zip(a, b):
        if x != y:
            break
        n += 1
    return n


def cluster_decks(infos):
    """Split the load-case pictures into ANSYS decks (projects).  A deck is what the title block names, so two projects
    that both call their case "A" end up in two decks and get their own load-case numbers (council item A3).
    -> [{'key', 'name', 'members':[info]}] in the order the decks first appear."""
    decks = []
    loose = []
    for i in infos:
        sig = _norm_name(i.get("model") or i.get("deck") or "")
        if not sig:
            loose.append(i)
            continue
        best, bs = None, 0.0
        for d in decks:
            s = deck_sim(sig, d["key"])
            if s > bs:
                best, bs = d, s
        if best is not None and bs >= DECK_MERGE:
            best["members"].append(i)
            if bs >= DECK_SAME and len(sig) > len(best["key"]):
                best["key"] = sig                            # keep the best-read version of the name
        else:
            decks.append({"key": sig, "name": (i.get("model") or "").strip(), "members": [i]})
    for i in loose:                                          # nothing read: follow the letter into a known deck
        cands = [d for d in decks if any(m.get("letter") and m["letter"] == i.get("letter") for m in d["members"])]
        if len(cands) == 1:
            cands[0]["members"].append(i)
        elif len(decks) == 1:
            decks[0]["members"].append(i)
        else:
            i["case_uncertain"] = "deck"
    for n, d in enumerate(decks, 1):
        d["n"] = n
        for i in d["members"]:
            i["deck_id"] = n
    return decks


def extra_heading(info):
    """Heading of an additional view.  A named ANSYS result keeps its own name (no yellow box needed); a section or a
    zoom stays yellow until the engineer says WHERE the cut / detail is - no program can see that."""
    vk = info.get("view_kind") or file_hint(info["name"])
    rk = info.get("rkind") or ""
    what = RESULT_KINDS[rk]["short"] if rk in RESULT_KINDS else None
    if not what:
        base = (info["role"][2:] if str(info.get("role", "")).startswith("x_") else info.get("role", ""))
        what = {"stress": "von-Mises stress", "deformation": "total deformation", "bc": "the setup",
                "other": "this result"}.get(base, "this view")
    if vk == "section":
        return f"Section view \u2013 {what} {PLACEHOLDER}"
    if vk == "detail":
        return f"Detail view \u2013 {what} {PLACEHOLDER}"
    if info["role"] == "x_other" and rk:
        return RESULT_KINDS[rk]["label"]
    if info["role"] == "x_other" and info.get("result_kind"):
        return info["result_kind"]
    return f"Additional view \u2013 {what} [describe - please confirm]"


def _parent_of(info, case, by_id):
    """which main picture of the case an additional view belongs to -> (parent_id, why, matched_region)"""
    mains = [(case.get("def_id"), "deformation"), (case.get("stress_id"), "stress"), (case.get("bc_id"), "bc")]
    mains = [(pid, k) for pid, k in mains if pid is not None]
    if not mains:
        return None, "", None
    if info.get("parent") is not None and any(pid == info["parent"] for pid, _k in mains):
        chosen = info["parent"]
        return chosen, "you chose it", _region(info, by_id, [by_id[pid] for pid, _k in mains if pid == chosen])
    base = info["role"][2:] if info["role"].startswith("x_") else info["role"]
    tgt = info.get("name_target") or ""
    group = rkind_group(info.get("rkind")) if info.get("rkind") else ""
    want = {"stress": "stress", "deformation": "deformation", "bc": "bc"}.get(tgt)
    if want is None and base in ("stress", "deformation", "bc"):
        want = base
    if want is None:
        want = {"stress": "stress", "strain": "stress", "energy": "deformation", "tool": "stress",
                "thermal": "stress"}.get(group, "stress")
    cands = [pid for pid, k in mains if k == want] or [pid for pid, _k in mains]
    order = {"stress": 0, "deformation": 1, "bc": 2}
    cands.sort(key=lambda pid: order.get(next(k for p2, k in mains if p2 == pid), 9))
    chosen = cands[0]
    reg = _region(info, by_id, [by_id[pid] for pid, _k in mains])
    if reg and reg.get("id") is not None and reg["id"] != chosen:
        return reg["id"], "found inside that picture", reg
    return chosen, ("the file name says so" if want == tgt and tgt else "the usual parent for this result type"), reg


def _region(info, by_id, candidates):
    """look for this view inside the candidate pictures (pixels, not the file name) -> match dict with 'id', or None"""
    if info.get("match"):
        return info["match"]
    if info.get("view_kind") not in ("detail", "section") and not info.get("attach"):
        return None
    cands = [c for c in candidates if c and c.get("path") and c["id"] != info["id"]]
    if not cands:
        return None
    best, scores, amb = match_parents(info["path"], [{"id": c["id"], "path": c["path"], "name": c["name"]} for c in cands])
    info["match_ambiguous"] = amb
    if not best:
        return None
    m = dict(best)
    m["found"] = True
    info["match"] = m
    return m


def assign_cases(infos):
    """Group pictures into load cases -> list of case drafts (one per case number).
    A case has ONE main setup / deformation / stress picture (the one with the highest Max wins); further pictures of the
    same kind become additional views, each ATTACHED to the picture it belongs to so that a section or a zoom sits on the
    SAME slide, next to its parent, instead of on a slide of its own (council item A4)."""
    by_id = {i["id"]: i for i in infos}
    groups = {}
    for i in infos:
        if i["role"] in CASE_ROLES and i.get("case"):
            base = i["role"][2:] if i["role"].startswith("x_") else i["role"]
            groups.setdefault(i["case"], {}).setdefault(base, []).append(i)
    out = []
    for n in sorted(groups):
        g = groups[n]
        c = {"n": n, "bc_id": None, "def_id": None, "stress_id": None, "extras": [], "demoted": [],
             "deck": "", "letter": None, "analysis": ""}
        for base, slot in MAIN_SLOTS:
            mains = [p for p in g.get(base, []) if p["role"] == base]
            if len(mains) > 1:
                mains.sort(key=lambda p: (not p.get("user_set"), 0.0 if base == "bc" else -(p.get("max") or 0.0), p["id"]))
                for p in mains[1:]:
                    p["role"] = "x_" + base
                    p["auto_extra"] = True
                    c["demoted"].append((mains[0]["id"], p["id"], base))
            if mains:
                c[slot] = mains[0]["id"]
        for p in infos:
            if p["role"] in ("x_bc", "x_deformation", "x_stress", "x_other") and p.get("case") == n:
                pid, why, reg = _parent_of(p, c, by_id)
                if reg and reg.get("id") is not None:
                    pid = reg["id"]
                pname = by_id[pid]["name"] if pid in by_id else ""
                vk = p.get("view_kind") or ""
                attach = p.get("attach")
                if attach is None:
                    # a zoom / section belongs on its parent's slide; a whole other result plot gets a slide of its own
                    # unless it is a named view of the same plot.  The setup slide has no room, so it never takes one.
                    attach = bool(vk) or (p["role"] in ("x_stress", "x_deformation"))
                    attach = attach and (by_id.get(pid, {}).get("role") != "bc")
                p["attach"], p["parent"] = bool(attach), pid
                c["extras"].append({
                    "id": p["id"], "kind": p["role"][2:], "heading": extra_heading(p), "max": p.get("max"),
                    "unit": p.get("unit"), "auto": bool(p.get("auto_extra")), "rkind": p.get("rkind") or "",
                    "result_kind": p.get("result_kind") or "", "view_kind": vk, "parent": pid, "parent_name": pname,
                    "attach": bool(p["attach"]), "pos": p.get("pos") or "auto", "parent_why": why,
                    "match": ({k: reg[k] for k in ("score", "box", "side", "row", "cx", "cy", "name") if k in reg}
                              if reg else None),
                    "ambiguous": bool(p.get("match_ambiguous")),
                })
        bc, d, s = by_id.get(c["bc_id"]), by_id.get(c["def_id"]), by_id.get(c["stress_id"])
        name, sub = (bc["headline_bc"] if bc else ("", ""))
        pxs = [x["legend_px"] for x in (bc, d, s) if x and x.get("legend_px")]
        anchor = bc or d or s or {}
        c.update(
            name=name or f"Load Case {n}", subtitle=sub or "(Self Weight + Pressure)", short_name=name or f"Load Case {n}",
            bc_items=[x["text"] for x in bc["bc_draft"]] if bc else [], bc_legend=[x["legend"] for x in bc["bc_draft"]] if bc else [],
            notes=[], def_unit=(d or {}).get("unit") or "mm", stress_unit=(s or {}).get("unit") or "MPa",
            def_max=d["max"] if d else None, stress_max=s["max"] if s else None,
            stress_red_px=s["red_px"] if s else None, stress_legend=s["legend_values"] if s else [],
            gravity=(bc or {}).get("gravity"), legend_px=(statistics.median(pxs) if pxs else None),
            letter=anchor.get("letter"), model=anchor.get("model"), deck=anchor.get("deck") or "",
            deck_id=anchor.get("deck_id"), analysis=anchor.get("analysis") or "")
        out.append(c)
    return out


def guess_cover(infos, settings=None):
    settings = settings or {}
    names = [i["model"] for i in infos if i.get("model")]
    model = max(names, key=lambda s: (s.count("_"), len(s))) if names else ""
    toks = [t for t in re.split(r"[_\s]+", model) if t and "+" not in t and t.lower() not in LOAD_WORDS]
    job = client = obj = ""
    if len(toks) >= 3 and toks[0].isdigit():
        job, client, obj = toks[0], toks[1], " ".join(toks[2:])
    today = _dt.date.today()
    d = today.day
    suffix = "th" if 10 <= d % 100 <= 20 else {1: "st", 2: "nd", 3: "rd"}.get(d % 10, "th")
    pattern = settings.get("report_no_pattern", "MWI/FEA/ACK-{job}/01")
    return {
        "title": f"Static Structural Analysis Of {obj}" if obj else "Static Structural Analysis Of ",
        "short": obj.split()[-1] if obj else "",
        "report_no": pattern.format(job=job) if job else "",
        "date": f"{d}{suffix} {MONTHS[today.month - 1]} {today.year}",
        "client": client,
        "model": model,
    }


def finish(infos, settings=None):
    """Case numbers, geometry headings, drafts and static warnings for a list of analysed pictures.

    The load case is taken from the ANSYS TITLE BLOCK, in this order (council item A3):
      1. the deck - which ANSYS project / model the picture was taken from (fuzzy, so an OCR mistake or a slightly
         different spelling of the same model still lands in the same deck);
      2. inside a deck, the letter ANSYS prints ("A:", "B:" ...) - which is the load case;
      3. a picture with no readable letter joins the case of its deck whose title it resembles most;
      4. only when that is impossible is it left for the engineer (and the page says so).
    Two projects that both call their case "A" therefore never end up in one load case."""
    for i in infos:
        i["case"] = None
        i.pop("case_uncertain", None)
    case_infos = [i for i in infos if i["role"] in CASE_ROLES and i.get("dup_of") is None]
    decks = cluster_decks(case_infos)
    cases = []
    for d in decks:
        members = d["members"]
        letters = sorted({i["letter"] for i in members if i["letter"]})
        by_letter = {}
        for i in members:
            if i["letter"] in letters:
                by_letter.setdefault(i["letter"], []).append(i)
            elif not letters or len(letters) == 1:
                by_letter.setdefault(letters[0] if letters else "", []).append(i)
            else:                                   # no letter, several cases: follow the closest title in this deck
                best, bs = None, 0.0
                for L, g in by_letter.items():
                    for m in g:
                        s = max(deck_sim(i.get("model") or "", m.get("model") or ""),
                                sim(i.get("headline") or "", m.get("headline") or "") * 0.9)
                        if s > bs:
                            best, bs = L, s
                if best is not None and bs >= 0.55:
                    by_letter[best].append(i)
                    i["notes"].append(f"No load-case letter was readable in the title, so this picture was put with case '{best}' "
                                      f"(the titles are {bs * 100:.0f} % alike). Check it.")
                else:
                    i["case_uncertain"] = "letter"   # the engineer assigns it (warned in build_draft)
        for L in sorted(by_letter):
            g = by_letter[L]
            bcs = sorted([i for i in g if i["role"] == "bc"], key=lambda i: (_norm_model(i["model"]), i["id"]))
            if len(bcs) < 2:
                cases.append(g)
                continue
            # two setup pictures with the same letter inside one deck: still two models - every other picture follows
            # the setup picture whose model name it resembles most
            sub = [[b] for b in bcs]
            for i in g:
                if i["role"] == "bc":
                    continue
                best = max(range(len(bcs)), key=lambda k: (deck_sim(i.get("model") or "", bcs[k].get("model") or ""),
                                                           _common_prefix(_norm_model(i["model"]), _norm_model(bcs[k]["model"])), -k))
                sub[best].append(i)
            cases.extend(sub)
    for n, g in enumerate(cases, 1):
        for i in g:
            i["case"] = n
    return build_draft(infos, settings)


def _views(infos, role):
    return [i for i in infos if i["role"] == role]


def attach_geometry_views(infos, group, settings=None):
    """Geometry / mesh views: a view whose file name (or the engineer's choice) says 'section' / 'zoom' is attached to
    the view it belongs to, so it lands on the same slide instead of adding one.  -> {parent_id: [child infos]}"""
    out = {}
    if len(group) < 2:
        return out
    def hinted(i):
        return bool(i.get("view_kind") or file_hint(i["name"]))
    mains = [i for i in group if not hinted(i)]
    if not mains:
        mains = group[:1]
    for i in group:
        if not hinted(i) or i in mains:
            continue
        pid = i.get("parent")
        if pid is None or pid not in [m["id"] for m in group]:
            reg = _region(i, {x["id"]: x for x in infos}, mains)
            pid = reg["id"] if reg else mains[0]["id"]
            i["parent"] = pid
        i["attach"] = True
        out.setdefault(pid, []).append(i)
    return out


def build_draft(infos, settings=None):
    """Everything the web page needs, derived from the CURRENT role/case of every picture."""
    by_id = {i["id"]: i for i in infos}
    cases = assign_cases(infos)
    geo, meshes = _views(infos, "geometry"), _views(infos, "mesh")
    heads = {}
    for i in geo:
        h = {"up": "Top view", "down": "Bottom view"}.get(i.get("z_dir"), "")
        vk = i.get("view_kind") or file_hint(i["name"])
        if vk:
            h = f"{'Section' if vk == 'section' else 'Detail'} view {PLACEHOLDER}"
        heads[i["id"]] = h
    for k, i in enumerate(geo, 1):                              # blank or repeated heading -> "View k"
        h = heads[i["id"]]
        if not h or list(heads.values()).count(h) > 1:
            heads[i["id"]] = f"View {k}"
    geo.sort(key=lambda i: {"Top view": 0, "Bottom view": 1}.get(heads[i["id"]], 2))        # top view first
    mheads = {}
    for k, i in enumerate(meshes):
        vk = i.get("view_kind") or file_hint(i["name"])
        mheads[i["id"]] = "Overall mesh" if k == 0 and not vk else (
            f"{'Mesh section' if vk == 'section' else 'Mesh detail'} {PLACEHOLDER}" if vk else "Mesh detail [describe - please confirm]")
    geo_attach = attach_geometry_views(infos, geo, settings)
    mesh_attach = attach_geometry_views(infos, meshes, settings)
    decks = cluster_decks([i for i in infos if i["role"] in CASE_ROLES and i.get("dup_of") is None])

    warns = []
    for i in infos:
        for n in i["notes"]:
            warns.append({"level": "warn", "text": f"{i['name']}: {n}"})
        if i.get("dup_of") is not None:
            first = next((x["name"] for x in infos if x["id"] == i["dup_of"]), "another picture")
            warns.append({"level": "info", "text": f"{i['name']} is the same picture as {first} - used once."})
        if i["role"] in CASE_ROLES and not i.get("case"):
            why = ("it is not clear which ANSYS deck it belongs to" if i.get("case_uncertain") == "deck"
                   else "could not tell which load case it belongs to")
            warns.append({"level": "warn", "text": f"{i['name']}: {why} - choose the case in the table."})
    label = {"bc": "setup", "deformation": "total-deformation", "stress": "von-Mises stress"}
    decks_used = sorted({c["deck"] for c in cases if c.get("deck")})
    if len(decks_used) > 1:
        warns.append({"level": "info", "text": f"The pictures come from {len(decks_used)} different ANSYS decks "
                                               f"({', '.join(repr(d) for d in decks_used)}). The load cases are numbered deck by deck, "
                                               f"so two cases that ANSYS both calls 'A' stay apart."})
    for c in cases:
        for key, name in (("bc_id", "setup (boundary-condition) picture"), ("def_id", "total-deformation picture"),
                          ("stress_id", "von-Mises stress picture")):
            if c[key] is None:
                warns.append({"level": "info", "text": f"Case {c['n']} has no {name} - that is fine: the report carries on without it."})
        for main_id, x_id, base in c["demoted"]:
            m, x = by_id[main_id], by_id[x_id]
            extra = f" (Max {m['max']:g})" if base != "bc" and m.get("max") is not None else ""
            warns.append({"level": "info", "text": f"Case {c['n']}: two {label[base]} pictures - {m['name']}{extra} is the main one, "
                                                   f"{x['name']} is used as an additional view on the same slide. Swap them in the table if that is wrong."})
        for e in c["extras"]:
            nm, mm, pn = e.get("name") or by_id[e["id"]]["name"], e.get("match"), e.get("parent_name") or ""
            if e.get("ambiguous"):
                warns.append({"level": "warn", "text": f"Case {c['n']}: '{nm}' looks like a magnified part of more than one picture "
                                                       f"of this case - check which picture it is attached to."})
            elif mm and e.get("attach"):
                where = {"left": "on the left", "right": "on the right"}[mm.get("side", "right")] + \
                        (", upper half" if mm.get("row") == "top" else ", lower half")
                warns.append({"level": "ok", "text": f"Case {c['n']}: '{nm}' was found inside '{pn}' ({where}, {mm['score'] * 100:.0f} % alike) - "
                                                     f"it is placed on the same slide, next to it, and the region is marked."})
            elif e.get("attach") and e.get("view_kind") == "detail" and not mm:
                warns.append({"level": "info", "text": f"Case {c['n']}: '{nm}' is a zoomed view, but I could not find that region inside "
                                                       f"'{pn}' - it is placed on the same slide without a marker. Check that it belongs to this picture."})
    if not geo:
        warns.append({"level": "info", "text": "No geometry picture - that is fine: the geometry slide is simply left out (or add one in the pictures table)."})
    if not meshes:
        warns.append({"level": "info", "text": "No mesh picture - that is fine: the mesh slide is simply left out (or add one in the pictures table)."})
    if not cases:
        warns.append({"level": "info", "text": "No load-case pictures (setup / deformation / stress) were recognised - the report would hold the cover, "
                                               "material, geometry and mesh slides only. Use the pictures table if some pictures were not recognised."})
    return {"cases": cases,
            "geometry": [{"id": i["id"], "heading": heads[i["id"]], "parent": i.get("parent"),
                          "attach": bool(geo_attach.get(i["id"]) or (i.get("parent") in geo_attach))} for i in geo],
            "meshes": [{"id": i["id"], "heading": mheads[i["id"]], "parent": i.get("parent"),
                        "attach": bool(mesh_attach.get(i["id"]) or (i.get("parent") in mesh_attach))} for i in meshes],
            "decks": [{"n": d.get("n"), "name": d["name"], "pictures": [m["name"] for m in d["members"]]} for d in decks],
            "warnings": warns}


MANUAL_NOTE = ("The picture reader (Tesseract) is not installed, so I could not read the numbers from the pictures. "
               "The types were guessed from the file names - please check them and type the Max values and the setup lines. "
               "To read the pictures automatically, install Tesseract once (step 1B in README.txt).")


def analyze(paths, settings=None, workers=4, names=None, force_manual=False) -> dict:
    """paths -> {images, cover, + draft}.  Order of the files does not matter.
    Never blocks: without the picture reader (OCR) the pictures are sorted by file name and the numbers are typed by hand;
    a picture that fails to read is handled the same way while all the others are read normally."""
    names = names or [None] * len(paths)
    ocr = (not force_manual) and ocr_available()

    def one(a):
        idx, path = a
        if not ocr:
            return analyze_manual_one(path, idx, names[idx], "ocr")
        _TLS.deadline = time.monotonic() + OCR_PICTURE_BUDGET
        try:
            info = analyze_one(path, idx, names[idx])
            if time.monotonic() > _TLS.deadline:                               # ran out of time: what was read is not reliable
                return analyze_manual_one(path, idx, names[idx], "it took too long")
            return info
        except Exception as e:                                                 # noqa: BLE001
            msg = str(e).lower()
            why = "it took too long" if (isinstance(e, TimeoutError) or "timeout" in msg or "too long" in msg) else type(e).__name__
            return analyze_manual_one(path, idx, names[idx], why)
        finally:
            _TLS.deadline = None

    with ThreadPoolExecutor(max_workers=workers) as pool:
        infos = list(pool.map(one, enumerate(paths)))
    first = {}
    for i in infos:                                             # the same file twice -> use it once
        if i["hash"] in first:
            i["role"], i["dup_of"], i["case"] = "unused", first[i["hash"]], None
        else:
            first[i["hash"]] = i["id"]
    draft = finish(infos, settings)
    if not ocr:
        draft["warnings"].insert(0, {"level": "warn", "text": MANUAL_NOTE})
    return {"images": infos, "cover": guess_cover(infos, settings), "manual": not ocr, **draft}


if __name__ == "__main__":                              # quick command-line test:  python analyzer.py pic1.png pic2.png ...
    import json
    import sys
    res = analyze(sys.argv[1:])
    for i in res["images"]:
        print(f"{i['name']:28} {i['role']:13} case={i['case']} letter={i['letter']} max={i['max']} {i['unit'] or ''} "
              f"legend_px={i['legend_px']} notes={i['notes']}")
    print(json.dumps({k: res[k] for k in ("cover", "cases", "geometry", "meshes", "warnings")}, indent=1, default=str)[:3500])
