# FEA Report Maker 2 - Council Review

A panel of seven deliberately hostile reviewers attacked every idea **before** it was built,
and attacked the finished app **after** it was built. This file keeps the record.

| Seat | Who | Their only question |
|---|---|---|
| 1 | **Dr. Anand Rao**, principal FEA analyst | "Could this lead an engineer to a wrong conclusion?" |
| 2 | **Priya Nair**, design-review / QA lead | "Where is the evidence? Who checked it?" |
| 3 | **Ravi Kulkarni**, meshing & verification | "Units? Mesh quality? Is the peak mesh-independent?" |
| 4 | **Meera Shah**, daily user at Mech Well | "How many clicks? Will this embarrass me in front of the client?" |
| 5 | **Kabir Malhotra**, hostile client reviewer | "I will find the hole in your report." |
| 6 | **Sana Iyer**, information designer | "Can a human actually read this slide?" |
| 7 | **Dev Patel**, red-team tester | "I will break it with the ugliest input I can find." |

---------------------------------------------------------------------------------------------------
## Round 0 - the council attacks the app as it was (version 1.0)

| # | Attack | By | Verdict |
|---|---|---|---|
| A1 | "The app writes *structurally inadequate - corrective action* from ONE number. 38,825 MPa in a steel gate is not a verdict, it is a bug report (stress singularity)." | Anand, Kabir | **Real flaw -> fixed (P3)** |
| A2 | "A *Directional Deformation*, *Maximum Principal Stress* or *Safety Factor* picture is silently filed as the **setup picture**. Wrong, and invisible." | Anand, Dev | **Real flaw -> fixed (P14)** |
| A3 | "Two pictures per slide: your legends are only 5.5 pt. For a long or big model they drop to 2-3 pt and nobody can read them." | Sana, Kabir | **Real flaw -> fixed (P1)** |
| A4 | "The OCR is read once. One misread digit = a wrong number in a client report. (Found live: legend value 1.88 was read as 188.)" | Dev, Priya | **Real flaw -> fixed (P4)** |
| A5 | "A second mesh picture is silently ignored. A third geometry picture is silently ignored. Duplicates are not detected." | Dev | **Real flaw -> fixed** |
| A6 | "Refresh the browser and ten minutes of typing are gone." | Meera, Dev | **Real flaw -> fixed (P15)** |
| A7 | "A 12-case job overflows the summary table off the slide. A 3 000 px screenshot makes a 40 MB PowerPoint." | Dev | **Real flaw -> fixed (P13)** |
| A8 | "No mesh statistics, no assumptions, nobody's name on the cover, no location of the maximum." | Priya, Ravi, Kabir | **Gap -> optional fields (P5-P8, P19)** |

---------------------------------------------------------------------------------------------------
## Round 1-3 - proposals, attacks, rulings

### P1  Layout modes + legibility-driven automatic choice  - ADOPTED (modified)
* Sana: *Legibility must be a number, not a feeling.*  Kabir: *If I cannot read the legend I reject the slide.*
* Dev: *Your number comes from OCR boxes. When OCR finds no digits you will invent garbage - fallback and floor needed. Mixed layouts inside one deck look sloppy.*
* Meera: *Do not make me choose per case.*
* **Ruling:** measured, not guessed: legend digit height is read from the screenshot (about 9.6 px in your jobs, so 5.5 pt on the delivered slides).
  One deck-wide choice, *Auto* by default. Auto keeps the template's side-by-side whenever the legend stays >= 5.0 pt (so existing jobs do not change)
  and otherwise takes the layout with the largest legend: *stacked* or *one picture per slide*. If OCR cannot measure, assume 9.6 px.
  All three options are shown with their predicted legend size. A per-case override exists but is tucked away.
  If even "one per slide" stays under 5 pt the app says so and tells you to export the picture larger / closer.

### P2  Section views, detail views and other result types - ADOPTED
* Anand: *A section view must say where the cut is, and a section cannot show more than the model maximum.*
* Kabir: *A picture without a caption and a section key is decoration.*
* Dev: *Choosing the "main" plot by highest Max is fragile when OCR misreads. Make swapping one click and show why it chose.*
* **Ruling:** extra views per case (setup / deformation / stress / other result), any number. The main plot = highest Max; the others become extras automatically.
  Extras get a **heading that stays yellow until the engineer states the location** ("Section A-A ..."), a caption field, and a consistency check
  (an extra whose Max exceeds the main plot's Max is flagged as an OCR or picture error). Geometry and mesh accept any number of views with headings
  (a mesh section is just another mesh view). Section detection from pixels was **rejected** - there is no reliable cue; file-name hints ("section", "cut", "detail", "zoom") only.

### P3  Verdict tiers + singularity guard - ADOPTED
* Anand: *Beyond 2 x yield a human must decide.*  Kabir: *A report claiming 38 GPa in steel loses all credibility.*
* Meera: *My template says exceeds -> corrective action. If I choose "singularity" I need the stress away from it, and I do not always have it.*
* Priya: *Whatever is chosen must be written in the report, not hidden in the app.*  Dev: *2 x is arbitrary - make it a setting, never default the choice.*
* **Ruling:** tiers:  within allowable  |  above allowable, below yield  |  above yield (note added: linear result indicative only)  |  >= 2 x yield (singularity suspected: **decision required**).
  Three explicit, plainly worded options: (a) report as is, (b) local peak at a support/feature - report the stress away from it (engineer types location + value),
  (c) not concluded - refine the model. The chosen basis is printed in the observations and the summary table. The app never infers the "away from peak" number.

### P4  Evidence crops, double-read, legend sanity - ADOPTED
* Priya: *Show me the crop the number came from.*  Dev: *One OCR pass is one point of failure; two independent passes, flag disagreement.*  Meera: *Do not make the page five kilometres long.*
* **Ruling:** the legend / setup crops sit next to the numbers (compact); Max is read twice (different scale + page mode); the legend must be monotonic (repairs "188" -> "1.88" when it can, flags when it cannot);
  Max must be >= every other legend value.

### P5  Mesh statistics - ADOPTED as typed fields; OCR of the "Details of Mesh" panel REJECTED
* Ravi: *No reviewer accepts a mesh slide without element count and quality.*  Dev: *You have never seen that screenshot. Never build a parser against data you have not seen.*
* **Ruling:** optional typed fields (element type, elements, nodes, element size, max/avg skewness, min orthogonal quality); one line on the mesh slide only if filled.
  Thresholds: verified against the current ANSYS Meshing Help (skewness 0.75-0.9 "poor", >= 0.9 "bad (sliver)"; guidance min orthogonal quality > 0.1).
  The older ANSYS lecture tables differ (!), so the limits live in `settings.json`.

### P6  Assumptions & scope slide - ADOPTED as a tick-list, default NONE ticked
* Priya: *Mandatory in a serious report.*  Dev: *Pre-filled assumptions that are not true are worse than none.*  Meera: *Only if I tick them.*
* **Ruling:** library of common statements (linear elastic, bonded contacts, welds not modelled, ...) + custom lines; the slide exists only if at least one line is ticked/typed.

### P7  Acceptance criteria - ADOPTED
* Kabir: *Strength is not the only criterion - where is the deflection limit? Show utilisation.*  Meera: *The template's table has fixed columns.*
* **Ruling:** optional allowable deformation (adds one observation and one table column only if filled); utilisation % in the within-limit sentence (switch).

### P8  Location of the maxima - ADOPTED (optional) - *"Nobody can act on 'Max = 545 MPa' without a location."* - Kabir
### P9  Unit / material sanity - ADOPTED - Ravi: *units are blunder number one*: density, E, Poisson, gravity vs unit system, legend units, FOS < 1.
### P10 Reaction-force balance - ADOPTED as optional fields (first thing to cut if time is short) - Anand: *the first sanity check of any run*; Dev: *needs numbers the screenshots do not contain*.
### P11 Slide preview after generation - ADOPTED, optional pypdfium2 (small, permissive licence); Dev: *must never break the install*.
### P12 Setup-picture layout (side / stacked) - ADOPTED (same engine as P1)
### P13 Overflow protection - ADOPTED: 8 rows per summary slide, BC-list font shrink, 2 600 px image cap.
### P14 Result-type recognition - ADOPTED immediately (correctness bug)
### P15 Autosave + resume - ADOPTED
### P16 Suggestions from past jobs (client, structure, support sentences) - ADOPTED (browser storage only)
### P17 Duplicate-picture detection - ADOPTED (content hash)
### P18 Separate cases when two ANSYS projects both print "A:" - ADOPTED (split by model name)
### P19 Cover: prepared / checked / revision / drawing reference - ADOPTED, printed only if filled
### P20 Working-temperature note (from the Silvertone sample) - ADOPTED as a one-click note

### Rejected
| Idea | Why it died |
|---|---|
| "Report health score 87 %" | Priya: a meaningless number. The explicit list of checks is better. |
| AI-written recommendations | Anand: liability, and the app is offline by design. The closing line stays the engineer's. |
| OCR of mesh details / reaction tables | Dev: unseen formats. |
| Auto-locate the maximum from the red callout | Cannot be turned into words reliably. |
| "Template-exact vs Enhanced" switch | Meera: complexity. Every extra is opt-in simply by being filled in. |
| Print the app's warnings inside the report | Priya: checks are for the author; the report states facts. |
| Part-by-part allowables for assemblies | Out of scope; disclosed as a limit. |
| Detect section views from pixels | No reliable cue. |


---------------------------------------------------------------------------------------------------
## Round 4 - the red team attacks the finished program (Dev Patel, with everyone else shouting)

Every item below was found by actually running the program on a nasty input - not by reading code.

| # | What broke | How it was found | Fix |
|---|---|---|---|
| F1 | The layout chooser judged the plain geometry drawings by *legend* standards and moved them to separate slides - the ordinary KCP deck no longer matched the template. | End-to-end comparison with the hand-made deck | Plain drawings (no legend) get a softer threshold (70 %). Test kept. |
| F2 | The "evidence" crops were badly framed (started mid-line, half the picture was red model) - useless as evidence. | Looking at the screenshots | Crops are framed tightly from the OCR word positions: colour bar + every number + Max/Min; setup legend with its coloured letter boxes. |
| F3 | Tall pictures: the legend was not found at all (my crop started 12 % down the picture, below the legend). | 1 600 px tall test picture | Big pictures: retry with boxes anchored at the top-left in real pixels (ANSYS keeps its text at a fixed pixel size), for screen scales 1x / 1.5x / 2x. |
| F4 | Wide pictures: the model name picked up junk letters from the model ("...Moment at"), producing the short name "Nt"; the setup legend gave 4 of 6 lines. | 4.5 : 1 test picture | Trailing junk stripped; for big pictures the region that reads the MOST lines wins; the letter-box junk in front of legend lines is cleaned. |
| F5 | The legend repair deleted a GOOD value (2.15) and repaired its neighbour. Harmless for the verdict, but silent damage. | A unit test built from the real image-4 legend | Rewritten: the element that a power of ten can repair is repaired; otherwise the later one is dropped. |
| F6 | The mesh-statistics line was struck through by the vertical divider of the two-up slide. | Looking at the rendered slide | The divider stops above the note. |
| F7 | On a phone-sized screen the review page was 58 px too wide. | Playwright, 390 px viewport | The offending checkbox label may wrap; tables scroll inside their card. |
| F8 | A 6x upscaled screenshot (5 970 px): would the legibility estimate lie? | Test | It does not: legend digits are measured in ORIGINAL pixels, the builder's 2 600 px cap is accounted for, the prediction equals the un-scaled picture (5.4 pt), the PowerPoint stays small. |
| F9 | Hindi / Chinese file names, a 10-case job, a 14-sentence setup list, a holiday photo instead of an ANSYS picture. | Test | All handled (display names kept, summary paginates 8 + 2, list font shrinks 14 -> 10 pt, photo becomes a re-assignable picture). |

Two of my own expectations were wrong and the program was right: for 4.5 : 1 pictures "one per slide"
(4.5 pt) beats "stacked" (3.9 pt) - the chooser follows the numbers, so the test was corrected, not the program.

---------------------------------------------------------------------------------------------------
## Round 5 - version 2.1: "never block, flag instead"

**The trigger** was three complaints from the engineer who uses the program every day, in her own words:
*"it should make the preview directly"* - *"when I do not have some image it should carry on forcefully; it gets stuck and shows an
error even for non-mandatory images"* - *"it should have a preview and a download option like a browser, which downloads directly
into the Downloads folder"*. The council read the code with those three sentences in mind.

| # | Attack | By | Verdict |
|---|---|---|---|
| B1 | "14 places in the server raise an error for something that is *missing*: no geometry, no mesh, no setup picture, an empty yield box, a blank number, an empty list. The program is a form with a hard stop. A report with a gap is useful - no report is not." | Meera, Dev | **Real flaw -> fixed.** The builder never raises for a missing picture or number; the server only passes on what exists. |
| B2 | "The preview exists only after a PDF was made, i.e. only with LibreOffice AND pypdfium2 installed, and only on the last page. Most people never saw it." | Meera, Sana | **Real flaw -> fixed.** The program draws the slides itself from the PowerPoint file (Pillow + lxml, the bundled Liberation fonts). The preview is built automatically as soon as the review page opens and after every change (about 1.4 s after the last keystroke, one build at a time, the latest state wins). |
| B3 | "'Download' is a link on the done page that points into the program's own `outputs\` folder. Nobody on Windows finds that." | Meera | **Real flaw -> fixed.** Download buttons are always visible; they are ordinary browser downloads (`Content-Disposition: attachment`), so the file goes to the browser's Downloads folder. A message says where it went. A copy still goes to `outputs\`. |
| B4 | "Without Tesseract the program answers HTTP 503 and the page is a dead end." | Dev | **Real flaw -> fixed.** Manual mode: roles guessed from the file names (with a case number if the name has one), view direction still read from the axis arrows, the numbers typed by the engineer. One picture that fails to read does not stop the others. |
| B5 | "If people may download with gaps, the gaps must be impossible to overlook - a bracket in dark text on page 7 will be missed and sent to the client." | Priya, Kabir | **Adopted.** Every `[... please confirm]` is a separate run with a yellow text highlight in the PowerPoint; the preview shows the same highlight; the page counts the open items and a blue box lists what was left out. |
| B6 | "Without a yield stress the old program refused. If it now carries on, it must not invent a verdict." | Anand | **Adopted.** Neutral sentences ("Von-Mises stress in the Blade is 74.2 MPa."), no pass / fail, the conclusion is a highlighted placeholder, the allowable cell of the summary is a highlighted placeholder. A zero or negative yield counts as 'not entered' (no division by zero). |
| B7 | "A missing picture either leaves an ugly hole or silently shortens the report; the client will ask where the mesh went." | Kabir, Sana | **Two policies, default 'leave it out'.** Slides are renumbered by what exists. The alternative keeps an empty, labelled frame the size of a real picture, which PowerPoint's *Change Picture* fills in place. A mesh slide that carries typed statistics always keeps its frame. The blue box under the preview says in words what was done. |
| B8 | "The 'N items still need your input - go back / generate anyway' dialog is the same hard stop, only more polite." | Meera | **Removed.** The yellow items are a non-blocking note. |
| B9 | "Your own renderer must not lie. If it draws something PowerPoint will not, you mislead the user." | Dev, Sana | **Mitigated and disclosed.** Line pitch, baselines, kerning across spaces and the fit tolerance were calibrated against LibreOffice's own PDF (text x within 0.45 pt, baselines within 0.1 pt except table cells 1.6 pt, 98 % of all text lines identical, mean pixel difference about 1.7 / 255). It is still not PowerPoint, and the README says so. |
| B10 | "Press Download while the page is still saving or rebuilding - the file no longer matches the screen." | Dev | **Fixed.** Download first finishes the pending wording update and build for the current state; builds are keyed by a hash of the whole state, so an unchanged state is never built twice. |

**Rejected in this round**

* *Open the PowerPoint through Office-online* - needs the internet; the pictures are confidential.
* *A native 'Save as' dialog to pick the folder* - the request was "straight into Downloads"; it would also be Chrome-only.
* *Let LibreOffice draw the preview* - 2-5 s per change, and not installed on most computers.
* *A strict mode that refuses to download while a yellow box is open* - every piece of feedback says the opposite. The default is flag, never block.
* *Fill a missing picture with a generic stand-in picture* - it would look like a real result.

**What broke during this round (and was fixed)**

| # | What broke | How it was found | Fix |
|---|---|---|---|
| F10 | A cover title longer than 255 characters crashed the PowerPoint writer (document properties are limited to 255 characters). The first fallback failed for the same reason. | A fuzz run of 220 randomly damaged payloads: 3 failures | Properties are cut to 250 characters; the build has a third rung (very long texts shortened) and says so. Fuzz run again with 500 payloads: no failure. |
| F11 | The renderer broke lines at slightly different words than LibreOffice (`A long sentence` vs `A long`). | Comparing every text line with LibreOffice's PDF | Words were measured one by one, which loses the kerning across spaces. Lines are now measured as whole strings (and overflowing by less than 0.1 % still fits). |
| F12 | Empty result frames were taller than real ANSYS plots; the heading touched the frame. | Looking at the render | Frames have the proportions of a real plot (1.7 : 1). |
| F13 | Two button icons were font characters and showed as empty boxes on a machine without symbol fonts. | Screenshot | Inline SVG icons, no font needed. |
| F14 | The bottom bar took a quarter of a phone screen; the header wrapped onto two rows. | 390 px screenshot | One slim row of buttons on narrow screens; shorter status chips. |

**Tests added** (developer folder `tests`, not shipped): `test_missing.py` (65 picture sets x both policies: every picture dropped,
every group dropped, every set of only one or two pictures; plus 500 randomly damaged payloads - always HTTP 200 and every slide
drawable), `test_preview.py` (the renderer against LibreOffice), new sections 3, 7, 9 and 10 of `test_api.py` (manual mode, missing
things, preview and download, no LibreOffice), and browser tests that really click the buttons and catch the downloads.

---------------------------------------------------------------------------------------------------
## Round 6 - version 2.2: "why does it still look the same?"

**The trigger**: the same three sentences arrived a second and a third time, after 2.1 had been delivered. The council did not
assume that the engineer had not looked; it asked *how a user could still end up seeing the old behaviour*, and read the start-up,
the reading step and the download once more with that question.

| # | Attack | By | Verdict |
|---|---|---|---|
| G1 | "Someone who still has the OLD black window open double-clicks the new start file. The new program finds the port busy, prints 'already running' and opens the old program. The engineer sees the old errors and the old missing preview and concludes that nothing changed." | Meera, Dev | **Real flaw -> fixed.** On a busy port the program asks whatever answers there who it is (`/api/status`: name, version, folder). Only the same version from the same folder is reused. An older copy or any other program: the new copy starts on the next free port and the black window says so. |
| G2 | "Nobody can tell which version they are looking at." | Dev, Sana | **Real flaw -> fixed.** A "v2.2" chip in the header (the tooltip explains it), `?v=2.2` on the script and style files, and the page itself is never cached. |
| G3 | "Tesseract is called with no time limit. One odd picture - and the page waits for ever. To the user that is 'stuck'." | Dev, Priya | **Real flaw -> fixed.** 30 s per Tesseract call, 60 s per picture. A picture that runs out of time is handled in manual mode with a plain note; the others carry on. The page counts the seconds and has a Cancel button. Tested with two pictures that 'hang'. |
| G4 | "'Download started - saved in your Downloads folder' is shown even if nothing was saved: the file is gone, or the page sits inside a window (a website's preview pane) whose sandbox forbids downloads." | Meera, Kabir | **Real flaw -> mitigated.** The page checks that the file exists before it starts the download. A page cannot detect that its download was blocked, so inside a frame the message says so openly and names the folder that holds the copy. Tested with a sandboxed frame with and without `allow-downloads` (Chromium really blocks it in the second case). |
| G5 | "On Windows people look in File Explorer, not in a browser list." | Meera | **Adopted.** When the program runs on the user's own computer the message has "Open the Downloads folder" (opens `shell:Downloads`, correct also when OneDrive moved the folder). No path comes from the page. |
| G6 | "The start file writes a 'packages installed' marker for ever. A new version unpacked over an old folder keeps the old packages." | Dev | **Fixed.** The marker carries the version; a new version reinstalls. |
| G7 | "Double-clicking the start file inside the ZIP window gives a Python error about a missing file." | Meera | **Fixed.** A plain message: extract the ZIP first. |
| G8 | "On Windows 10/11 the Microsoft Store shortcut called python counts as 'Python found', then nothing works." | Dev | **Fixed.** Python is accepted only if `--version` succeeds. |

**Rejected in this round**

* *Copy the report silently into the Windows Downloads folder from the server as well* - the browser's own download would then create `report (1).pptx` next to it. Two copies of every report is worse than one.
* *Ship a private Windows Python inside the ZIP* - 60 MB, and it could not be tested on Windows.
* *Install Tesseract automatically with winget* - needs administrator rights and could not be tested.
* *Detect a blocked download from JavaScript* - browsers deliberately give no signal for it.

**Tests added**: `tests/test_v22.py` (version, HEAD check without copy, open-folder endpoint, two pictures that hang, start-up next to an older copy / another program / the same copy) and `tests/browser_ui_v22.py` (version chip, only some pictures, seconds counter and Cancel, sandboxed frame with and without downloads, the open-folder button).

### What the council still does not like (honest list)

1. **Two real jobs.** Other ANSYS versions or non-default screenshot layouts may need a crop adjusted. The fallbacks and the yellow boxes catch it; nobody has proven it for job number three.
2. **A section view is described by the engineer, not understood by the program.** (Rejected idea: guessing from pixels.)
3. **The legend-size estimate is only as good as the OCR's digit height** (about +/- 10 %). It falls back to the typical 9.6 px.
4. **2 x yield is a rule of thumb**, and peak von-Mises is compared as is: no stress linearisation / averaging, no part-by-part allowables.
5. **A very tall model cannot be helped by any layout.** The program says so instead of pretending.
6. **PowerPoint itself was never opened** - only LibreOffice. The Windows start file was never run on Windows.
7. **Reaction-force and mesh statistics are typed**, because the program has never seen those screenshots (parsing unseen screenshot formats was rejected).
8. **The preview is not PowerPoint.** It is drawn by the program's own renderer and agrees with LibreOffice's rendering of the same file to a fraction of a point; PowerPoint may still break a line one word earlier or later.
9. **The yellow highlight of unfinished spots is shown by PowerPoint 2019 / 365 and LibreOffice.** Older versions ignore it; the bracketed text is still there.
10. **Manual mode is only as good as the file names.** Unnamed pictures wait for the engineer's choice; no program can tell a deformation plot from a stress plot without reading its title.
11. **The PDF made without LibreOffice is picture-based** (its text cannot be selected).
12. **A download can be forbidden by the window the page is shown in.** If the page is shown inside the preview pane of a website, the browser may block every download from it, whatever the program does. Open the program in a normal browser tab of the user's own computer (`start_windows.bat`), or take the copy from `outputs`.
13. **The Windows start file and the "Open the Downloads folder" button were never run on a real Windows PC.** They use standard commands (`py -3`, `os.startfile("shell:Downloads")`) with fallbacks, but only a real PC can prove it.
