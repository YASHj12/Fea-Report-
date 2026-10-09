#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Round-7 council checks (version 2.3) - the seven changes the engineer asked for, proven end to end.

Run:  python tests/test_v23.py            (offline; uses the examples folder)

U1  the preview is NOT rebuilt by itself (scheduleBuild only marks it stale) - checked in the page, see browser_ui_v23
U2  pictures can be added folder by folder (/api/analyze_more keeps everything typed)
A2  every ANSYS result type has its own entry, and keeps its own name + unit in the report
A3  the load case comes from the ANSYS title block; two decks that both say "A" stay apart
A4  a zoomed / section view is found INSIDE its parent picture and drawn on the SAME slide, region marked
"""
import json
import shutil
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "app"))
sys.path.insert(0, str(ROOT / "engine"))

import server                                     # noqa: E402
import analyzer                                   # noqa: E402
import mw_report as mw                            # noqa: E402
import pptx_preview as pv                         # noqa: E402
from PIL import Image                             # noqa: E402

EX = ROOT / "examples"
FAILS = []


def check(name, ok, detail=""):
    print(("  PASS  " if ok else "  FAIL  ") + name + (f"   {detail}" if detail and not ok else ""))
    if not ok:
        FAILS.append(name)


def zoom_of(path, box=(0.60, 0.22, 0.90, 0.46), zoom=2.0):
    im = analyzer.load_rgb(path)
    W, H = im.size
    b = (int(box[0] * W), int(box[1] * H), int(box[2] * W), int(box[3] * H))
    c = im.crop(b)
    out = tempfile.mktemp(suffix=".png")
    c.resize((int(c.width * zoom), int(c.height * zoom)), Image.LANCZOS).save(out)
    return out


def set_roles(sid, roles):
    infos = server.load_infos(sid)
    for i in infos:
        r = roles.get(i["name"])
        if not r:
            continue
        i.update(role=r["role"], case=r.get("case"), letter=r.get("letter"), model=r.get("model"),
                 deck=analyzer.deck_key(r.get("model") or ""), view_kind=r.get("view_kind", ""),
                 rkind=r.get("rkind", ""), max=r.get("max"), unit=r.get("unit"),
                 legend_values=r.get("legend_values") or [], letter_conf="high", headline=r.get("headline", ""),
                 user_set=True, confidence="high")
        if r["role"] == "bc":
            i["bc_draft"] = [{"legend": "B: Pressure: 9.8e-3 MPa", "text": "Design pressure 1000 mmWC applied which is 0.0098 MPa.",
                              "needs_input": False}]
            i["bc_legend"] = ["B: Pressure: 9.8e-3 MPa"]
            i["headline_bc"] = ["Pressure Only", "(Pressure)"]
    server.save_infos(sid, infos)
    return infos


def payload_for(sid, infos, cases):
    return {
        "sid": sid, "cover": {"title": "Static Structural Analysis Of Test Plate", "report_no": "MWI/FEA/T-99/01",
                              "date": "9th Oct 2026", "client": "Test"},
        "short": "plate", "fos": 1.3, "yield": 250, "allowable": 192, "show_utilisation": True, "def_limit": None,
        "material": {"rows": [["IS 2062", "Plate", "191400", "0.3", "7.8E-9", "250", "192"]],
                     "units": "Units: Length = mm, Mass = tonnes, Time = second, Force = Newton"},
        "geometry": [{"id": i["id"], "heading": "Isometric view", "caption": "", "parent": None, "attach": False}
                     for i in infos if i["role"] == "geometry"],
        "meshes": [], "mesh_stats": {}, "assumptions": [], "layouts": {"results": "auto", "geometry": "auto", "bc": "auto", "extras": "auto"},
        "cases": cases, "missing": "skip",
    }


def case_payload(c, extras):
    return {"n": c["n"], "name": c["name"], "subtitle": c["subtitle"], "short_name": c["short_name"],
            "bc_id": c["bc_id"], "def_id": c["def_id"], "stress_id": c["stress_id"],
            "bc_items": c["bc_items"], "notes": [], "def_max": c["def_max"], "stress_max": c["stress_max"],
            "basis": "", "sing_loc": "", "stress_away": "", "loc_stress": "", "loc_def": "",
            "react_applied": "", "react_sum": "", "layout": "", "bc_layout": "", "legend_px": 9.6,
            "extras": extras, "texts": {}}


def main():
    cli = server.app.test_client()
    tmp = Path(tempfile.mkdtemp(prefix="fea_t23_"))

    # ── build a small synthetic job: one deck, one case, a zoom of the stress plot ──
    zoom = zoom_of(EX / "image-3.png")
    shutil.copy(zoom, tmp / "stress_zooom_view.png")          # a misspelled name on purpose
    srcs = [(EX / "image-9.png", "image-9.png"), (EX / "image-1.png", "image-1.png"), (EX / "image-3.png", "image-3.png"),
            (tmp / "stress_zooom_view.png", "stress_zooom_view.png"), (EX / "image-5.png", "image-5.png")]
    data = {"files": [(open(pth, "rb"), nm) for pth, nm in srcs]}
    r = cli.post("/api/analyze", data=data, content_type="multipart/form-data")
    check("analyze answers", r.status_code == 200, r.get_data(as_text=True)[:200])
    sid = r.get_json()["sid"]
    infos = server.load_infos(sid)
    by_name = {i["name"]: i for i in infos}

    # ── A3: title blocks decide the case; two decks that both say "A" must stay apart ──
    hdr = analyzer.parse_header(["A: 6203_KCP_MLD_Blade_PRESSURE", "Total Deformation", "Type: Total Deformation",
                                 "Unit: mm", "Time: 1 s"])
    check("A3 header reads deck + letter", hdr["letter"] == "A" and hdr["deck"].startswith("6203 kcp mld blade"))
    hdr2 = analyzer.parse_header(["A: 7101_SILVERTONE_GATE", "Static Structural"])
    check("A3 same letter, other deck", analyzer.deck_sim(hdr["deck"], hdr2["deck"]) < 0.5)
    fake = [dict(id=k, name=f"p{k}", path="", role=rl, letter=lt, model=mo, deck=analyzer.deck_key(mo), notes=[],
                 max=None, legend_values=[], bc_draft=[], bc_legend=[], headline_bc=["", ""], unit=None,
                 legend_px=None, red_px=None, edge_density=None, z_dir=None, gravity=None, dup_of=None,
                 user_set=False, view_kind="", rkind="", result_kind="", parent=None, attach=None, pos="auto",
                 match=None, match_ambiguous=False)
            for k, (rl, lt, mo) in enumerate([("bc", "A", "2401_GATE"), ("deformation", "A", "2401_GATE"),
                                              ("bc", "A", "6203_BLADE"), ("stress", "A", "6203_BLADE")], 1)]
    d = analyzer.finish(fake)
    decks = {i["case"]: i["deck"] for i in fake}
    check("A3 two decks both called A stay apart", len(d["cases"]) == 2 and decks[1] != decks[2], str(decks))

    # ── A2b: a file name that merely mentions "structural" / "thermal" is not a result type ──
    r = cli.post("/api/analyze", data={"files": [(open(EX / "image-5.png", "rb"), "SETUP STRUCTURAL.png"),
                                                 (open(EX / "image-7.png", "rb"), "thermal setup.png")]},
                 content_type="multipart/form-data")
    js = r.get_json()
    check("A2b setup pictures keep role bc and no result kind",
          all(i["role"] == "bc" and not i["rkind"] for i in js["images"]),
          str([(i["name"], i["role"], i["rkind"]) for i in js["images"]]))

    # ── A4: the zoom is found inside its parent, and attached to it ──
    m = analyzer.match_region(str(EX / "image-3.png"), str(tmp / "stress_zooom_view.png"))
    check("A4 zoom located inside its parent", bool(m) and m["score"] > 0.7, str(m))
    m2 = analyzer.match_region(str(EX / "image-9.png"), str(tmp / "stress_zooom_view.png"))
    check("A4 an unrelated picture scores lower", (m2 is None) or m2["score"] < m["score"], f"{m2} vs {m}")

    set_roles(sid, {
        "image-9.png": {"role": "geometry"},
        "image-1.png": {"role": "deformation", "case": 1, "letter": "A", "model": "6203_KCP_MLD_Blade_PRESSURE",
                        "max": 2.42, "unit": "mm", "legend_values": [2.42, 2.0, 1.5]},
        "image-3.png": {"role": "stress", "case": 1, "letter": "A", "model": "6203_KCP_MLD_Blade_PRESSURE",
                        "max": 120.0, "unit": "MPa", "legend_values": [120, 100, 80]},
        "image-5.png": {"role": "bc", "case": 1, "letter": "A", "model": "6203_KCP_MLD_Blade_PRESSURE"},
        "stress_zooom_view.png": {"role": "x_stress", "case": 1, "letter": "A", "model": "6203_KCP_MLD_Blade_PRESSURE",
                                  "view_kind": "detail", "max": 96.0, "unit": "MPa"},
    })
    infos = server.load_infos(sid)
    assign = [{"id": i["id"], "role": analyzer.make_choice(i), "case": i.get("case"),
               "view_kind": i.get("view_kind", ""), "rkind": i.get("rkind", "")} for i in infos]
    r = cli.post("/api/regroup", json={"sid": sid, "assign": assign})
    check("regroup answers", r.status_code == 200, r.get_data(as_text=True)[:300])
    draft = r.get_json()
    c1 = draft["cases"][0]
    ex = c1["extras"][0]
    check("A4 zoom attached to the stress picture", ex["parent"] == c1["stress_id"] and ex["attach"] is True,
          json.dumps(ex)[:300])
    check("A4 the matched region travelled with it", bool(ex.get("match")) and ex["match"]["score"] > 0.7,
          json.dumps(ex.get("match"))[:200])

    # ── A2: the catalogue is served and lands in the report with its own name ──
    r = cli.get("/api/status")
    kinds = {k["key"]: k for k in r.get_json()["result_kinds"]}
    check("A2 catalogue served", {"max_shear", "min_principal", "plastic_strain", "safety_factor"} <= set(kinds),
          str(sorted(kinds))[:200])
    check("A2 units come from the catalogue", kinds["max_shear"]["unit"] == "MPa" and kinds["safety_factor"]["unit"] == "")

    # ── build the report: the zoom must sit on the RESULTS slide, region marked ──
    p = payload_for(sid, infos, [case_payload(c1, [dict(e, caption="") for e in c1["extras"]])])
    r = cli.post("/api/build", json=p)
    check("build answers", r.status_code == 200, r.get_data(as_text=True)[:300])
    meta = r.get_json()
    d = server.build_dir(sid, meta["key"])
    deck = pv.get_deck(d / "report.pptx")
    titles = [deck.title(i) for i in range(deck.count())]
    res_i = next(i for i, t in enumerate(titles) if t.startswith("Results"))
    ops = deck.ops(res_i)
    npics = sum(1 for o in ops if o[0] == "pic")
    nlines = sum(1 for o in ops if o[0] == "line")
    check("A4 the results slide carries the zoom", npics >= 3, f"{npics} pictures on '{titles[res_i]}'")
    check("A4 no arrows or leader lines (company style)", nlines == 0, f"{nlines} lines")
    from pptx import Presentation
    prs = Presentation(str(d / "report.pptx"))
    shapes = [s.name for s in prs.slides[res_i].shapes]
    check("A4 red dashed region mark + boxed label under the inset",
          any("Detail region mark" in s for s in shapes) and any("Inset label" in s for s in shapes)
          and any("Inset label frame" in s for s in shapes), str(shapes))
    check("A4 no extra slide for the zoom", not any("zoom" in t.lower() or "additional" in t.lower() for t in titles),
          str(titles))
    im = deck.render(res_i, 1200)
    out = ROOT / "outputs" / "v23_results_slide.png"
    im.save(out)
    print("        slide rendered ->", out)

    # ── U2: more pictures join the same report ──
    r = cli.post("/api/analyze_more", data={"sid": sid, "files": (open(EX / "image-2.png", "rb"), "image-2.png")},
                 content_type="multipart/form-data")
    check("U2 analyze_more answers", r.status_code == 200, r.get_data(as_text=True)[:200])
    js = r.get_json()
    check("U2 the new picture joined the session", js["added"] == 1 and len(js["images"]) == len(infos) + 1)
    r = cli.post("/api/analyze_more", data={"sid": sid, "files": (open(EX / "image-2.png", "rb"), "image-2.png")},
                 content_type="multipart/form-data")
    check("U2 the same picture twice is refused", r.status_code != 200 or r.get_json().get("added") == 0,
          r.get_data(as_text=True)[:200])

    # ── U1b: pixel matching never holds up the page; what the sync budget leaves behind finishes in the background ──
    analyzer.MATCH_DEADLINE["at"] = time.monotonic() - 1.0            # pretend the budget is already spent
    fake2 = [dict(id=k, name=n, path=pa, role=rl, letter="A", model="6203_BLADE", deck=analyzer.deck_key("6203_BLADE"),
                  notes=[], max=None, legend_values=[], bc_draft=[], bc_legend=[], headline_bc=["", ""], unit=None,
                  legend_px=None, red_px=None, edge_density=None, z_dir=None, gravity=None, dup_of=None,
                  user_set=False, view_kind=vk, rkind="", result_kind="", parent=None, attach=None, pos="auto",
                  match=None, match_ambiguous=False)
             for k, (n, pa, rl, vk) in enumerate([("d.png", str(EX / "image-1.png"), "deformation", ""),
                                                  ("s.png", str(EX / "image-3.png"), "stress", ""),
                                                  ("z.png", str(tmp / "stress_zooom_view.png"), "x_stress", "detail")], 1)]
    analyzer.finish(fake2)
    dz = fake2[2]
    check("U1b a spent budget defers matching instead of blocking", bool(dz.get("match_pending")) and not dz.get("match"))
    analyzer.MATCH_DEADLINE["at"] = None

    infos = server.load_infos(sid)                                    # now the server-side background pass
    zid = next(i["id"] for i in infos if i["name"] == "stress_zooom_view.png")
    for i in infos:
        if i["id"] == zid:
            i["match"], i["match_pending"] = None, True
    server.save_infos(sid, infos)
    server._start_matcher(sid, [zid])
    got = None
    for _ in range(60):
        time.sleep(0.2)
        mm = cli.get(f"/api/matches?sid={sid}").get_json()
        if not mm["pending"] and (mm["matches"].get(str(zid)) or {}).get("match"):
            got = mm
            break
    check("U1b the background pass places the view", bool(got) and got["matches"][str(zid)]["match"]["score"] > 0.7,
          str(got)[:200])

    # ── U3b: the preview status chip must never wear the full-screen overlay's class (it would cover the page) ──
    js = (ROOT / "app" / "static" / "app.js").read_text(encoding="utf-8")
    check("U3b the status chip never uses the overlay class 'busy'",
          "'pv-status busy'" not in js and "idle: ['busy'" not in js and "building: ['busy'" not in js)

    shutil.rmtree(tmp, ignore_errors=True)
    print()
    if FAILS:
        print(f"{len(FAILS)} CHECK(S) FAILED: " + "; ".join(FAILS))
        sys.exit(1)
    print("all v2.3 checks passed")


if __name__ == "__main__":
    main()
