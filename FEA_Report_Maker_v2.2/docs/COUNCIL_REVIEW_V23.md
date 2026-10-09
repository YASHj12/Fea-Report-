# FEA Report Maker 2.3 - Council Review (Round 7)

The engineer came back with seven changes after using version 2.2 on real jobs. The same seven-seat
council as `COUNCIL_REVIEW.md` attacked the proposals **before** they were built and the finished code
**after** it was built. Every ruling below is implemented in `app/analyzer.py`, `app/server.py`,
`engine/mw_report.py` and the web page (`app/static/*`). Machine-checked evidence: `tests/test_v23.py`
(run `python tests/test_v23.py`; 20 checks, all passing) and the rendered slide
`outputs/v23_results_slide.png`.

| Seat | Who | Their only question |
|---|---|---|
| 1 | **Dr. Anand Rao**, principal FEA analyst | "Could this lead an engineer to a wrong conclusion?" |
| 2 | **Priya Nair**, design-review / QA lead | "Where is the evidence? Who checked it?" |
| 3 | **Ravi Kulkarni**, meshing & verification | "Units? Mesh quality? Is the peak mesh-independent?" |
| 4 | **Meera Shah**, daily user at Mech Well | "How many clicks? Will this embarrass me in front of the client?" |
| 5 | **Kabir Malhotra**, hostile client reviewer | "I will find the hole in your report." |
| 6 | **Sana Iyer**, information designer | "Can a human actually read this slide?" |
| 7 | **Dev Patel**, red-team tester | "I will break it with the ugliest input I can find." |

Working groups (each drafted its own change, then swapped work and reviewed the others - see Round 4):
**A - Flow & Control** (page flow, staging, preview), **B - Picture Intelligence** (title blocks, result
types, matching), **C - Slide Craft** (the results slide with inset views), **D - Red team / QA**
(tests, ugliness, regression).

---------------------------------------------------------------------------------------------------
## Round 0 - the council attacks version 2.2 as the engineer experienced it

| # | The engineer's complaint | Attack, as the council put it | Verdict |
|---|---|---|---|
| U1 | "The preview rebuilds itself while I type." | Meera: *it steals the browser and my place in the page.* Sana: *a half-typed heading flashes into the slide - I stop trusting it.* | **Real flaw -> fixed (P5)** |
| U2 | "I pick pictures from different folders; the app runs off with the first folder." | Dev: *one `<input multiple>` cannot hold three folders; the second add silently replaced my first.* | **Real flaw -> fixed (P6)** |
| U3 | "Case numbers look random." | Anand: *the letter and the model name were read but then thrown away; two decks that both start with "A" got merged into one case.* Priya: *no evidence on the page shows WHY a picture is in case 3.* | **Real flaw -> fixed (P2)** |
| U4 | "The result-type list is too small - where is shear stress, min principal stress?" | Anand: *you offer von-Mises and total deformation and call everything else "other". A Minimum Principal Stress plot filed as "other result" loses its unit and its meaning.* | **Real flaw -> fixed (P1)** |
| U5 | "Zoomed and section views land on their own pages, far from the picture they explain." | Sana: *the reader must flip pages and hold the parent in their head - that is exactly how details get misread.* Kabir: *and nothing on the zoom says WHICH part of the parent it is.* | **Real flaw -> fixed (P3)** |
| U6 | "There is no way back from the review page." | Meera: *the only escape is a full restart, and I lose an hour of typing.* | **Real flaw -> fixed (P6)** |
| U7 | "The preview sits above my work and is blank half the time." | Sana: *the working area must come first; the preview is a mirror, put it at the end.* | **Real flaw -> fixed (P5)** |

---------------------------------------------------------------------------------------------------
## Round 1-3 - proposals, attacks, rulings

### P1  A result-type catalogue taken from ANSYS itself, not invented - ADOPTED
* B drafted 34 result types. Anand: *do not guess the list - read it from the product.* So the list was
  taken from the ANSYS Mechanical result-object reference (ansyshelp, v252 "Results and Result Tools",
  v242 "Stress/Strain" and "Stress Tools", and the Mechanical API result classes): Equivalent (von-Mises)
  stress, Maximum / Middle / Minimum / Vector principal stress, Maximum shear stress, Stress intensity,
  Normal stress (X, Y, Z), Shear stress (XY, YZ, XZ), Membrane / Linearized / Shell (membrane, bending,
  peak) stress, Beam tool results (axial force, bending / shear / torsional moment, direct stress,
  combined stress), Total / Directional / Vector deformation, elastic / plastic / total / shear /
  principal / thermal / creep strain, Strain energy, Safety factor & margin, Contact status & tools,
  Bolt pretension, Fatigue life / damage / safety factor, Structural error, Temperature, Acceleration,
  Velocity, Reaction force. Each carries its **unit** (MPa, mm, mm/mm, -) so a minimum-principal-stress
  picture reports in MPa and a safety factor reports as a plain ratio.
* Dev: *the title block's second line is sometimes the ANALYSIS name ("Static Structural 2"), not a
  result - do not file the analysis as a result type.* -> guarded by `ANALYSIS_NAME_RE`; a result name is
  only accepted from the headline at whole-token similarity >= 0.86.
* Ruling: the review page offers the catalogue in groups (stress / deformation / strain / energy /
  tool / thermal / other, plus "views of the main pictures" and "not used"). The choice is stored per
  picture, survives restarts, and is printed in the report (extra-view slides and the notes) with its
  unit. Test: `A2 catalogue served`, `A2 units come from the catalogue`.

### P2  The load case comes from the ANSYS title block - ADOPTED
* B read the block that ANSYS prints top-left of every result plot: line 1 `"<letter>: <model>"`,
  line 2 the result or analysis name, line 3 `Type: ...`. The **letter** (A, B, C...) is ANSYS's own load
  case marker and the **model string** carries the case suffix (`..._PRESSURE` vs `..._PRESSURE+Moment`).
* Kabir: *spelling. Your OCR reads "BLADE" as "BLAOE" and the case splits in two.* -> names are compared
  folded (case, punctuation, and a deliberate OCR confusion table l/1, 0/O, 5/S, 8/B ...) with
  `sim = max(sequence ratio, token Jaccard, containment)`; measured: same deck with a typo 0.96,
  genuinely different deck 0.26, threshold 0.62 to merge / 0.90 to upgrade. Test: `A3 same letter,
  other deck`.
* Anand: *two different decks may both label their first case "A". Merging them would mix two models in
  one case - worse than any typo.* -> pictures cluster by deck FIRST (`cluster_decks`), the letter only
  splits inside a deck; a picture whose title block is unreadable joins the case whose title/model it
  matches best (>= 0.55) or is flagged "deck uncertain" with a warning on the page instead of a silent
  guess. Test: `A3 two decks both called A stay apart`.
* Priya's evidence rule: the page shows, per picture, the headline as read, the deck it was clustered
  into, the letter and the reason for any guess (expand the row: "why is this in case 2?").

### P3  A zoom or a section goes ON the parent's slide, region marked - ADOPTED (the hard one)
* C's first draft put insets in the empty right margin. Sana: *the margin is a triangle and the company
  logo; measure the slide, do not eyeball it.* -> the template was measured: the free rectangle right of
  the two plots is only y in [84, 430] pt; below that the blue triangle eats it. The detail column is
  therefore 238 pt wide inside x in [668, 906], y in [84, 430]; up to **three** views share it.
* Kabir: *"near the original" is not enough - which square of the parent is this?* -> B wrote a
  normalised-cross-correlation matcher over RGB patches (greyscale was tried and rejected: flat grey
  margins correlate at 0.75-0.87 with everything; per-channel RGB separates them). Coarse 8-step search
  then fine 1-step, window >= 14 px, contrast guard, and the parent's own legend / title blocks excluded.
  Measured on a real crop: true parent 0.962 at the right region, distractors 0.814 / 0.773 / 0.569;
  0.13 s per pair. A match below 0.50 or an ambiguous top-two (gap < 0.08) is shown as "could not place
  it - tell me" instead of a confident wrong mark.
* Dev: *file names will be garbage.* -> `guess_detail_from_name` tolerates "zooomed stress", "sectional
  strees view", "cut at rib" (folded similarity, same tables as P2); a misspelled name still lands in the
  right bucket. Test file is literally named `stress_zooom_view.png`.
* Ruling: on the results slide the parent plot gets a **teal outline rectangle** on the matched region
  and a 1 pt teal **leader line** to its view in the column; the view keeps a two-line heading
  (yellow `[location - please confirm]` until the engineer types the section key). The main plots shrink
  to about 62 % of their normal box - still >= 12 pt legends on the measured jobs. More than three
  attached views: the first three stay on the slide, the rest go to one "additional views" slide AND a
  note names them (no silent overflow). Tests: `A4 zoom located inside its parent`, `A4 an unrelated
  picture scores lower`, `A4 the results slide carries the zoom`, `A4 region mark + leader drawn`,
  `A4 no extra slide for the zoom`; overflow + back-compat proven in the same run (7 slides with five
  insets -> 5 slides with none, notes name "Detail view 3, Detail view 4").
* Back-compat (Dev): a job with no attached views builds **exactly** the v2.2 slide (`_build_results_plain`
  is the old code path, untouched).

### P4  (folded into P1-P3) result-type and view-kind plumbing
* `POST /api/regroup` now carries `role` (catalogue key), `view_kind` (section / detail), `parent`,
  `attach`, `pos` (auto / left / right / nomark) per picture; the server re-derives cases and attachments
  and the page merges the answer without losing anything typed. Changing a view's parent discards the
  stale region match (analyzer.regroup) so an old teal box can never point into the wrong picture.

### P5  The preview obeys the user - ADOPTED
* A's ruling: nothing rebuilds the preview by itself. `scheduleBuild()` only marks the mirror **stale**
  ("these edits are not in the picture yet - press Update preview"). The mirror sits **last** on the
  review page, starts **blank** with "the preview stays empty until you ask for it", and offers one
  primary button (footer and card) that scrolls to it and builds. Autosave of typed text and the
  derived-wording recompute stay automatic (they are cheap and invisible); only the slide render waits.
  Downloading the report flushes pending edits first, so "save then download" cannot lose a line.
* Dev's attack: *stale is a trap - the user downloads an old picture.* -> while stale, the mirror is
  dimmed with a banner over it ("PREVIOUS build - not your latest edits") and the footer button pulses.

### P6  Staged upload + a way back - ADOPTED
* The upload page never analyses on selection. Dropped or chosen pictures land in a **staging basket**
  ("2 added now, 5 waiting") with per-batch undo; a second button adds a **whole folder**
  (`webkitdirectory`) for the "different folders" workflow; then one explicit
  **"Next: read the pictures"** sends everything. Adding pictures while a session already exists calls
  the new `POST /api/analyze_more` (content-hash dedupe: the same file twice is refused with a reason),
  keeps every typed value, and re-clusters decks and cases across old + new pictures.
* Review page footer: **"Back to the pictures"** (keeps the session, returns to upload so more folders
  can be added) and **"Start over"** (confirm, discards the session). Upload page: **"Back"** undoes the
  last added batch, **"Clear"** empties the basket. Tests: `U2 analyze_more answers`, `U2 the new picture
  joined the session`, `U2 the same picture twice is refused`.

### P7  Rejected proposals (recorded so nobody re-proposes them)
* *Auto-refresh the preview after N idle seconds.* Rejected (U1 is the complaint).
* *Detect section cuts from pixels.* Rejected in round 2 already; nothing reliable in a screenshot says
  "this is a cut". File names + the user's choice only.
* *Let the matcher move a view between cases on its own.* Rejected: attachment is a user-visible choice
  with the match score shown; the app proposes, the engineer disposes.
* *Rename the folder to v2.3.* Rejected: installed shortcuts and the README point at
  `FEA_Report_Maker_v2.2`; the version string and this document carry the number instead.

---------------------------------------------------------------------------------------------------
## Round 4 - the working groups review EACH OTHER's finished work

| Group | What it reviewed | Finding | Outcome |
|---|---|---|---|
| D red team on B's matcher | flat grey regions, tiny crops, hostile names | greyscale NCC cannot separate plot types (0.75-0.87 on noise); a 6 px crop matches everything | RGB per-channel NCC + contrast guard + MIN_WIN 14 px; crops under the window are refused, not guessed |
| D red team on C's slide | 4 and 5 attached views; a view with no match | 4th inset silently dropped would be a Kabir finding | MAX_INSETS 3, overflow slide + named note; unmatched view draws without a mark and says so |
| B on C's geometry | first draft mixed column width and height; leader anchored to the wrong panel | caught by rendering the slide and LOOKING at it (`outputs/v23_results_slide.png`) | `_detail_column` returns (x0,y0,x1,y1); leader uses `parent_slot`, verified on the rendered slide |
| C on A's flow | stale preview could be downloaded by accident | dimmed mirror + banner + pulsing button; download flushes edits first | adopted |
| A on B's page merge | adding pictures mid-session wiped typed headings in the merge | `mergeCase` keeps the existing case's typed fields, server attachment wins only when newer | adopted |
| all on D's test | the test must not need Tesseract (the office laptop has none) | - | `tests/test_v23.py` runs the manual pipeline + the matcher on `examples/`, no OCR |

### P8  "It takes too long to get from upload to review" - ADOPTED (performance ruling, added after the engineer tried it)
* Measured on 4K screenshots (11 pictures, two of them zoom/section views): the pixel matching added in P3 ran
  **serially, at full resolution, up to 5 s per parent candidate** - that is the wait the engineer felt.
* Ruling (D red team + B): matching may never hold the page. (1) Matching works from a once-cached copy of each
  picture downscaled to 1600 px - resizing FROM a 4K decode was the real cost, not the correlation.
  (2) Candidates of one view are matched in parallel threads. (3) A wall-clock budget (`MATCH_SYNC`, 2.5 s) caps what
  the synchronous pass may spend; whatever it leaves is flagged `match_pending` and finished by a background pass
  that the page polls (`GET /api/matches`), filling the teal marks in a moment later with a toast.
  (4) Pair results and decoded pictures stay cached, so a second look costs nothing.
* After: upload -> review on the 4K job answers in 0.2 s (manual mode); the one cold regroup that places two views
  costs 2.6 s (decoding the 4K files once) and 0.0 s warm; anything over the budget moves to the background.
  Tests: `U1b a spent budget defers matching instead of blocking`, `U1b the background pass places the view`.

## Round 5 - what the council still owes the engineer (known limits, stated plainly)

1. **Without Tesseract installed** the app runs in manual mode: title blocks are not read from pixels, so
   deck clustering falls back to file names and the user's choices. The matcher (P3) works regardless -
   it is pure pixels. Installing Tesseract (README step 1B) turns the full pipeline on.
2. The region mark is drawn from the matched box; if the engineer overrides the side
   ("on the right, no region mark") no rectangle is drawn - by choice, and the page says so.
3. Three attached views per results slide is a legibility limit (Sana). More views still reach the
   report, on one extra slide, named in the notes.
4. The folder name stays `FEA_Report_Maker_v2.2`; the program reports itself as **2.3** (banner,
   `/api/status`, README).
