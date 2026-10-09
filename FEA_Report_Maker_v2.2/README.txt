FEA REPORT MAKER  -  version 2.3
================================

Drop your ANSYS screenshots in. The program sorts them, reads the numbers from the
colour legends (every number TWICE), and writes the first draft of the Mech Well FEA
report. You SEE the report as slides inside the page while you work, and you download the
PowerPoint or a PDF with one click - like any download in a browser: the file lands in the
Downloads folder of your computer (Windows: File Explorer > Downloads).

Everything runs on YOUR computer. No picture and no data is sent to the internet.

NEW IN 2.3   (the seven changes you asked for)
  * Upload waits for you. Pictures you drop or pick are COLLECTED first (a list shows what is waiting);
    add more pictures or a whole folder at any time, take a batch back with "Back", then press
    "Next: read the pictures". Nothing is read or sorted until you say so.
  * A way back, always: the review page footer has "Back to the pictures" (keeps everything you typed)
    and "Start over" (asks first, then forgets the job). On the upload page "Back" undoes the last batch.
  * The preview no longer rebuilds by itself. It sits at the BOTTOM of the review page and stays empty
    ("the preview stays empty until you ask for it") until you press "Update preview" - on the preview
    card or on the blue button in the footer. While your latest edits are not in it, it is dimmed and
    says "PREVIOUS build", and the footer button pulses.
  * The load case of a picture is read from the ANSYS title block (the "A: model name" line at the top
    left of every result plot), not guessed: pictures cluster by their deck (model) first, then by the
    case letter, and spelling mistakes in the block are tolerated. Two different decks that both call
    their first case "A" stay two cases. Expand a row in the Pictures table to see the headline as read
    and WHY the picture sits in its case.
  * The result-type list is now the full ANSYS Mechanical list (von-Mises; maximum / middle / minimum
    principal; maximum shear; stress intensity; normal and shear components; membrane, linearized, shell
    and beam stresses; every deformation and strain type; strain energy; safety factor; contact; bolt
    pretension; fatigue; temperature; reaction ...), grouped in the dropdown, each with its unit.
  * A zoomed or a section view is drawn ON THE SAME SLIDE as the picture it belongs to: the program finds
    the region inside the parent picture (pure pixel comparison - works without Tesseract), marks it with
    a teal rectangle and a leader line to the view in a column next to the plots. Up to three views per
    slide; more move to one extra slide and the notes say which. You can overrule everything: which
    parent, which side, or "no region mark".
  * The load case is read from the BOLD FIRST LINE of each screenshot (the case name you print on the
    picture): alike names are one case even when OCR misspells them, different names stay apart, and cases
    with alike names are presented series by series.  Every result picture states its maximum on the page
    ('Max. ... = value unit'); where nothing could be read the page asks in yellow instead of guessing.
  * The results slide now looks like the company's own duct report: a zoomed or section view sits ON the
    slide of its parent picture with a boxed label under it ("Zoomed view", "Section view"), the region it
    belongs to is marked with a red dashed rectangle on the parent, no arrows, and the result statements
    sit in thin boxes at the bottom. A view whose parent is known goes on that parent's slide unless you
    explicitly choose "on a slide of its own".
  * Getting from the upload page to the review page is quick again: the heavy pixel work (finding a zoomed
    or section view inside its parent picture) is capped at a couple of seconds; whatever is not finished by
    then completes in the background and appears on the page a moment later, with a short message.
  * The folder keeps its name FEA_Report_Maker_v2.2 on purpose (your shortcuts point at it); the page and
    the black window say v2.3. The council record of this round is docs/COUNCIL_REVIEW_V23.md and the
    machine-checked evidence is tests/test_v23.py.

NEW IN 2.2   (read this if you still see the OLD screen)
  * The page shows its VERSION at the top right (a small box "v2.2"). If it shows another number, or no
    number at all, you are looking at an OLD copy that is still open in another black window.
    Close that black window and its browser tab, then start this folder again.
  * An old copy that is still running can no longer be opened by mistake: the new copy starts on the next
    free port (5056, 5057 ...) and the black window says so. Your browser opens the right address by itself.
  * Reading the pictures never waits for ever. A picture the reader cannot finish within a minute is handed
    over to you (manual mode for that picture only); all the others are read normally. While it works the page
    counts the seconds and has a Cancel button.
  * After a download the message has a button "Open the Downloads folder" (File Explorer opens right there).
    Before the download starts the page checks that the file really exists.
  * The start file (a) tells you to extract the ZIP if you started it from INSIDE the ZIP, (b) ignores the
    Microsoft Store shortcut called "python", and (c) installs the packages again when you use a new version.

NEW IN 2.1
  * The preview is there by itself. As soon as the pictures are read, the report appears as slides at
    the top of the page and updates a moment after every change you make. It needs nothing
    else installed (no LibreOffice).
  * "Download PowerPoint" and "Download PDF" are normal browser downloads, always available.
    Nothing has to be generated first, and there is no separate "done" page.
  * NOTHING BLOCKS YOU any more. A picture you do not have (geometry, mesh, a setup or result
    picture ...) is simply left out, or - your choice - an empty labelled frame is kept. A number that is
    missing leaves out its sentence. A missing yield stress gives neutral wording and a yellow
    "[value - please confirm]". The program never stops with an error for something that is missing;
    it carries on and tells you what it did.
  * Without Tesseract (the picture reader) the program still works: "manual mode" - pictures are sorted by
    their file names and you type the numbers.
  * In the PowerPoint, every "[... please confirm]" is highlighted YELLOW, so an unfinished spot cannot
    be overlooked.

Version 2 was built with a "council" of hostile reviewers (FEA analyst, QA lead, meshing
specialist, daily user, client reviewer, slide designer, red-team tester) who attacked every
idea before it was built and attacked the finished program afterwards. Versions 2.1 and 2.2 went through a
fifth and a sixth round of the same kind. The record is in docs\COUNCIL_REVIEW.md.


-----------------------------------------------------------------------------
1.  INSTALL  (one time, about 10 minutes)
-----------------------------------------------------------------------------

A)  Python 3.9 or newer
      Windows : https://www.python.org/downloads/
                Run the installer and TICK  "Add python.exe to PATH"  on the first screen.
      Mac     : https://www.python.org/downloads/   (or:  brew install python)
      Linux   : normally already there  (sudo apt install python3 python3-venv)

B)  Tesseract OCR  =  the "picture reader"   (RECOMMENDED - without it the program runs in manual mode)
      Windows : https://github.com/UB-Mannheim/tesseract/wiki
                Download the "tesseract-ocr-w64-setup ..." installer and install with the default options.
      Mac     : brew install tesseract
      Linux   : sudo apt install tesseract-ocr

C)  LibreOffice   (OPTIONAL - only makes the PDF contain real, selectable text)
      https://www.libreoffice.org/download/
      Not needed for the preview and not needed for the PowerPoint. Without it the PDF is made
      from pictures of the slides (looks the same, but its text cannot be selected or searched).
      For a PDF with real text you can also use PowerPoint: File > Save As > PDF.


-----------------------------------------------------------------------------
2.  START
-----------------------------------------------------------------------------

   (First extract the ZIP: right-click it > Extract All... > Extract. Do not start from inside the ZIP window.
    Use a NEW folder - do not extract over an older version.)

   Windows : double-click   start_windows.bat
   Mac     : double-click   start_mac.command      (first time: right-click > Open)
   Linux   : in a terminal  ./start_mac_linux.sh

The very first start installs a few Python packages (internet needed, about 1 minute).
After that the program works completely offline.

A black window opens - leave it open while you work - and your browser opens at
        http://127.0.0.1:5055
(If an older copy is still running, this one uses the next free number - 5056 and so on - and the black
window tells you. The address in the browser is always the right one.)
The small box "v2.3" at the top right is the version you are looking at.
The top right of the page shows "Picture reader: ready" (or "manual mode" when Tesseract is
not installed - see 1B) and where the PDF comes from.

To stop the program: close the black window.
If you close the browser tab by mistake, open the address again: the page offers
"Continue your last report?" and everything you typed is back.


-----------------------------------------------------------------------------
3.  HOW TO USE
-----------------------------------------------------------------------------

 1. Drag pictures of one job into the page FOLDER BY FOLDER (any order, any file names): geometry,
    mesh, and for every load case the setup / total deformation / von-Mises stress picture,
    plus section views, detail views or other result types if you have them. They wait in a list;
    when everything is in, press "Next: read the pictures".
    Missing some of them? Drop what you have - see "NOTHING BLOCKS YOU" below.
    No pictures at hand? Press "Try it with the 9 example pictures".

 2. Work through the cards top to bottom; the PREVIEW is the last card, at the bottom of the page.
    Yellow boxes are things only you know. Everything else is filled in, and every sentence can be
    edited. The preview stays empty until you press "Update preview" - it never rebuilds by itself.
    - The preview shows the real report: arrows or the small slides below it move through
      the pages, "Full screen" shows a slide large.
    - "Checks" lists what the program noticed.
    - Next to every number you see the crop of the picture it was read from: compare them.
    - If a picture was sorted wrongly, change it in the "Pictures" table.

 3. Press "Download PowerPoint" or "Download PDF" (bottom right, or under the preview). The browser
    saves the file in your Downloads folder. A small message tells you the file name and has a button
    "Open the Downloads folder" (File Explorer opens there; or press Ctrl+J in Chrome / Edge to see the list).
    A copy is also kept in the folder  outputs  of this program.
    You may download at any time - also while yellow boxes are still open. Whatever is not filled in
    appears as a yellow [text in brackets] in the PowerPoint, to be completed there.

    If your browser asks WHERE to save every file: Chrome / Edge > Settings > Downloads > switch off
    "Ask where to save each file before downloading". Then the files go straight to Downloads.


NOTHING BLOCKS YOU  -  what happens when something is missing
  A picture is missing ...
     geometry / mesh ........ that slide is left out; the section numbers close up (2., 3. ...). A mesh slide
                              is kept as a frame if you typed mesh statistics.
     setup picture .......... the setup slide shows the list of loads and supports only.
     one result picture ..... the other result picture fills the whole slide.
     both result pictures ... that results slide is left out.
     a whole load case ...... that case is left out; the summary table lists the cases that exist.
     Under the preview you can choose instead:  "If a picture is missing: Keep an empty frame to fill in
     PowerPoint". The frame is a real picture box labelled "... not provided": in PowerPoint right-click it >
     Change Picture and your picture takes its place.
  A number is missing ...
     maximum deformation / stress ... its sentences are left out and the summary table shows a dash.
     yield stress .................. neutral wording ("Von-Mises stress in the Blade is 74.2 MPa."), no pass / fail
                                     statement, and a yellow "[value - please confirm]" where the allowable
                                     stress belongs. The program never invents a verdict.
  The picture reader (Tesseract) is missing ... manual mode (file names are used to guess what a picture is;
     you type the Max values and the lines of the setup list).
  A picture file is damaged ... it is treated as missing.
  Under the preview a blue box lists what was left out or simplified, in plain words.


WHAT THE PROGRAM DOES BY ITSELF
  - decides which picture is geometry / mesh / setup / total deformation / von-Mises stress /
    another result type, and which load case each belongs to (also when two separate ANSYS
    projects both call their case "A")
  - never mistakes a Principal Stress, Directional Deformation or Safety Factor plot for the
    von-Mises plot: those become additional views
  - reads Max deformation and Max stress from the legends, TWICE with different OCR settings;
    repairs a dropped decimal point inside a legend; flags any disagreement
  - reads the loads and supports (A, B, C ...), writes the sentences, converts pressure to mmWC
  - names Top / Bottom view from the axis arrows, names the cases, writes captions, observations,
    the pass / fail statement and the final summary table
  - uses the same picture only once if you upload it twice
  - suggests the yield stress from the stress legend; checks units, density, Young's modulus,
    Poisson's ratio, gravity, FOS; checks mesh statistics against the ANSYS limits
  - keeps your work safe: everything you type is saved automatically
  - rebuilds the preview ONLY when you ask (it never jumps or steals the page while you type);
    downloading always flushes your latest edits into the file first

WHAT NEEDS YOU
  - the yield stress (always typed or accepted by you, never remembered between jobs)
  - WHERE the supports and loads are ("on the left shaft") - the pictures cannot tell
  - client / report number when they are not in the ANSYS model name; the date
  - what a section or detail view shows ("Section A-A through the hub") - no program can see that
  - HOW to report a stress that is absurdly high (see 4.)
  - names of load cases when two cases have the same loads
  - a last look at the PowerPoint before it goes to the client


-----------------------------------------------------------------------------
4.  THE THINGS THAT WERE ADDED FOR BIG MODELS AND FOR FEA JUDGEMENT
-----------------------------------------------------------------------------

BIG OR WIDE MODELS - "Picture layout" card
  When two pictures share a slide they get small. The program measures the text INSIDE your
  screenshots (legend, axes) and tells you how big it will be on the slide, for each layout:
        Side by side  |  Stacked (one above the other)  |  One per slide
  About 5.5 pt is what the Mech Well template gives. Auto keeps the template look whenever the
  text stays readable (5 pt or more) and switches to the layout with the largest text when it
  does not. You can override it for the whole report or for one load case. If even the best
  layout leaves the text small, the program says so honestly - then export the picture larger or
  closer to the model, or add zoomed views. (A very TALL model cannot be helped by any layout,
  because the slide height is the limit.)
  The same applies to the setup picture (picture on top, list underneath) and to geometry,
  mesh and additional views. More than 8 summary rows continue on a second slide.

SECTION VIEWS, DETAIL VIEWS, OTHER RESULTS - "Additional views"
  A second picture of the same kind in a load case (a section of the stress plot, a zoom on a
  weld) is added as an additional view with its own slide. The plot with the highest Max stays
  the main plot (swap them with one click). Every additional view has a heading that stays
  YELLOW until you say where the cut / detail is, and an optional caption. A section cannot show
  more than the model maximum - if it does, the program warns you that one reading is wrong.
  Geometry and mesh accept any number of views, each with its own heading and caption.
  (File names containing "section", "cut", "detail", "zoom" give a hint for the heading.)

VERDICT TIERS AND THE SINGULARITY GUARD
  The report compares the maximum von-Mises stress with the allowable stress (yield / FOS):
     within allowable                      -> "adequate", with the utilisation in %
     above allowable, below yield          -> "exceeds ... need to take corrective actions"
     above yield                           -> same, plus a note that the linear-elastic result is
                                              only indicative at that location
     2 x yield or more (setting)           -> almost always a stress singularity (point support,
                                              sharp corner, contact edge) or a load / unit error.
                                              The program does NOT decide: you must choose
                                              (a) report as it is,
                                              (b) the peak is a local singularity - report the stress
                                                  away from it (you type where, and the value),
                                              (c) no conclusion yet - the model must be refined.
  Your choice is written into the observations and the summary table. The program never invents
  the "away from the peak" value.

OPTIONAL ENGINEERING EVIDENCE (printed only if you fill it in)
  - location of the maximum stress / deformation   - reaction-force check (applied vs reactions)
  - allowable deformation (adds a table column)    - mesh statistics under the mesh picture
  - assumptions & scope slide (a tick-list; NOTHING is ticked for you)
  - revision / prepared by / checked by / drawing reference on the cover
  - a one-click working-temperature note


-----------------------------------------------------------------------------
5.  SETTINGS  (settings.json - open with Notepad, save, restart the program)
-----------------------------------------------------------------------------

   port                 the number in the address (default 5055)
   report_no_pattern    how the report number is made; {job} = job number from the model name
   fos                  default factor of safety (1.3)
   material             default material row (the page also remembers what you typed last time)
   units                the "Units: ..." line of the material page
   mm_format            "sig3" = 3 significant digits (2.42 mm);  "sample" = 4 decimals like the old report
   show_utilisation     "(utilisation 25 %)" in the within-limit sentence
   singularity_factor   peak stress at or above this many times YIELD -> you must choose how to report it (2.0)
   min_legend_pt        legend text on the slide below this size is flagged (5.0 pt)
   mesh_limits          warning limits for the mesh statistics (ANSYS Meshing Help values)
   assumption_library   the tick-list of the Assumptions card - write your own standard sentences
   missing_pictures     "skip" = a missing picture's slide is left out (default), "frame" = keep an empty frame
                        (the page also remembers the choice you make under the preview)
   keep_copy_in_outputs true = every downloaded report is also copied into the folder  outputs


-----------------------------------------------------------------------------
6.  LIMITS  (please read)
-----------------------------------------------------------------------------

  * Built and tested on TWO real jobs (KCP MLD Blade and the Silvertone guillotine gate) plus
    many deliberately nasty variants (wide, tall and huge pictures, JPG, duplicates, section
    views, other result types, two projects both called "A", 10 load cases ...). Both real jobs
    were sorted correctly and every Max value was read correctly. A third job may still surprise it.
  * It expects the normal ANSYS Workbench screenshot: title block top-left, colour legend at the
    left, load / support legend top-left of the setup picture. If it cannot read something it says
    so (yellow box or a note in "Checks") and you type the value - it never invents numbers.
  * Section / detail views are told apart from the main plot by their Max value and file name
    only - there is no reliable cue in the pixels. YOU state what they show.
  * The size of the text on the slide is an estimate (measured from the legend digits).
  * The 2 x yield rule is a rule of thumb. The engineer decides.
  * One governing allowable stress per report (several materials: you choose the governing row).
    No part-by-part allowables, no stress linearisation / averaging (peak von-Mises only).
  * The generated PowerPoint uses the same layout as the template, but it was only checked in
    LibreOffice and by the program's own slide renderer, not in PowerPoint itself - give it one look in
    PowerPoint before sending. The yellow highlight of unfinished spots shows in PowerPoint 2019 / 365.
  * The preview is drawn by the program's own slide renderer (so it works on every computer). It follows
    the PowerPoint file closely (text lines agree with LibreOffice's within a fraction of a point) but it
    is not PowerPoint: a line may break one word earlier or later, and text may sit a pixel or two apart.
  * Without LibreOffice the PDF is made from pictures of the slides: it looks the same, but its text
    cannot be selected or searched.
  * Without Tesseract (manual mode) the program cannot read the pictures; it guesses from the file names
    only, and you type every number. With unnamed pictures you choose what each one is.
  * The Windows start file could not be tested on a real Windows PC by the person who built it.
    If it fails, send a photo of the black window.
  * The preview and the downloads run inside the program on YOUR computer. Do not upload confidential
    pictures to any copy of the program that runs on someone else's computer.
  * It is a drafting aid. The engineer remains responsible for the numbers and the conclusions.


-----------------------------------------------------------------------------
7.  PROBLEMS
-----------------------------------------------------------------------------

  "Picture reader: manual mode"     Tesseract is not installed (step 1B). You can work without it, but the
                                    numbers are not read from the pictures. Windows: restart the program afterwards.
  The download does not start       The page shows a link "Nothing happened? Click here". A pop-up blocker or
                                    a "this site wants to download multiple files" question can be the cause: allow it.
                                    If the page is shown INSIDE another window (for example the preview pane of a
                                    website) that window can forbid every download. Open the program in a normal
                                    browser tab of your own computer (start_windows.bat), or take the copy from outputs.
  I still see the old screen        Look at the top right of the page: it must say  v2.3.  If not, an OLD copy is still
                                    open. Close all black windows of the program and the browser tabs, start the new
                                    folder again. (Extract the ZIP into a NEW folder; do not mix it with an old one.)
  Where is my file?                 Your browser's Downloads folder (Windows: File Explorer > Downloads, or
                                    Ctrl+J in Chrome / Edge, or the button "Open the Downloads folder" in the
                                    message). Also in the folder  outputs  of this program.
  The page waits for ever           It should not any more (see "NEW IN 2.2"). Press Cancel and drop the pictures again;
                                    if a picture keeps failing, rename it  case1_bc.png  /  case1_stress.png ...  and try again.
  The slide looks slightly different in PowerPoint
                                    The preview is the program's own drawing, not PowerPoint (see 6.).
  PowerPoint says "Protected View" PowerPoint does this for every file that was downloaded from a browser.
                                    Click "Enable Editing" - the file is your own report.
  The browser does not open         Type  http://127.0.0.1:5055  in the address bar.
  "port ... in use"                 Normally nothing to do: the program takes the next free number by itself.
                                    Only if no number is free: close the other black windows.
  Packages could not be installed   Check the internet connection and start again. (To force a fresh
                                    install delete the hidden folder  .venv  and start again.)
  A picture is sorted wrongly       Change "What is it?" / "Load case" in the Pictures table.
  A number is wrong                 Compare with the crop next to it and type the correct value.
  Old pictures                      Temporary copies are kept in the folder  work  for 7 days.
                                    You can delete that folder any time.


-----------------------------------------------------------------------------
8.  WHAT IS IN THIS FOLDER
-----------------------------------------------------------------------------

  start_windows.bat / start_mac.command / start_mac_linux.sh    start the program
  settings.json                                                 your defaults and limits
  outputs\                                                      a copy of every report you download
  examples\                                                     9 example pictures (KCP MLD Blade)
  docs\COUNCIL_REVIEW.md                                        how the program was challenged
  app\                                                          the program (web page + picture reader)
  engine\                                                       the report builder (template look, fonts, logos)

Third-party software used: Tesseract OCR (Apache-2.0), Flask, python-pptx, Pillow, NumPy,
pytesseract; LibreOffice (optional, MPL-2.0); the Liberation Sans fonts (SIL Open Font License, see
engine\fonts\LICENSE.txt) are used to measure and draw text so that lines break the same way as in PowerPoint.
