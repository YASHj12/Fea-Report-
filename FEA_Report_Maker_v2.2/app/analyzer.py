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


# ═════════════════════════════════════════════ parsing helpers ═════════════════════════════════════════════
def _clean_kind(txt: str) -> str:
    t = re.sub(r"[^A-Za-z0-9 ()/.+\-]", "", txt)
    return re.sub(r"\s+", " ", t).strip(" -.")[:60]


def parse_header(lines):
    """ANSYS title block -> letter, model, role, unit, result kind.
    Result plots carry 'Type:' and 'Unit:' lines, the setup picture does not.  Only 'Total Deformation' and
    'Equivalent (von-Mises) Stress' are the MAIN plots; every other result type (principal stress, directional
    deformation, safety factor, strain ...) becomes an additional view - it must never be mistaken for the von-Mises plot."""
    out = {"letter": None, "model": None, "role": None, "unit": None, "has_header": False, "is_result": False, "result_kind": ""}
    for l in lines[:3]:
        m = re.match(r"^\W*([A-Z])\s*[:;]\s*(\S.*)$", l)
        if m:
            out["letter"], out["model"] = m.group(1), re.sub(r"(\s+[a-z]{1,2})+$", "", m.group(2).strip())
            out["has_header"] = True
            break
    joined = " ".join(lines)
    type_txt = ""
    for l in lines[:7]:
        m = re.match(r"^\W*Type\s*[:;]\s*(.+)$", l, re.I)
        if m:
            type_txt = m.group(1).strip()
            break
    mu = re.search(r"Unit\s*[:;]\s*([A-Za-z/²^0-9]+)", joined)
    out["unit"] = mu.group(1) if mu else None
    out["is_result"] = bool(type_txt or mu)
    name_line = lines[1] if len(lines) > 1 else ""
    src = f"{type_txt} | {name_line}".lower()
    deform = "total deformation" in src
    vm = bool(re.search(r"von[- ]?mises|equivalent\s*(\(.*?\))?\s*stress", src))
    other = re.search(r"deformation|stress|strain|safety factor|displacement|reaction|energy|fatigue|damage|shear|principal|intensity", src)
    static = re.search(r"static structural|transient structural|steady-state|modal|harmonic|buckling", " ".join(lines[:3]).lower())
    if deform:
        out["role"] = "deformation"
    elif vm:
        out["role"] = "stress"
    elif out["is_result"] or (other and not static):
        out["role"] = "x_other"
        out["result_kind"] = _clean_kind(type_txt or name_line) or "Other result"
    elif static:
        out["role"] = "bc"
    return out


def file_hint(name: str):
    """'section' / 'detail' / None - from the file name only (there is no reliable cue in the pixels)."""
    n = Path(name).stem.lower()
    if re.search(r"(?<![a-z])(section|sect|cut|cross)", n):
        return "section"
    if re.search(r"(?<![a-z])(detail|zoom|close|hot[-_ ]?spot|local|enlarg)", n):
        return "detail"
    return None


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
                    headline=list(case_headline(legend)), gravity=gravity_of(legend))
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
    if role in ("bc", "deformation", "stress") and file_hint(nm):      # "..._section" / "..._detail": an additional view, not the main plot
        role = "x_" + role
    info = {"id": idx, "name": nm, "path": str(path), "width": W0, "height": H0, "role": role or "unknown",
            "confidence": "medium" if role else "low", "letter": (chr(64 + case_no) if (case_no and role in CASE_ROLES) else None),
            "model": None, "header": [], "notes": [], "unit": None, "max": None, "min": None, "legend_values": [],
            "bc_legend": [], "bc_draft": [], "headline": ["", ""], "z_dir": None, "red_px": None, "edge_density": None,
            "result_kind": "", "legend_px": None, "gravity": None, "dup_of": None, "user_set": False, "manual": True,
            "hash": hashlib.md5(Path(path).read_bytes()).hexdigest()}
    if role is None:
        info["notes"].append("I could not tell what this picture is - please choose it in the list." if why == "ocr"
                             else f"This picture could not be read automatically ({why}) - please choose what it is.")
    elif why != "ocr":
        info["notes"].append(f"This picture could not be read automatically ({why}) - its type was guessed from the file name; please check it and type the numbers.")
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
            "confidence": "high", "letter": None, "model": None, "header": [], "notes": [], "unit": None,
            "max": None, "min": None, "legend_values": [], "bc_legend": [], "bc_draft": [], "headline": ["", ""],
            "z_dir": None, "red_px": None, "edge_density": None, "result_kind": "", "legend_px": None, "gravity": None,
            "hash": hashlib.md5(Path(path).read_bytes()).hexdigest(), "dup_of": None, "user_set": False}

    for hbox in candidate_boxes(img, HEADER_BOX, HEADER_PX):
        hdr_lines = _lines(ocr_region(img, *hbox))
        h = parse_header(hdr_lines)
        if h["has_header"] or h["role"]:
            break
    info.update(letter=h["letter"], model=h["model"], header=hdr_lines[:5], unit=h["unit"], result_kind=h["result_kind"])

    if h["role"]:
        info["role"] = h["role"]
        if h["role"] == "x_other":
            info["confidence"] = "medium"
            info["notes"].append(f"Result type '{h['result_kind']}' is not Total Deformation or Equivalent (von-Mises) Stress, "
                                 f"so it is used as an additional view. Change it in the table if that is wrong.")
    elif h["has_header"]:
        info["role"] = "unknown"
        info["notes"].append("Title not recognised - please choose what this picture is.")
    else:                                              # no ANSYS title -> plain model picture
        ed = edge_density(img)
        info["edge_density"] = round(ed, 3)
        info["role"] = "mesh" if ed > MESH_EDGE_THRESHOLD else "geometry"
        info["confidence"] = "medium"
    extract_role_data(img, info, info["role"], k_an)
    return info


def regroup(infos, assignments, settings=None):
    """Apply the user's corrections {id: (role, case)} and return the new draft."""
    by_id = {i["id"]: i for i in infos}
    for a in assignments:
        i = by_id.get(a["id"])
        if not i:
            continue
        role = a["role"]
        case = a.get("case") if role in CASE_ROLES else None
        if case is None and role in CASE_ROLES:
            case = i.get("case") or 1
        if role != i["role"] or case != i.get("case"):
            i["user_set"] = True
        if role != i["role"]:
            base = role[2:] if role.startswith("x_") else role
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
            i["notes"] = [n for n in i["notes"] if "recognised" not in n and "additional view" not in n]
            i["confidence"] = "high" if i["confidence"] != "low" else "low"
        i["role"], i["case"] = role, case
    return build_draft(infos, settings)


# ═════════════════════════════════════════════ whole set ═════════════════════════════════════════════
def _norm_model(m):
    return re.sub(r"[_\s]+", " ", (m or "").lower()).strip()


def _common_prefix(a, b):
    n = 0
    for x, y in zip(a, b):
        if x != y:
            break
        n += 1
    return n


def extra_heading(info):
    """Heading of an additional view - stays yellow until the engineer states WHERE (section line, detail position)."""
    if info["role"] == "x_other" and info.get("result_kind"):
        return info["result_kind"]
    hint = file_hint(info["name"])
    if hint == "section":
        return f"Section view {PLACEHOLDER}"
    if hint == "detail":
        return f"Detail view {PLACEHOLDER}"
    return "Additional view [describe - please confirm]"


def assign_cases(infos):
    """Group pictures into load cases -> list of case drafts (one per case number).
    A case has ONE main setup / deformation / stress picture (the one with the highest Max wins); further pictures of the
    same kind become additional views (section, detail ...)."""
    by_id = {i["id"]: i for i in infos}
    groups = {}
    for i in infos:
        if i["role"] in CASE_ROLES and i.get("case"):
            base = i["role"][2:] if i["role"].startswith("x_") else i["role"]
            groups.setdefault(i["case"], {}).setdefault(base, []).append(i)
    out = []
    for n in sorted(groups):
        g = groups[n]
        c = {"n": n, "bc_id": None, "def_id": None, "stress_id": None, "extras": [], "demoted": []}
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
                c["extras"].append({"id": p["id"], "kind": p["role"][2:], "heading": extra_heading(p), "max": p.get("max"),
                                    "unit": p.get("unit"), "auto": bool(p.get("auto_extra"))})
        bc, d, s = by_id.get(c["bc_id"]), by_id.get(c["def_id"]), by_id.get(c["stress_id"])
        name, sub = (bc["headline"] if bc else ("", ""))
        pxs = [x["legend_px"] for x in (bc, d, s) if x and x.get("legend_px")]
        c.update(
            name=name or f"Load Case {n}", subtitle=sub or "(Self Weight + Pressure)", short_name=name or f"Load Case {n}",
            bc_items=[x["text"] for x in bc["bc_draft"]] if bc else [], bc_legend=[x["legend"] for x in bc["bc_draft"]] if bc else [],
            notes=[], def_unit=(d or {}).get("unit") or "mm", stress_unit=(s or {}).get("unit") or "MPa",
            def_max=d["max"] if d else None, stress_max=s["max"] if s else None,
            stress_red_px=s["red_px"] if s else None, stress_legend=s["legend_values"] if s else [],
            gravity=(bc or {}).get("gravity"), legend_px=(statistics.median(pxs) if pxs else None),
            letter=(bc or d or s or {}).get("letter"), model=(bc or d or s or {}).get("model"))
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
    """Case numbers, geometry headings, drafts and static warnings for a list of analysed pictures."""
    for i in infos:
        i["case"] = None
    case_infos = [i for i in infos if i["role"] in CASE_ROLES]
    letters = sorted({i["letter"] for i in case_infos if i["letter"]})
    groups = {}
    for i in case_infos:
        if i["letter"] in letters:
            groups.setdefault(i["letter"], []).append(i)
        elif not letters or len(letters) == 1:
            groups.setdefault(letters[0] if letters else "", []).append(i)
        # else: no letter and several cases -> the engineer assigns it (warned in build_draft)
    cases = []
    for L in sorted(groups):
        g = groups[L]
        bcs = sorted([i for i in g if i["role"] == "bc"], key=lambda i: (_norm_model(i["model"]), i["id"]))
        if len(bcs) < 2:
            cases.append(g)
            continue
        # two setup pictures with the same letter = two separate ANSYS projects that both call themselves "A":
        # every other picture follows the setup picture whose model name it resembles most
        sub = [[b] for b in bcs]
        for i in g:
            if i["role"] == "bc":
                continue
            best = max(range(len(bcs)), key=lambda k: (_common_prefix(_norm_model(i["model"]), _norm_model(bcs[k]["model"])), -k))
            sub[best].append(i)
        cases.extend(sub)
    for n, g in enumerate(cases, 1):
        for i in g:
            i["case"] = n
    return build_draft(infos, settings)


def _views(infos, role):
    return [i for i in infos if i["role"] == role]


def build_draft(infos, settings=None):
    """Everything the web page needs, derived from the CURRENT role/case of every picture."""
    cases = assign_cases(infos)
    geo, meshes = _views(infos, "geometry"), _views(infos, "mesh")
    heads = {}
    for i in geo:
        h = {"up": "Top view", "down": "Bottom view"}.get(i.get("z_dir"), "")
        hint = file_hint(i["name"])
        if hint:
            h = f"{'Section' if hint == 'section' else 'Detail'} view {PLACEHOLDER}"
        heads[i["id"]] = h
    for k, i in enumerate(geo, 1):                              # blank or repeated heading -> "View k"
        h = heads[i["id"]]
        if not h or list(heads.values()).count(h) > 1:
            heads[i["id"]] = f"View {k}"
    geo.sort(key=lambda i: {"Top view": 0, "Bottom view": 1}.get(heads[i["id"]], 2))        # top view first, whatever the upload order
    mheads = {}
    for k, i in enumerate(meshes):
        hint = file_hint(i["name"])
        mheads[i["id"]] = "Overall mesh" if k == 0 and not hint else (
            f"{'Mesh section' if hint == 'section' else 'Mesh detail'} {PLACEHOLDER}" if hint else "Mesh detail [describe - please confirm]")

    warns = []
    for i in infos:
        for n in i["notes"]:
            warns.append({"level": "warn", "text": f"{i['name']}: {n}"})
        if i.get("dup_of") is not None:
            first = next((x["name"] for x in infos if x["id"] == i["dup_of"]), "another picture")
            warns.append({"level": "info", "text": f"{i['name']} is the same picture as {first} - used once."})
        if i["role"] in CASE_ROLES and not i.get("case"):
            warns.append({"level": "warn", "text": f"{i['name']}: could not tell which load case it belongs to - choose the case in the table."})
    label = {"bc": "setup", "deformation": "total-deformation", "stress": "von-Mises stress"}
    for c in cases:
        for key, name in (("bc_id", "setup (boundary-condition) picture"), ("def_id", "total-deformation picture"),
                          ("stress_id", "von-Mises stress picture")):
            if c[key] is None:
                warns.append({"level": "info", "text": f"Case {c['n']} has no {name} - that is fine: the report carries on without it."})
        for main_id, x_id, base in c["demoted"]:
            m = next(x for x in infos if x["id"] == main_id)
            x = next(x for x in infos if x["id"] == x_id)
            extra = f" (Max {m['max']:g})" if base != "bc" and m.get("max") is not None else ""
            warns.append({"level": "info", "text": f"Case {c['n']}: two {label[base]} pictures - {m['name']}{extra} is the main one, "
                                                   f"{x['name']} is used as an additional view. Swap them in the table if that is wrong."})
    if not geo:
        warns.append({"level": "info", "text": "No geometry picture - that is fine: the geometry slide is simply left out (or add one in the pictures table)."})
    if not meshes:
        warns.append({"level": "info", "text": "No mesh picture - that is fine: the mesh slide is simply left out (or add one in the pictures table)."})
    if not cases:
        warns.append({"level": "info", "text": "No load-case pictures (setup / deformation / stress) were recognised - the report would hold the cover, "
                                               "material, geometry and mesh slides only. Use the pictures table if some pictures were not recognised."})
    return {"cases": cases, "geometry": [{"id": i["id"], "heading": heads[i["id"]]} for i in geo],
            "meshes": [{"id": i["id"], "heading": mheads[i["id"]]} for i in meshes], "warnings": warns}


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
