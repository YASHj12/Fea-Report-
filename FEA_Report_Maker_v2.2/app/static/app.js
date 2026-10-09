'use strict';
/* FEA Report Maker 2 - page logic.  No libraries, no internet needed. */

// ───────────────────────────── small helpers ─────────────────────────────
const $ = (s, r = document) => r.querySelector(s);
const $$ = (s, r = document) => Array.from(r.querySelectorAll(s));
const JSON_H = { 'Content-Type': 'application/json' };

function h(tag, attrs, ...kids) {
  const el = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs || {})) {
    if (v === undefined || v === null || v === false) continue;
    if (k === 'class') el.className = v;
    else if (k === 'value') el.value = v;
    else if (k.startsWith('on')) el.addEventListener(k.slice(2), v);
    else el.setAttribute(k, v === true ? '' : v);
  }
  for (const kid of kids.flat(Infinity)) {
    if (kid === null || kid === undefined || kid === false) continue;
    el.append(kid instanceof Node ? kid : document.createTextNode(String(kid)));
  }
  return el;
}

async function api(url, opts) {
  let r;
  try { r = await fetch(url, opts); }
  catch (e) {
    if (e && e.name === 'AbortError') throw e;                // the user pressed Cancel
    throw new Error('Cannot reach the program. Is the black program window still open?');
  }
  let j = null;
  try { j = await r.json(); } catch (e) { /* not JSON */ }
  if (!r.ok) { const err = new Error((j && j.error) || `Error ${r.status}`); err.data = j; throw err; }
  return j;
}
const post = (url, body) => api(url, { method: 'POST', headers: JSON_H, body: JSON.stringify(body) });

const NUM_RE = /^[-+]?(\d+\.?\d*|\.\d+)([eE][-+]?\d+)?$/;
const isNum = v => NUM_RE.test(String(v ?? '').trim().replace(',', '.'));
const num = v => (isNum(v) ? parseFloat(String(v).trim().replace(',', '.')) : null);
const nice = v => (v === null || v === undefined ? '' : String(v));

const ROLES = [['geometry', 'Geometry (the model)'], ['mesh', 'Mesh'], ['bc', 'Setup: loads & supports'],
               ['deformation', 'Total deformation'], ['stress', 'Von-Mises stress'],
               ['x_deformation', 'Extra view: deformation'], ['x_stress', 'Extra view: stress'], ['x_other', 'Extra view: other result'],
               ['x_bc', 'Extra view: setup'], ['unused', 'Not used'], ['unknown', 'Choose ...']];
const choiceOf = i => `${i.role || 'unknown'}|${i.view_kind || ''}|${i.rkind || ''}`;

// "What is it?" as grouped options: the main pictures, then every named ANSYS result type as an additional view
// (section / zoom / own plot), then the leftovers.  Built from the catalogue the program sends (one list, one place).
function roleOptions(img) {
  const kinds = (S.status && S.status.result_kinds) || [];
  const byGroup = {};
  kinds.forEach(k => { (byGroup[k.group] = byGroup[k.group] || []).push(k); });
  const G = [['stress', 'Other stress plots (additional view)'], ['deformation', 'Other deformation plots (additional view)'],
             ['strain', 'Strain results (linear / nonlinear)'], ['energy', 'Energy'], ['tool', 'Stress tool & contact / bolt results'],
             ['thermal', 'Thermal'], ['other', 'Other results']];
  const viewOf = (role, vk, rk, label) => h('option', { value: `${role}|${vk}|${rk}`, selected: choiceOf(img) === `${role}|${vk}|${rk}` }, label);
  const groups = [h('optgroup', { label: 'The main pictures' },
    ROLES.filter(r => ['geometry', 'mesh', 'bc', 'deformation', 'stress'].includes(r[0]))
      .map(([v, t]) => h('option', { value: `${v}||`, selected: choiceOf(img) === `${v}||` || (img.role === v && !img.view_kind && !img.rkind) }, t)))];
  for (const [g, label] of G) {
    const ks = byGroup[g] || [];
    if (!ks.length) continue;
    groups.push(h('optgroup', { label }, ks.flatMap(k => {
      const main = k.key === 'von_mises' ? 'stress' : k.key === 'total_deformation' ? 'deformation' : 'x_other';
      const opts = [viewOf(main === 'x_other' ? 'x_other' : main, '', k.key, k.label)];
      if (main !== 'x_other') {                       // a section or a zoom OF this plot
        opts.push(viewOf('x_' + main, 'section', k.key, `Section view of ${k.short}`),
                  viewOf('x_' + main, 'detail', k.key, `Zoomed / detail view of ${k.short}`));
      }
      return opts;
    })));
  }
  groups.push(h('optgroup', { label: 'Views of the main pictures' },
    viewOf('x_stress', 'section', '', 'Section view of the von-Mises stress'),
    viewOf('x_stress', 'detail', '', 'Zoomed / detail view of the von-Mises stress'),
    viewOf('x_deformation', 'section', '', 'Section view of the deformation'),
    viewOf('x_deformation', 'detail', '', 'Zoomed / detail view of the deformation'),
    viewOf('x_bc', 'detail', '', 'Zoomed / detail view of the setup'),
    viewOf('x_other', '', 'other', 'Another result (I will keep the name you type in the heading)')));
  groups.push(h('optgroup', { label: 'Not used' },
    h('option', { value: 'unused||', selected: img.role === 'unused' }, 'Not used'),
    img.role === 'unknown' ? h('option', { value: 'unknown||', selected: true }, 'Choose ...') : null));
  return groups;
}
const CASE_ROLES = new Set(['bc', 'deformation', 'stress', 'x_bc', 'x_deformation', 'x_stress', 'x_other']);
const KIND_LABEL = { bc: 'setup', deformation: 'deformation', stress: 'stress', other: 'other result' };
const LAYOUT_NAMES = { side: 'Side by side', stack: 'Stacked', separate: 'One per slide' };
const WORDING = [['deformation_caption', 'Caption under the deformation picture'], ['stress_caption', 'Caption under the stress picture'],
                 ['conclusion', 'Closing line (leave empty for none)']];
const BASIS_OPTIONS = [
  ['asis', 'Report it as it is', 'The peak stress is compared with the allowable stress (the template wording).'],
  ['singularity', 'The peak is a local singularity (support, sharp corner, contact edge)', 'Report the stress AWAY from the peak instead. You say where the peak is and what the stress is elsewhere.'],
  ['refine', 'No conclusion yet: the model must be refined', 'The report says the peak is mesh-dependent and no conclusion can be drawn.'],
];

// ───────────────────────────── state ─────────────────────────────
const freshBuild = () => ({ key: null, slides: [], notes: [], name: 'FEA_Report', status: 'idle', error: '', cur: 0, sig: '', pdfMethod: null,
                            running: false, again: false, promise: null, downloaded: false });
// pictures that are chosen but not read yet (the engineer picks them folder by folder - see bindUpload)
let STAGE = [];            // [{name, size, file}]
let STAGE_BATCH = [];      // index into STAGE where the last "add" started (Back removes that batch)
const S = {
  status: null, sid: null, took: 0, skipped: [], images: [], cover: {}, short: '', fos: 1.3, fosText: '1.3', units: '',
  haveSession: false,
  mat: { rows: [], gov: 0 }, defLimit: '', showUtil: true, geometry: [], meshes: [], meshStats: {}, assumed: {}, assumeCustom: '',
  layouts: { results: 'auto', geometry: 'auto', bc: 'auto', extras: 'auto' },
  cases: [], warnings: [], checks: [], suggest: null, layoutPlan: null, missing: 'skip', manual: false, build: freshBuild(), dlBusy: false,
};
const STATE_KEYS = ['took', 'skipped', 'cover', 'short', 'fos', 'fosText', 'units', 'mat', 'defLimit', 'showUtil', 'geometry', 'meshes',
                    'meshStats', 'assumed', 'assumeCustom', 'layouts', 'cases', 'warnings', 'missing', 'manual'];

const KEY = 'fea_maker_v2';
function loadSaved() { try { return JSON.parse(localStorage.getItem(KEY)) || {}; } catch (e) { return {}; } }
function writeSaved(o) { try { localStorage.setItem(KEY, JSON.stringify(o)); } catch (e) { /* storage not available - fine */ } }
function saveSaved() {      // remembers material + FOS (never the yield: it must be typed for every job)
  const r = S.mat.rows[0] || {}, o = loadSaved();
  writeSaved(Object.assign(o, { fos: S.fos, units: S.units, showUtil: S.showUtil, missing: S.missing,
    material: { material: r.material, item: r.item, E: r.E, nu: r.nu, rho: r.rho } }));
}
const HIST_KEYS = ['client', 'short', 'bc', 'prepared_by', 'checked_by'];
const hist = k => (loadSaved().hist || {})[k] || [];
function pushHist(entries) {   // suggestions for the next job (browser storage only)
  const o = loadSaved(); o.hist = o.hist || {};
  for (const [k, vals] of Object.entries(entries)) {
    const cur = o.hist[k] || [];
    for (const v of [].concat(vals)) { const t = String(v || '').trim(); if (t && !/\[.*\]/.test(t)) { const i = cur.indexOf(t); if (i >= 0) cur.splice(i, 1); cur.unshift(t); } }
    o.hist[k] = cur.slice(0, 40);
  }
  writeSaved(o);
}

const allowOf = row => { const y = num(row && row.yield); return (y && S.fos > 0) ? Math.floor(y / S.fos + 1e-9) : null; };
const govRow = () => S.mat.rows[S.mat.gov] || S.mat.rows[0];
const govAllow = () => allowOf(govRow());

// ───────────────────────────── overlays ─────────────────────────────
let busyTimer = null;
function busy(msg, onCancel) {
  const el = $('#busy'), cb = $('#busy-cancel'), extra = $('#busy-extra');
  clearInterval(busyTimer); busyTimer = null;
  if (!msg) { el.hidden = true; cb.hidden = true; extra.textContent = ''; return; }
  $('#busy-msg').textContent = msg; extra.textContent = '';
  cb.hidden = !onCancel; cb.onclick = onCancel || null;
  el.hidden = false;
  const t0 = Date.now();                                      // shows that the program is alive, however long it takes
  busyTimer = setInterval(() => {
    const s = Math.round((Date.now() - t0) / 1000);
    extra.textContent = s < 4 ? '' : s < 25 ? `${s} s` : `${s} s - large pictures take longer. Still working ...`;
  }, 1000);
}
function showAlert(content, kind = 'bad') {
  const a = $('#alert'); a.className = 'alert ' + kind; a.replaceChildren(content); a.hidden = false;
  a.scrollIntoView({ block: 'nearest' });
}
const hideAlert = () => { $('#alert').hidden = true; };
window.addEventListener('error', ev => {
  console.error('page error:', ev.error || ev.message);
  try { showAlert(h('div', {}, h('b', {}, 'The page hit a snag: '), String(ev.message),
    ' Nothing you typed is lost. Please send a screenshot of this message.'), 'warn'); } catch (e) { /* ignore */ }
});
window.addEventListener('unhandledrejection', ev => {
  console.error('page rejection:', ev.reason);
  try { showAlert(h('div', {}, h('b', {}, 'The page hit a snag: '), String((ev.reason && ev.reason.message) || ev.reason),
    ' Nothing you typed is lost. Please send a screenshot of this message.'), 'warn'); } catch (e) { /* ignore */ }
});
const ICONS = {
  refresh: '<path d="M20 12a8 8 0 1 1-2.5-5.8M20 4v5.2h-5.2"/>', expand: '<path d="M4 9V4h5M20 9V4h-5M4 15v5h5M20 15v5h-5"/>',
  download: '<path d="M12 4v11m0 0l-4.5-4.5M12 15l4.5-4.5M5 20h14"/>', eye: '<path d="M2 12s3.7-7 10-7 10 7 10 7-3.7 7-10 7S2 12 2 12z"/><circle cx="12" cy="12" r="3"/>',
};
function ico(name) {          // small line icon (inline SVG: no font or image files needed)
  const svg = document.createElementNS('http://www.w3.org/2000/svg', 'svg');
  svg.setAttribute('viewBox', '0 0 24 24'); svg.setAttribute('class', 'ico'); svg.setAttribute('aria-hidden', 'true');
  svg.innerHTML = ICONS[name]; return svg;
}
function setStep(stage) { $$('#steps li').forEach(li => li.classList.toggle('on', li.dataset.stage === stage)); }
function show(stage) {
  for (const s of ['upload', 'review']) $('#s-' + s).hidden = s !== stage;
  $('#bar').hidden = stage !== 'review';
  setStep(stage);
  if (stage === 'upload') renderSessionBar();
  window.scrollTo({ top: 0 });
}

// ───────────────────────────── going back / starting over ─────────────────────────────
function backToUpload() {
  show('upload');
  renderStage();
}

function renderSessionBar() {
  const bar = $('#session-bar'); if (!bar) return;
  if (!S.haveSession || !S.sid) { bar.hidden = true; bar.replaceChildren(); return; }
  bar.hidden = false;
  bar.replaceChildren(h('div', { class: 'resume' },
    h('div', { class: 'grow' }, h('b', {}, `A report is already open (${S.images.length} pictures).`), h('br'),
      h('span', {}, 'Add pictures from another folder below - they join this report. Or start a fresh one.')),
    h('button', { class: 'primary', type: 'button', onclick: () => show('review') }, 'Re-open the draft'),
    h('button', { class: 'secondary', type: 'button', onclick: restartAll }, 'Start a new report')));
}

function restartAll() {
  modal('Start a new report?', h('div', {}, h('p', {}, 'The pictures and everything you typed in THIS report are thrown away. ' +
    'The report file you already downloaded is not touched.')), [
    { label: 'Keep this report', run: null },
    { label: 'Start over', primary: true, run: async () => {
        const sid = S.sid;
        Object.assign(S, { sid: null, haveSession: false, took: 0, skipped: [], images: [], cover: {}, short: '', images2: undefined,
                           mat: { rows: [], gov: 0 }, defLimit: '', geometry: [], meshes: [], meshStats: {}, assumed: {}, assumeCustom: '',
                           cases: [], warnings: [], checks: [], suggest: null, layoutPlan: null, build: freshBuild() });
        STAGE = []; STAGE_BATCH = []; renderStage(); renderSessionBar();
        if (sid) await post('/api/discard', { sid }).catch(() => {});
        show('upload');
      } }]);
}
function toast(content, kind = 'ok', ms = 9000) {
  const t = h('div', { class: 'toast ' + kind, role: 'status' }, h('div', { class: 'tx' }, content),
    h('button', { class: 'x', type: 'button', 'aria-label': 'Close', onclick: () => t.remove() }, '\u2715'));
  $('#toasts').append(t);
  if (ms) setTimeout(() => t.remove(), ms);
  return t;
}
function modal(title, body, buttons) {
  const m = $('#modal');
  m.replaceChildren(h('div', { class: 'box', role: 'dialog', 'aria-modal': 'true' }, h('h3', {}, title), body,
    h('div', { class: 'btns' }, buttons.map(b => h('button', { class: b.primary ? 'primary' : 'secondary', type: 'button',
      onclick: () => { m.hidden = true; if (b.run) b.run(); } }, b.label)))));
  m.hidden = false;
}
function lightbox(url) { const lb = $('#lightbox'); $('img', lb).src = url; lb.hidden = false; }
document.addEventListener('keydown', e => { if (e.key === 'Escape') { $('#lightbox').hidden = true; $('#modal').hidden = true; } });
document.addEventListener('click', e => { if (e.target.closest('#lightbox')) $('#lightbox').hidden = true; });

// ───────────────────────────── boot + upload ─────────────────────────────
async function boot() {
  bindUpload();
  try { S.status = await api('/api/status'); }
  catch (e) { showAlert(e.message); return; }
  try {
    renderChips();
    $('#btn-example').hidden = !S.status.has_examples;
    S.missing = loadSaved().missing || S.status.settings.missing_pictures || 'skip';
    if (!S.status.ocr) {
      showAlert(h('div', {}, h('b', {}, 'Heads-up: '), S.status.ocr_message, ' ',
        h('button', { class: 'link', type: 'button', onclick: () => location.reload() }, 'Check again')), 'warn');
    }
    checkResume();
  } catch (e) {
    console.error('boot:', e);
    showAlert(h('div', {}, h('b', {}, 'The page could not finish starting: '), e.message,
      ' Please reload once - if it returns, send a screenshot of this message.'), 'warn');
  }
}

function renderChips() {
  const st = S.status;
  const chip = (cls, label, text) => h('span', { class: 'chip ' + cls }, h('i'), `${label}: ${text}`);
  $('#chips').replaceChildren(
    h('span', { class: 'chip ver', id: 'chip-version', title: 'Version of this program. If you do not see the version you expect, an older copy is still open.' }, 'v' + st.version),
    chip(st.ocr ? 'ok' : 'info', 'Picture reader', st.ocr ? 'ready' : 'manual mode'),
    chip('ok', 'PDF', st.pdf_method === 'libreoffice' ? 'via LibreOffice' : 'from slide pictures'));
}

function bindUpload() {
  const drop = $('#drop'), file = $('#file');
  const stage = f => { const fl = Array.from(f).filter(x => /\.(png|jpe?g|bmp|tiff?|webp)$/i.test(x.name)); if (fl.length) stageFiles(fl); };
  file.addEventListener('change', () => { stage(file.files); file.value = ''; });
  ['dragenter', 'dragover'].forEach(ev => drop.addEventListener(ev, e => { e.preventDefault(); drop.classList.add('over'); }));
  ['dragleave', 'drop'].forEach(ev => drop.addEventListener(ev, e => { e.preventDefault(); drop.classList.remove('over'); }));
  drop.addEventListener('drop', e => { const fl = Array.from(e.dataTransfer.files); if (fl.length) stageFiles(fl); });
  window.addEventListener('dragover', e => e.preventDefault());
  window.addEventListener('drop', e => e.preventDefault());
  $('#btn-add-more').addEventListener('click', () => $('#file').click());
  $('#btn-add-folder').addEventListener('click', () => { const i = $('#folder'); if (i) i.click(); else showAlert('Your browser cannot pick a whole folder - add the pictures of each folder one by one.', 'warn'); });
  $('#folder').addEventListener('change', e => { stage(e.target.files); e.target.value = ''; });
  $('#btn-stage-next').addEventListener('click', nextFromStage);
  $('#btn-stage-back').addEventListener('click', () => { unstageBatch(); });
  $('#btn-stage-clear').addEventListener('click', () => { STAGE = []; STAGE_BATCH = []; renderStage(); });
  $('#btn-example').addEventListener('click', loadExample);
  $('#btn-preview').addEventListener('click', () => { const c = $('#pv-card'); if (c) c.scrollIntoView({ block: 'start', behavior: 'smooth' }); buildNow().catch(() => {}); });
  $('#btn-back').addEventListener('click', backToUpload);
  $('#btn-pdf').addEventListener('click', () => downloadReport('pdf'));
  $('#btn-pptx').addEventListener('click', () => downloadReport('pptx'));
  bindSlideBox();
}

function stageFiles(files) {
  const usable = [...files].filter(f => f && f.name && f.size > 0);
  if (usable.length !== files.length) toast('Some entries had no name or no content - they were left out.', 'warn', 8000);
  if (!usable.length) return;
  files = usable;
  const known = new Set(STAGE.map(s => s.name + s.size));
  const at = STAGE.length;                                   // where this batch starts: "Back" cuts back to here
  let dup = 0, added = 0;
  for (const f of files) {
    if (known.has(f.name + f.size)) { dup++; continue; }
    known.add(f.name + f.size);
    STAGE.push({ name: f.name, size: f.size, file: f });
    added++;
  }
  if (added) STAGE_BATCH.push(at);
  renderStage();
  if (dup) toast(`${dup} file${dup > 1 ? 's' : ''} were already in the list - added once.`, 'info', 6000);
}

function unstageBatch() {
  if (!STAGE_BATCH.length) { STAGE = []; renderStage(); return; }
  const at = STAGE_BATCH.pop();
  STAGE = STAGE.slice(0, Math.max(0, at));
  renderStage();
}

function renderStage() {
  const wrap = $('#stage-list'), bar = $('#stage-bar');
  if (!wrap) return;
  bar.hidden = STAGE.length === 0;
  $('#drop').classList.toggle('small', STAGE.length > 0);
  $('#btn-stage-back').disabled = STAGE.length === 0;
  $('#btn-stage-next').disabled = STAGE.length === 0;
  $('#stage-count').textContent = `${STAGE.length} picture${STAGE.length === 1 ? '' : 's'} ready`;
  wrap.replaceChildren(...STAGE.map((s, k) => h('div', { class: 'stg' },
    h('span', { class: 'stg-n', title: s.name }, s.name),
    h('span', { class: 'stg-s' }, s.size > 1048576 ? (s.size / 1048576).toFixed(1) + ' MB' : Math.round(s.size / 1024) + ' kB'),
    h('button', { class: 'icon', type: 'button', title: 'Remove this picture', onclick: () => {
      STAGE.splice(k, 1); STAGE_BATCH = STAGE_BATCH.map(b => (b > k ? b - 1 : b)); renderStage(); } }, '\u2715'))));
}

async function nextFromStage() {
  if (!STAGE.length) return;
  const pics = STAGE.map(s => s.file).filter(f => f && f.name && f.size > 0);
  if (!pics.length) { showAlert('The pictures in the list could not be read any more (the browser dropped them). Add them again - nothing else is lost.'); return; }
  if (S.haveSession && S.sid) { await addFiles(pics); return; }   // adding to a report that is already open
  const ac = new AbortController();
  busy(`Reading ${pics.length} picture${pics.length > 1 ? 's' : ''} ... a few seconds`, () => ac.abort());
  const fd = new FormData();
  pics.forEach(f => fd.append('files', f, f.name));
  try {
    startReview(await api('/api/analyze', { method: 'POST', body: fd, signal: ac.signal }));
    STAGE = []; STAGE_BATCH = [];
  } catch (e) {
    if (ac.signal.aborted) showAlert('Cancelled. The pictures are still in the list - press Next when you are ready.', 'warn');
    else showAlert(e.message);
  } finally { busy(false); }
}

async function addFiles(files) {
  const ac = new AbortController();
  busy(`Reading ${files.length} more picture${files.length > 1 ? 's' : ''} ...`, () => ac.abort());
  const fd = new FormData();
  fd.append('sid', S.sid);
  files.forEach(f => fd.append('files', f, f.name));
  try {
    const r = await api('/api/analyze_more', { method: 'POST', body: fd, signal: ac.signal });
    mergeAnalysis(r, true);
    STAGE = []; STAGE_BATCH = []; renderStage();
    show('review');
    toast(`${r.added} picture${r.added === 1 ? '' : 's'} added to this report.` + (r.skipped.length ? ` Skipped: ${r.skipped.join(', ')}` : ''), 'ok', 12000);
  } catch (e) {
    if (ac.signal.aborted) { showAlert('Cancelled.', 'warn'); return; }
    if (/not found|no such session|404|restarted/i.test(e.message)) {          // the old session died (program restarted): start a fresh one with these pictures
      S.haveSession = false; S.sid = null;
      return nextFromStage();
    }
    showAlert(e.message);
  } finally { busy(false); }
}

function mergeAnalysis(r, keepInputs) {
  // fold a fresh (or extended) analysis into the page state without losing what the engineer typed
  S.images = r.images || S.images; S.warnings = r.warnings || [];
  if (!keepInputs) return;
  const oldCases = new Map(S.cases.map(c => [c.n, c]));
  const oldGeo = new Map(S.geometry.map(g => [g.id, g]));
  const oldMesh = new Map(S.meshes.map(g => [g.id, g]));
  S.geometry = (r.geometry || []).map(g => { const o = oldGeo.get(g.id); return Object.assign({}, g, { heading: o ? o.heading : g.heading, caption: o ? o.caption : '', attach: o && o.attach !== undefined ? o.attach : g.attach, parent: o ? o.parent : g.parent }); });
  S.meshes = (r.meshes || []).map(g => { const o = oldMesh.get(g.id); return Object.assign({}, g, { heading: o ? o.heading : g.heading, caption: o ? o.caption : '', attach: o && o.attach !== undefined ? o.attach : g.attach, parent: o ? o.parent : g.parent }); });
  S.cases = (r.cases || []).map(dc => { const o = oldCases.get(dc.n); return o ? mergeCase(o, dc) : newCase(dc); });
  if ((r.match_pending || []).length) {
    S.matchPending = new Set([...(S.matchPending || []), ...r.match_pending]);
    pollMatches();
  }
}

let matchPoll = null;
function pollMatches() {
  // the server finishes placing zoom / section views in the background so "Next" stays quick; collect the answers here
  if (matchPoll || !S.sid) return;
  let tries = 0;
  const stop = () => { clearInterval(matchPoll); matchPoll = null; };
  matchPoll = setInterval(async () => {
    tries++;
    if (tries > 120) { stop(); return; }
    let r;
    try { r = await api(`/api/matches?sid=${encodeURIComponent(S.sid)}`); } catch (e) { return; }
    let changed = false;
    for (const [id, m] of Object.entries(r.matches || {})) {
      const pid = Number(id), img = S.images.find(x => x.id === pid);
      if (!img) continue;
      const pimg = (m.parent === null || m.parent === undefined) ? null : S.images.find(x => x.id === m.parent);
      if (JSON.stringify(img.match || null) !== JSON.stringify(m.match || null)) { img.match = m.match || null; changed = true; }
      if (pimg && img.parent !== m.parent) { img.parent = m.parent; img.parent_why = m.parent_why; changed = true; }
      if (img.match_ambiguous !== m.match_ambiguous) { img.match_ambiguous = m.match_ambiguous; changed = true; }
      for (const c of S.cases) for (const e of (c.extras || [])) if (e.id === pid) {
        if (JSON.stringify(e.match || null) !== JSON.stringify(m.match || null)) { e.match = m.match || null; changed = true; }
        if (pimg && e.parent !== m.parent) { e.parent = m.parent; e.parent_name = pimg.name; changed = true; }
      }
    }
    S.matchPending = new Set(r.pending || []);
    if (changed && !$('#s-review').hidden) { renderReview(); scheduleBuild(); }
    if (!S.matchPending.size) {
      stop();
      if (changed) toast('The zoomed and section views are placed next to their pictures now - press Update preview to see the marks.', 'ok', 9000);
    }
  }, 900);
}

function mergeCase(o, dc) {
  const c = Object.assign({}, o);
  const bcChanged = o.bc_id !== dc.bc_id;
  c.bc_id = dc.bc_id; c.def_id = dc.def_id; c.stress_id = dc.stress_id;
  c.stress_red_px = dc.stress_red_px; c.stress_legend = dc.stress_legend; c.stress_unit = dc.stress_unit; c.def_unit = dc.def_unit;
  c.gravity = dc.gravity; c.legend_px = dc.legend_px; c.deck = dc.deck; c.model = dc.model;
  c.extras = dc.extras.map(e => {
    const oe = (o.extras || []).find(x => x.id === e.id);
    return Object.assign({}, e, { heading: oe ? oe.heading : e.heading, caption: oe ? oe.caption : '',
                                  attach: e.attach !== undefined ? e.attach : true, pos: e.pos || 'auto' });
  });
  if (bcChanged) { c.bc_items = dc.bc_items.slice(); c.bc_legend = dc.bc_legend.slice(); c.name = dc.name; c.subtitle = dc.subtitle; c.short_name = dc.short_name; }
  if (o.def_id !== dc.def_id) c.def_max = nice(dc.def_max);
  if (o.stress_id !== dc.stress_id) c.stress_max = nice(dc.stress_max);
  return c;
}

async function sendFiles(files) {
  const pics = files.filter(f => /\.(png|jpe?g|bmp|tiff?|webp)$/i.test(f.name));
  if (!pics.length) { showAlert('Please choose picture files (PNG or JPG).'); return; }
  const ac = new AbortController();
  busy(`Reading ${pics.length} picture${pics.length > 1 ? 's' : ''} ... a few seconds`, () => ac.abort());
  const fd = new FormData();
  pics.forEach(f => fd.append('files', f, f.name));
  try { startReview(await api('/api/analyze', { method: 'POST', body: fd, signal: ac.signal })); }
  catch (e) { if (ac.signal.aborted) showAlert('Cancelled. Choose the pictures again when you are ready.', 'warn'); else showAlert(e.message); }
  finally { busy(false); }
}

async function loadExample() {
  const ac = new AbortController();
  busy('Reading the 9 example pictures ...', () => ac.abort());
  try { startReview(await api('/api/analyze_example', { method: 'POST', signal: ac.signal })); }
  catch (e) { if (ac.signal.aborted) showAlert('Cancelled.', 'warn'); else showAlert(e.message); }
  finally { busy(false); }
}

// ───────────────────────────── auto-save + resume ─────────────────────────────
function snapshot() { const o = { ver: 2 }; for (const k of STATE_KEYS) o[k] = S[k]; return JSON.parse(JSON.stringify(o)); }
let saveTimer = null;
function scheduleSave() {
  if (!S.sid || $('#s-review').hidden) return;
  scheduleBuild();
  clearTimeout(saveTimer);
  saveTimer = setTimeout(() => { post('/api/state', { sid: S.sid, state: snapshot() }).catch(() => {}); }, 800);
}
const ago = t => { const m = Math.max(0, Math.round((Date.now() / 1000 - t) / 60)); return m < 1 ? 'just now' : m < 60 ? `${m} min ago` : m < 1440 ? `${Math.round(m / 60)} h ago` : `${Math.round(m / 1440)} days ago`; };
async function checkResume() {
  try {
    const r = await api('/api/last');
    if (!r.found || !r.state || r.state.ver !== 2) return;
    $('#resume').replaceChildren(h('div', { class: 'resume' },
      h('div', { class: 'grow' }, h('b', {}, 'Continue your last report?'), h('br'),
        h('span', {}, `${r.title || 'Unnamed report'}  \u00b7  ${r.pictures} pictures  \u00b7  saved ${ago(r.saved_at)}`)),
      h('button', { class: 'primary', type: 'button', onclick: () => resume(r) }, 'Continue'),
      h('button', { class: 'secondary', type: 'button', onclick: async () => { await post('/api/discard', { sid: r.sid }).catch(() => {}); $('#resume').replaceChildren(); } }, 'Discard')));
  } catch (e) { /* nothing to resume */ }
}
function resume(r) {
  hideAlert();
  S.sid = r.sid; S.images = r.images;
  for (const k of STATE_KEYS) if (r.state[k] !== undefined) S[k] = r.state[k];
  S.checks = []; S.suggest = null; S.layoutPlan = null; S.build = freshBuild(); S.missing = r.state.missing || S.missing;
  $('#resume').replaceChildren();
  S.haveSession = true;
  show('review'); renderReview(); scheduleDerive();
}

// ───────────────────────────── start the review ─────────────────────────────
function newCase(dc) {
  return Object.assign({}, dc, {
    def_max: nice(dc.def_max), stress_max: nice(dc.stress_max), notes: [], texts: {}, dirty: {}, obs: [], exceeds: null, tier: null,
    needsBasis: false, bc_items: dc.bc_items.slice(), bc_legend: (dc.bc_legend || []).slice(),
    basis: '', sing_loc: '', stress_away: '', loc_stress: '', loc_def: '', react_applied: '', react_sum: '', layout: '', bc_layout: '',
    extras: (dc.extras || []).map(e => Object.assign({}, e, { caption: '', attach: e.attach !== undefined ? e.attach : true,
                                                              pos: e.pos || 'auto', parent: e.parent !== null && e.parent !== undefined ? e.parent : null })),
  });
}

function startReview(r) {
  hideAlert();
  const cv = r.cover || {};
  Object.assign(S, { sid: r.sid, took: r.took, skipped: r.skipped || [], images: r.images || [], warnings: r.warnings || [],
                     checks: [], suggest: null,
                     layoutPlan: null, build: freshBuild(), manual: !!r.manual });
  S.cover = { title: cv.title || '', report_no: cv.report_no || '', date: cv.date || '', client: cv.client || '',
              revision: '', prepared_by: '', checked_by: '', ref: '' };
  S.short = cv.short || '';
  const st = S.status.settings, saved = loadSaved();
  S.fos = saved.fos || st.fos; S.fosText = String(S.fos); S.units = saved.units || st.units;
  S.showUtil = saved.showUtil !== undefined ? saved.showUtil : st.show_utilisation;
  const m = Object.assign({}, st.material, saved.material || {});
  S.mat = { rows: [{ material: m.material, item: m.item, E: m.E, nu: m.nu, rho: m.rho, yield: '' }], gov: 0 };
  S.defLimit = '';
  S.meshStats = { type: '', elements: '', nodes: '', size: '', skew_avg: '', skew_max: '', oq_min: '' };
  S.assumed = {}; S.assumeCustom = '';
  S.layouts = { results: 'auto', geometry: 'auto', bc: 'auto', extras: 'auto' };
  S.geometry = (r.geometry || []).map(g => ({ id: g.id, heading: g.heading, caption: '', parent: g.parent || null, attach: !!g.attach }));
  S.meshes = (r.meshes || []).map(g => ({ id: g.id, heading: g.heading, caption: '', parent: g.parent || null, attach: !!g.attach }));
  S.cases = (r.cases || []).map(newCase);
  S.matchPending = new Set(r.match_pending || []);
  S.haveSession = true;
  show('review');
  renderReview();
  scheduleDerive();                       // the preview is NOT built here - the engineer asks for it (council item U3)
  if (S.matchPending.size) pollMatches(); // but the view placement keeps arriving in the background
}

// ───────────────────────────── review page ─────────────────────────────
function card(num, title, sub, ...body) {
  return h('section', { class: 'card' }, h('h2', {}, num ? h('span', { class: 'num' }, num) : null, title),
    sub ? h('p', { class: 'sub' }, sub) : null, ...body);
}

function thumb(id, cls = 'thumb', w = 240) {
  if (id === null || id === undefined) return h('div', { class: cls + ' missing' }, 'none');
  const url = `/api/img/${S.sid}/${id}`;
  const im = h('img', { class: cls, src: `${url}?w=${w}`, alt: '', title: 'Click to enlarge' });
  im.addEventListener('click', () => lightbox(url));
  return im;
}

function evid(id, kind, caption, w = 300) {
  if (id === null || id === undefined) return null;
  const im = h('img', { src: `/api/crop/${S.sid}/${id}/${kind}?w=${w}`, alt: caption, title: 'Click to enlarge' });
  im.addEventListener('click', () => lightbox(`/api/crop/${S.sid}/${id}/${kind}?w=900`));
  return h('figure', { class: 'evid' }, im, h('figcaption', {}, caption));
}

function inp(obj, key, o = {}) {
  const el = h('input', { type: 'text', value: nice(obj[key]), placeholder: o.ph, 'data-need': o.need, 'data-label': o.label,
                          'data-casename': o.casename, 'aria-label': o.label, list: o.list });
  el.addEventListener('input', () => { obj[key] = el.value; if (o.onchange) o.onchange(el); updateNeeds(); });
  return el;
}
function field(label, obj, key, o = {}) {
  return h('label', { class: 'f ' + (o.span || '') }, h('span', {}, label, o.optional ? h('span', { class: 'opt' }, '  (optional)') : null),
    inp(obj, key, Object.assign({ label }, o)), o.hint ? h('small', {}, o.hint) : null);
}

function readSummary(i) {
  const bits = [];
  if (['deformation', 'stress', 'x_deformation', 'x_stress', 'x_other'].includes(i.role))
    bits.push(i.max !== null && i.max !== undefined ? `Max ${i.max} ${i.unit || ''}` : 'Max not read');
  if (i.role === 'x_other' && i.result_kind) bits.push(i.result_kind);
  if (i.view_kind) bits.push(i.view_kind === 'section' ? 'section view' : 'zoom / detail view');
  if (i.role === 'bc' || i.role === 'x_bc') bits.push(`${i.bc_legend.length} load / support entries`);
  if (i.role === 'geometry') bits.push(i.z_dir === 'up' ? 'Z axis points up: top view' : i.z_dir === 'down' ? 'Z axis points down: bottom view' : 'view direction unknown');
  if (i.role === 'mesh') bits.push('mesh pattern');
  if (i.letter) bits.push(`ANSYS case ${i.letter}`);
  if (i.confidence === 'medium' && ['geometry', 'mesh'].includes(i.role)) bits.push('no ANSYS title: told apart by how the picture looks');
  if (i.dup_of !== null && i.dup_of !== undefined) bits.push('same picture as another one: used once');
  return bits.join('  \u00b7  ');
}

function safePart(name, fn) {
  // a card that chokes on one odd picture must never take the whole page with it
  try { return fn(); }
  catch (e) {
    console.error('card failed:', name, e);
    return card('', `One card could not be shown (${name})`,
      h('div', {}, 'The rest of the page works. This card failed: ', h('b', {}, e.message),
        ' - please send a screenshot of this message; nothing you typed is lost.'));
  }
}

function renderReview() {
  const root = $('#s-review');
  // the preview is the LAST card: you work top to bottom and look at the report when you ask for it (council item U3)
  const parts = [safePart('intro', introCard), safePart('checks', checksCard), safePart('pictures', picturesCard),
                 safePart('cover', coverCard), safePart('material', materialCard), safePart('layouts', layoutCard),
                 safePart('geometry', geometryCard), safePart('assumptions', assumptionsCard)];
  if (S.cases.length) S.cases.forEach((c, k) => parts.push(safePart('case ' + (k + 1), () => caseCard(c, k))));
  else parts.push(card('7', 'Load cases', 'No load case found. Use the pictures table above to say which pictures are setup, deformation and stress pictures.'));
  parts.push(safePart('preview', previewCard));
  const dl = h('div', { hidden: true });
  for (const k of HIST_KEYS) dl.append(h('datalist', { id: 'dl-' + k }, hist(k).map(v => h('option', { value: v }))));
  parts.push(dl);
  root.replaceChildren(...parts);
  for (const [nm, fn] of [['checks', renderChecks], ['verdicts', renderVerdicts], ['hint', renderHint],
                          ['allowable', refreshAllowable], ['layoutcard', renderLayoutCard], ['basis', renderBasisBoxes],
                          ['needs', updateNeeds], ['preview', renderPreview]]) {
    try { fn(); } catch (e) { console.error('render', nm, e); }
  }
}

function introCard() {
  return h('section', { class: 'card intro' },
    h('h2', {}, `I read ${S.images.length} picture${S.images.length > 1 ? 's' : ''} in ${S.took} s. Here is the first draft.`),
    h('p', {}, 'The preview at the BOTTOM of this page is your real report. It is built when you press "Update preview" - never by itself. ', h('span', { class: 'need-legend' }, 'Yellow boxes'),
      ' need your input, but nothing blocks you: you can download at any time, and whatever is not filled in appears as [text in brackets] in the report. ',
      h('button', { class: 'link', type: 'button', onclick: () => { show('upload'); } }, 'Use other pictures')),
    S.manual ? h('p', { class: 'manual-note' }, h('b', {}, 'Manual mode: '),
      'the picture reader is not installed, so the pictures were sorted by their file names and the numbers are left for you to type.') : null);
}

function checksCard() {
  return h('section', { class: 'card', id: 'checks-card' }, h('h2', {}, 'Checks'),
    h('p', { class: 'sub' }, 'Things I noticed while reading the pictures. They update as you type.'), h('ul', { class: 'checks', id: 'checks' }));
}

function renderChecks() {
  const ul = $('#checks'); if (!ul) return;
  const items = [...S.warnings, ...S.checks, ...localChecks()];
  const order = { bad: 0, warn: 1, info: 2, ok: 3 }, icon = { bad: '\u2716', warn: '!', info: 'i', ok: '\u2713' };
  items.sort((a, b) => order[a.level] - order[b.level]);
  if (!items.length) items.push({ level: 'ok', text: 'Nothing unusual found.' });
  ul.replaceChildren(...items.map(it => h('li', { class: it.level }, h('b', {}, icon[it.level]), h('span', {}, it.text))));
}

function localChecks() {
  const out = [];
  const names = S.cases.map(c => (c.name || '').trim().toLowerCase());
  if (names.some((n, i) => names.indexOf(n) !== i))
    out.push({ level: 'warn', text: 'Two load cases have the same name. Give every case its own name (what is different between them?).' });
  S.skipped.forEach(f => out.push({ level: 'info', text: `Skipped (not a picture file): ${f}` }));
  return out;
}

// ----- pictures table
function picturesCard() {
  const rows = [];
  S.images.forEach(img => {
    const role = h('select', { class: 'sel', 'data-need': 'role', 'data-label': `What is ${img.name}`, 'aria-label': `What is ${img.name}` }, roleOptions(img));
    const isCase = CASE_ROLES.has(img.role);
    const cs = h('select', { class: 'cs', disabled: !isCase, 'data-need': isCase ? 'req' : null, 'data-label': `Load case of ${img.name}`, 'aria-label': `Load case of ${img.name}` },
      isCase && !img.case ? h('option', { value: '', selected: true }, 'Choose ...') : null,
      Array.from({ length: 12 }, (_, i) => i + 1).map(n => h('option', { value: n, selected: n === img.case }, 'Case ' + n)));
    role.addEventListener('change', () => {
      const [r, vk, rk] = role.value.split('|');
      img.role = r; img.view_kind = vk || ''; img.rkind = rk || '';
      if (CASE_ROLES.has(img.role) && !img.case) img.case = 1;
      regroup();
    });
    cs.addEventListener('change', () => { img.case = cs.value ? parseInt(cs.value, 10) : null; regroup(); });
    const attn = img.notes.length || img.confidence === 'low' || img.role === 'unknown';
    const more = h('tr', { class: 'picmore', hidden: true }, h('td', { colspan: 5 }, pictureEvidence(img)));
    const tg = h('button', { class: 'icon', type: 'button', title: 'Show what I read in this picture',
      onclick: () => { more.hidden = !more.hidden; tg.classList.toggle('on', !more.hidden); } }, 'i');
    rows.push(h('tr', { class: attn ? 'attn' : '' }, h('td', {}, h('div', { class: 'thwrap' }, thumb(img.id), tg)),
      h('td', { class: 'fn' }, img.name), h('td', {}, role), h('td', {}, cs), h('td', { class: 'read' }, readSummary(img))), more);
  });
  return card('1', 'Pictures', 'I sorted your pictures like this - the load case comes from the ANSYS title block of each picture (the "i" shows what I read). ' +
    'If one is wrong, change it here. A section or a zoom is attached to the picture it belongs to and goes on the SAME slide.',
    h('div', { class: 'scroll' }, h('table', { class: 'pics' },
      h('thead', {}, h('tr', {}, ['', 'File', 'What is it?', 'Load case', 'What I read'].map(t => h('th', {}, t)))), h('tbody', {}, rows))));
}

function pictureEvidence(img) {
  const bits = [];
  bits.push(h('div', { class: 'ev' }, h('b', {}, 'ANSYS title block: '),
    h('span', {}, img.headline || '(none read - the type was guessed from the file name)')));
  bits.push(h('div', { class: 'ev' }, h('b', {}, 'Deck (project): '), h('span', {}, img.deck || '(not readable)'),
    '  \u00b7  ', h('b', {}, 'case letter: '), h('span', {}, img.letter ? `${img.letter}${img.letter_conf === 'low' ? ' (read without its colon - check it)' : ''}` : '(none readable)'),
    '  \u00b7  ', h('b', {}, 'load case: '), h('span', {}, img.case ? `Case ${img.case}` : 'not assigned yet')));
  const ex = extraOf(img.id);
  if (ex) {
    const parents = S.cases.find(c => c.n === img.case);
    const opts = [['def_id', 'the total-deformation picture'], ['stress_id', 'the von-Mises picture'], ['bc_id', 'the setup picture']]
      .filter(([k]) => parents && parents[k] !== null && parents[k] !== undefined && parents[k] !== img.id);
    const psel = h('select', { 'aria-label': `Which picture is ${img.name} attached to` },
      opts.map(([k, t]) => h('option', { value: parents[k], selected: ex.parent === parents[k] }, t)));
    psel.addEventListener('change', () => { ex.parent = parseInt(psel.value, 10); regroup(); });
    const asel = h('select', { 'aria-label': 'Where this view is placed' },
      h('option', { value: 'same', selected: !!ex.attach }, 'On the same slide as that picture'),
      h('option', { value: 'own', selected: !ex.attach }, 'On a slide of its own'));
    asel.addEventListener('change', () => { ex.attach = asel.value === 'same'; scheduleSave(); renderPreviewStale(); });
    const ssel = h('select', { 'aria-label': 'Which side the detail column is on' },
      h('option', { value: 'auto', selected: (ex.pos || 'auto') === 'auto' }, 'Side: where the detail sits in the picture'),
      h('option', { value: 'right', selected: ex.pos === 'right' }, 'Side: right'),
      h('option', { value: 'left', selected: ex.pos === 'left' }, 'Side: left'),
      h('option', { value: 'nomark', selected: ex.pos === 'nomark' }, 'Side: right, without the region mark'));
    ssel.addEventListener('change', () => { ex.pos = ssel.value; scheduleSave(); renderPreviewStale(); });
    const m = ex.match, pend = !!(S.matchPending && S.matchPending.has(ex.id));
    bits.push(h('div', { class: 'ev' }, h('b', {}, 'Attached to: '), psel, ' ', asel, ' ', ssel));
    bits.push(h('div', { class: 'ev' }, h('b', {}, 'Why: '), h('span', {},
      pend ? 'I am still looking for this view inside the other pictures - a second or two.'
      : m ? `I found this view inside "${ex.parent_name}" - the marked region is ${m.side === 'left' ? 'on the left' : 'on the right'}, ` +
          `${m.row === 'top' ? 'upper' : 'lower'} half (${Math.round(m.score * 100)} % alike).`
        : (ex.view_kind === 'detail' ? `This looks like a zoomed view, but I could not find that region inside "${ex.parent_name}" - it is placed beside it without a mark.`
                                     : ex.parent_why ? `It is attached to ${ex.parent_name} (${ex.parent_why}).` : `It is attached to ${ex.parent_name}.`))));
    if (ex.ambiguous) bits.push(h('div', { class: 'ev warn' }, 'Two pictures of this case match this view almost equally well - check the attachment above.'));
  }
  (img.notes || []).forEach(n => bits.push(h('div', { class: 'ev warn' }, n)));
  return h('div', { class: 'evbox' }, bits);
}

function extraOf(id) {
  for (const c of S.cases) { const e = (c.extras || []).find(x => x.id === id); if (e) return e; }
  return null;
}
function renderPreviewStale() { scheduleBuild(); }

// ----- report details
function coverCard() {
  return card('2', 'Report details', 'Shown on the cover page and used in the sentences.',
    h('div', { class: 'grid' },
      field('Report title', S.cover, 'title', { span: 'span2', need: 'title', hint: 'Made from the ANSYS model name: check it' }),
      field('Report number', S.cover, 'report_no', { need: 'req', hint: 'From the job number in the model name' }),
      field('Date', S.cover, 'date', { need: 'req' }),
      field('Client', S.cover, 'client', { span: 'span2', need: 'req', list: 'dl-client' }),
      field('Short name of the structure', S, 'short', { span: 'span2', need: 'req', onchange: scheduleDerive, list: 'dl-short',
        hint: 'Used in sentences such as "Total deformation in the ____ is ..."' })),
    h('p', { class: 'sublabel' }, 'Sign-off (printed on the cover only if filled in)'),
    h('div', { class: 'grid' },
      field('Revision', S.cover, 'revision', { optional: true }), field('Prepared by', S.cover, 'prepared_by', { optional: true, list: 'dl-prepared_by' }),
      field('Checked by', S.cover, 'checked_by', { optional: true, list: 'dl-checked_by' }), field('Drawing / model reference', S.cover, 'ref', { optional: true })));
}

// ----- material
function materialCard() {
  const wrap = h('div', { class: 'scroll', id: 'mat-wrap' });
  const fosEl = h('input', { type: 'text', value: S.fosText, 'data-need': 'num', 'data-label': 'Factor of safety (FOS)', style: 'width:90px', 'aria-label': 'FOS' });
  fosEl.addEventListener('input', () => {
    S.fosText = fosEl.value; const v = num(fosEl.value); if (v && v > 0) S.fos = v; refreshAllowable(); updateNeeds();
  });
  const limEl = h('input', { type: 'text', value: S.defLimit, style: 'width:100px', 'aria-label': 'Allowable deformation', placeholder: 'none', 'data-need': 'optnum', 'data-label': 'Allowable deformation' });
  limEl.addEventListener('input', () => { S.defLimit = limEl.value; scheduleDerive(); updateNeeds(); });
  const utilEl = h('input', { type: 'checkbox', checked: S.showUtil, 'aria-label': 'Show utilisation' });
  utilEl.addEventListener('change', () => { S.showUtil = utilEl.checked; scheduleDerive(); scheduleSave(); });
  const unitsEl = h('input', { type: 'text', value: S.units, 'data-need': 'req', 'data-label': 'Units line', 'aria-label': 'Units line' });
  unitsEl.addEventListener('input', () => { S.units = unitsEl.value; updateNeeds(); });
  const c = card('3', 'Material & acceptance criteria', 'Allowable stress = Yield stress \u00f7 FOS, rounded down (the formula on the material page of your template).',
    wrap,
    h('div', { class: 'rowtools' },
      h('button', { class: 'link', type: 'button', onclick: addMaterialRow }, '+ Add another material'),
      h('label', { class: 'unit' }, h('span', {}, 'Factor of safety (FOS)'), fosEl)),
    h('div', { class: 'hintbox', id: 'yield-hint', hidden: true }),
    h('div', { class: 'rowtools' },
      h('label', { class: 'unit' }, h('span', {}, 'Allowable deformation'), limEl, h('em', {}, 'mm  (optional)')),
      h('label', { class: 'chk' }, utilEl, 'Show utilisation (stress \u00f7 allowable) in the sentences')),
    h('label', { class: 'f', style: 'margin-top:14px;display:block' }, h('span', {}, 'Units line on the material page'), unitsEl));
  renderMaterialTable(wrap);
  return c;
}

function renderMaterialTable(wrap) {
  wrap = wrap || $('#mat-wrap');
  const head = S.status.settings.material_header, multi = S.mat.rows.length > 1;
  const th = t => h('th', {}, t.split('\n').flatMap((p, k) => (k ? [h('br'), p] : [p])));
  const keys = ['material', 'item', 'E', 'nu', 'rho', 'yield'];
  const body = S.mat.rows.map((r, idx) => {
    const cells = keys.map(k => {
      const el = h('input', { type: 'text', value: nice(r[k]), 'data-need': k === 'yield' ? 'num' : 'req', 'data-label': k === 'yield' ? 'Yield stress' : `Material: ${k}`,
                              'data-yield': k === 'yield' ? idx : null, placeholder: k === 'yield' ? 'type it' : null, 'aria-label': k });
      el.addEventListener('input', () => { r[k] = el.value; if (k === 'yield') refreshAllowable(); else scheduleDerive(); updateNeeds(); });
      return h('td', {}, el);
    });
    return h('tr', {}, ...cells, h('td', { class: 'auto', 'data-allow': idx }, '\u2013'),
      multi ? h('td', { class: 'gov' }, h('input', { type: 'radio', name: 'gov', checked: idx === S.mat.gov, title: 'Use this material for the pass / fail checks',
        onchange: () => { S.mat.gov = idx; refreshAllowable(); } })) : null,
      multi ? h('td', {}, h('button', { class: 'icon', type: 'button', title: 'Remove this row', onclick: () => {
        S.mat.rows.splice(idx, 1); S.mat.gov = Math.min(S.mat.gov, S.mat.rows.length - 1); renderMaterialTable(); refreshAllowable(); updateNeeds(); } }, '\u2715')) : null);
  });
  wrap.replaceChildren(h('table', { class: 'mat' },
    h('thead', {}, h('tr', {}, head.map(th), multi ? h('th', { class: 'gov' }, 'Used for checks') : null, multi ? h('th') : null)), h('tbody', {}, body)));
}

function addMaterialRow() {
  const r0 = S.mat.rows[0] || {};
  S.mat.rows.push({ material: r0.material || '', item: r0.item || '', E: r0.E || '', nu: r0.nu || '', rho: r0.rho || '', yield: '' });
  renderMaterialTable(); refreshAllowable(); updateNeeds();
}

function refreshAllowable() {
  $$('[data-allow]').forEach(td => { const a = allowOf(S.mat.rows[+td.dataset.allow]); td.textContent = a === null ? '\u2013' : a; });
  renderHint(); scheduleDerive();
}

function renderHint() {
  const box = $('#yield-hint'); if (!box) return;
  const sg = S.suggest;
  box.hidden = !sg; if (!sg) return;
  const row = govRow(), a = allowOf(row);
  if (a !== null && Math.abs(a - sg.allowable) <= 0.5) {
    box.className = 'hintbox okk';
    box.replaceChildren(`\u2713 Your yield stress gives an allowable of ${a} MPa: the same value the stress-picture legends are set to.`);
  } else {
    box.className = 'hintbox';
    box.replaceChildren(h('span', {}, h('b', {}, 'Tip: '), `the stress pictures use ${sg.allowable} MPa as the start of the top colour band. With FOS ${S.fos} that fits a yield stress of ${sg.yield} MPa.`),
      h('button', { type: 'button', onclick: () => {
        row.yield = String(sg.yield); const el = $(`[data-yield="${S.mat.rows.indexOf(row)}"]`); if (el) el.value = row.yield;
        refreshAllowable(); updateNeeds(); } }, `Use ${sg.yield} MPa`));
  }
}

// ----- layout (big / wide models)
function layoutCard() {
  return h('section', { class: 'card', id: 'layout-card' }, h('h2', {}, h('span', { class: 'num' }, '4'), 'Picture layout'),
    h('p', { class: 'sub' }, 'Big or wide models make the pictures small when two share a slide. The numbers show how large the text INSIDE your screenshots (legend, axes) will be on the slide. The template standard is about 5.5 pt.'),
    h('div', { id: 'layout-body' }));
}

function modeChip(p, m) {
  const o = p.options[m]; if (!o) return null;
  return h('span', { class: 'chipk ' + (o.pt >= p.thr ? 'good' : 'low') + (m === p.effective ? ' pick' : '') }, `${LAYOUT_NAMES[m]}: ${o.pt} pt`);
}

function renderLayoutCard() {
  const body = $('#layout-body'); if (!body) return;
  if (body.contains(document.activeElement) && document.activeElement.tagName === 'SELECT') return;
  const pl = S.layoutPlan;
  if (!pl) { body.replaceChildren(h('p', { class: 'sub' }, 'Calculating ...')); return; }
  const rows = [];
  const mk = (key, label, modes) => {
    const p = pl[key]; if (!p) return;
    const sel = h('select', { 'aria-label': label },
      h('option', { value: 'auto', selected: S.layouts[key] === 'auto' }, `Auto (recommended): ${LAYOUT_NAMES[p.auto]}`),
      modes.map(m => h('option', { value: m, selected: S.layouts[key] === m }, LAYOUT_NAMES[m])));
    sel.addEventListener('change', () => { S.layouts[key] = sel.value; scheduleDerive(); scheduleSave(); });
    rows.push(h('tr', {}, h('td', {}, label), h('td', {}, sel), h('td', { class: 'pt' }, modes.map(m => modeChip(p, m)))));
  };
  mk('results', 'Deformation + stress plots', ['side', 'stack', 'separate']);
  mk('bc', 'Setup picture + list', ['side', 'stack']);
  if (S.geometry.length > 1 || S.meshes.length > 1) mk('geometry', 'Geometry / mesh views', ['side', 'stack', 'separate']);
  if (pl.extras) mk('extras', 'Additional views', ['side', 'stack', 'separate']);
  const parts = [h('div', { class: 'scroll' }, h('table', { class: 'lay' }, h('thead', {}, h('tr', {}, ['Which pictures', 'Layout', 'Text size on the slide'].map(t => h('th', {}, t)))), h('tbody', {}, rows)))];
  if (S.cases.length > 1 && pl.results) {
    const crow = S.cases.map(c => {
      const e = pl.cases[String(c.n)] || {};
      const mkSel = (field, modes, label, cur) => {
        const sel = h('select', { 'aria-label': `${label} of case ${c.n}` }, h('option', { value: '', selected: !cur }, 'Same as above'),
          modes.map(m => h('option', { value: m, selected: cur === m }, LAYOUT_NAMES[m])));
        sel.addEventListener('change', () => { c[field] = sel.value; scheduleDerive(); scheduleSave(); });
        return sel;
      };
      return h('tr', {}, h('td', {}, `Case ${c.n}`), h('td', {}, mkSel('layout', ['side', 'stack', 'separate'], 'Result plots', c.layout)),
        h('td', {}, mkSel('bc_layout', ['side', 'stack'], 'Setup picture', c.bc_layout)),
        h('td', { class: 'pt' }, e.results ? ['side', 'stack', 'separate'].map(m => h('span', { class: 'chipk ' + (e.results[m].pt >= pl.results.thr ? 'good' : 'low') }, `${LAYOUT_NAMES[m]}: ${e.results[m].pt} pt`)) : null));
    });
    parts.push(h('details', { class: 'wording' }, h('summary', {}, 'A different layout for one load case (optional)'),
      h('div', { class: 'in' }, h('div', { class: 'scroll' }, h('table', { class: 'lay' }, h('thead', {}, h('tr', {}, ['', 'Result plots', 'Setup picture', 'Predicted text size'].map(t => h('th', {}, t)))), h('tbody', {}, crow))))));
  }
  body.replaceChildren(...parts);
}

// ----- geometry & mesh
function viewRow(v, k, n, label) {
  const first = (label === 'view' ? S.geometry : S.meshes)[0];
  const canAttach = n > 1 && first && first.id !== v.id;
  const att = h('input', { type: 'checkbox', checked: !!v.attach && canAttach, disabled: !canAttach, 'aria-label': `Attach ${label} ${k + 1} to the first view` });
  att.addEventListener('change', () => {
    v.attach = att.checked; v.parent = att.checked ? first.id : null;
    scheduleSave(); scheduleBuild(); renderPreview();
  });
  return h('div', { class: 'view' }, thumb(v.id, 'thumb big', 320),
    n > 1 ? h('div', { class: 'f' }, field(`Heading of ${label} ${k + 1}`, v, 'heading', { need: 'bracket', hint: k === 0 && label === 'view' ? 'Taken from the axis arrows in the picture' : null }),
      field('Caption', v, 'caption', { optional: true }),
      h('label', { class: 'chk' }, att, h('span', {}, `Show on the same slide as "${(first && first.heading) || 'the first view'}"`)))
          : h('div', { class: 'f' }, h('span', {}, label === 'view' ? 'Geometry' : 'Mesh'), h('small', {}, 'One picture: it gets its own slide.'),
              field('Caption', v, 'caption', { optional: true })));
}

function geometryCard() {
  const geo = S.geometry.map((g, k) => viewRow(g, k, S.geometry.length, 'view'));
  const mesh = S.meshes.map((g, k) => viewRow(g, k, S.meshes.length, 'mesh view'));
  const ms = S.meshStats;
  const mf = (label, key, hint) => h('label', { class: 'f' }, h('span', {}, label), inp(ms, key, { label, onchange: scheduleDerive }), hint ? h('small', {}, hint) : null);
  return card('5', 'Geometry & mesh', 'One slide each (two views are placed side by side). Section views of the model or of the mesh are just more views: give each a heading that says where the cut is.',
    geo.length ? h('div', { class: 'sublabel' }, 'Geometry') : null, geo.length ? h('div', { class: 'views' }, ...geo) : h('p', { class: 'hint' }, 'No geometry picture: that slide is simply left out. (Assign one in the pictures table if you have it.)'),
    h('div', { class: 'sublabel' }, 'Mesh'), mesh.length ? h('div', { class: 'views' }, ...mesh) : h('p', { class: 'hint' }, 'No mesh picture: that slide is simply left out. (Assign one in the pictures table if you have it.)'),
    h('details', { class: 'wording' }, h('summary', {}, 'Mesh statistics (optional): element count and quality, printed under the mesh picture'),
      h('div', { class: 'in' }, h('div', { class: 'mini-grid' },
        mf('Element type', 'type', 'e.g. SOLID187'), mf('Elements', 'elements'), mf('Nodes', 'nodes'), mf('Element size (mm)', 'size'),
        mf('Average skewness', 'skew_avg'), mf('Maximum skewness', 'skew_max', 'ANSYS: >= 0.9 is "bad"'), mf('Min. orthogonal quality', 'oq_min', 'ANSYS: keep above 0.1')),
        h('small', { class: 'hint' }, 'Reviewers nearly always ask for these. Read them from "Details of Mesh" in ANSYS. The limits used for the warnings are in settings.json.'))));
}

// ----- assumptions
function assumptionsList() {
  const lib = S.status.settings.assumption_library || [];
  const ticked = lib.filter((_, i) => S.assumed[i]);
  const custom = (S.assumeCustom || '').split('\n').map(t => t.trim()).filter(Boolean);
  return [...ticked, ...custom];
}
function assumptionsCard() {
  const lib = S.status.settings.assumption_library || [];
  const count = h('small', { class: 'hint', id: 'assume-count' });
  const upd = () => { const n = assumptionsList().length; count.textContent = n ? `${n} line${n > 1 ? 's' : ''} selected: the slide "4. Assumptions & Scope" is added after the mesh.` : 'Nothing selected: no assumptions slide.'; };
  const boxes = lib.map((t, i) => {
    const cb = h('input', { type: 'checkbox', checked: !!S.assumed[i], 'aria-label': t });
    cb.addEventListener('change', () => { S.assumed[i] = cb.checked; upd(); scheduleDerive(); scheduleSave(); });
    return h('label', {}, cb, h('span', {}, t));
  });
  const ta = h('textarea', { rows: 2, placeholder: 'Your own lines, one per line (e.g. "Design temperature 210 \u00b0C: yield stress taken at that temperature.")', 'aria-label': 'Own assumptions' });
  ta.value = S.assumeCustom || '';
  ta.addEventListener('input', () => { S.assumeCustom = ta.value; upd(); scheduleSave(); });
  upd();
  return card('6', 'Assumptions & scope (optional)', 'Tick only what is TRUE for this model. Nothing is ticked for you, because a wrong assumption is worse than none.',
    h('div', { class: 'assume' }, boxes), h('div', { style: 'margin-top:10px' }, ta), count);
}

// ----- load cases
function caseCard(c, k) {
  const bcBox = h('div'), noteBox = h('div'), xBox = h('div', { class: 'xlist', 'data-extras': c.n }), obsBox = h('div', { 'data-obsbox': c.n, class: 'in', style: 'padding:0;display:grid;gap:12px' });
  renderLines(c, 'bc', bcBox); renderLines(c, 'notes', noteBox); renderExtras(c, xBox);
  const wordFlags = [];
  const wording = h('details', { class: 'wording' },
    h('summary', {}, 'Wording of captions and observations (written automatically, edit if you like)'),
    h('div', { class: 'in' }, WORDING.slice(0, 2).map(([key, label]) => wordField(c, key, label, wordFlags, 'input')), obsBox,
      wordField(c, 'conclusion', WORDING[2][1], wordFlags, 'input'),
      h('div', {}, h('button', { class: 'link', type: 'button', onclick: () => resetWording(c, wordFlags) }, 'Reset the wording of this case to automatic'))));
  renderObs(c, obsBox, wordFlags);

  const nameEl = inp(c, 'name', { need: 'casename', casename: '1', label: `Case ${c.n} name`, onchange: () => {
    if (!c.dirty.short) { c.short_name = c.name; shortEl.value = c.name; } scheduleDerive(); renderChecks(); } });
  const shortEl = inp(c, 'short_name', { need: 'req', label: `Case ${c.n} short name`, onchange: () => { c.dirty.short = true; scheduleDerive(); renderChecks(); } });
  const defEl = inp(c, 'def_max', { need: 'num', label: `Case ${c.n} max deformation`, onchange: scheduleDerive });
  const strEl = inp(c, 'stress_max', { need: 'num', label: `Case ${c.n} max stress`, onchange: scheduleDerive });
  const lf = (label, el, extra) => h('label', { class: 'f ' + (extra || '') }, h('span', {}, label), el);
  const ev = (key, label, hint) => h('label', { class: 'f' }, h('span', {}, label, h('span', { class: 'opt' }, '  (optional)')), inp(c, key, { label: `Case ${c.n}: ${label}`, onchange: scheduleDerive }), hint ? h('small', {}, hint) : null);

  return h('section', { class: 'card', 'data-casecard': c.n },
    h('div', { class: 'case-head' }, h('h2', {}, h('span', { class: 'num' }, String(7 + k)), `Load case ${c.n}`), h('span', { class: 'badge', 'data-badge': c.n, hidden: true })),
    h('div', { class: 'casepics' },
      h('figure', {}, thumb(c.bc_id, 'thumb big', 300), h('figcaption', {}, 'Setup')),
      h('figure', {}, thumb(c.def_id, 'thumb big', 300), h('figcaption', {}, 'Deformation')),
      h('figure', {}, thumb(c.stress_id, 'thumb big', 300), h('figcaption', {}, 'Stress'))),
    h('div', { class: 'grid' },
      lf('Case name (slide title)', nameEl, 'span2'), lf('Subtitle', inp(c, 'subtitle', { label: `Case ${c.n} subtitle` }), 'span2'),
      lf('Short name in the final summary table', shortEl, 'span2')),
    h('h3', { class: 'mini' }, 'Boundary conditions ', h('small', {}, '(the letters A, B, C ... follow the legend in the setup picture)')),
    h('div', { class: 'cols2' }, h('div', {}, bcBox), evid(c.bc_id, 'bc', 'What I read in the setup legend (compare)', 760)),
    h('h3', { class: 'mini' }, 'Notes ', h('small', {}, '(optional, shown under the list)')),
    noteBox,
    h('h3', { class: 'mini' }, 'Results ', h('small', {}, '(read from the colour legends, each TWICE. Compare with the crop and correct if wrong)')),
    h('div', { class: 'resrow' },
      h('div', { class: 'resbox' }, h('div', {}, lf('Maximum total deformation', h('div', { class: 'unit' }, defEl, h('em', {}, 'mm')))), evid(c.def_id, 'legend', 'the legend I read', 420)),
      h('div', { class: 'resbox' }, h('div', {}, lf('Maximum von-Mises stress', h('div', { class: 'unit' }, strEl, h('em', {}, 'MPa'))), h('div', { class: 'verdict-line', 'data-verdict': c.n })),
        evid(c.stress_id, 'legend', 'the legend I read', 420))),
    h('div', { 'data-basisbox': c.n }),
    h('h3', { class: 'mini' }, 'Additional views ', h('small', {}, '(a section or a zoom is drawn on the SAME slide as the picture it belongs to, with the region marked; other result plots may get their own slide)')),
    xBox,
    h('details', { class: 'wording' }, h('summary', {}, 'Engineering evidence (optional): where is the maximum, reaction-force check'),
      h('div', { class: 'in' }, h('div', { class: 'grid' },
        h('div', { class: 'span2' }, ev('loc_stress', 'Where does the maximum stress occur?', 'e.g. "the weld toe at the hub" - becomes a sentence in the observations')),
        h('div', { class: 'span2' }, ev('loc_def', 'Where does the maximum deformation occur?', 'e.g. "the free end of the shaft"')),
        h('div', { class: 'span2' }, ev('react_applied', 'Total applied load (N)', 'from your load calculation')),
        h('div', { class: 'span2' }, ev('react_sum', 'Sum of the reaction forces (N)', 'from ANSYS "Force Reaction" - the app compares the two (should agree within 2 %)'))))),
    wording);
}

function wordField(c, key, label, flags, kind) {
  const el = kind === 'area' ? h('textarea', { rows: 2 }) : h('input', { type: 'text' });
  el.value = c.texts[key] || ''; el.dataset.case = c.n; el.dataset.key = key;
  const flag = h('em', { class: 'edited', hidden: !c.dirty[key] }, 'edited by you');
  flags.push(flag);
  el.addEventListener('input', () => { c.texts[key] = el.value; c.dirty[key] = true; flag.hidden = false; scheduleSave(); });
  return h('label', { class: 'f' }, h('span', {}, label, flag), el);
}

function renderObs(c, box, flags) {
  const n = Math.max(3, c.obs.length);
  const flag = h('em', { class: 'edited', hidden: !c.dirty.obs }, 'edited by you');
  if (flags) flags.push(flag);
  box.replaceChildren(...Array.from({ length: n }, (_, i) => {
    const el = h('textarea', { rows: 2, 'aria-label': `Observation ${i + 1}` });
    el.value = c.obs[i] || '';
    el.addEventListener('input', () => {
      c.obs[i] = el.value; c.dirty.obs = true; flag.hidden = false; box.dataset.touched = '1'; scheduleSave();
    });
    return h('label', { class: 'f' }, h('span', {}, `Observation ${i + 1}`, i === 0 ? flag : null), el);
  }));
}

function resetWording(c, flags) {
  c.dirty = {}; c.texts = {}; c.obs = [];
  $$(`[data-case="${c.n}"][data-key]`).forEach(el => { el.value = ''; });
  flags.forEach(f => { f.hidden = true; });
  scheduleDerive();
}

function renderLines(c, kind, box) {
  const items = kind === 'bc' ? c.bc_items : c.notes;
  const rows = items.map((t, k) => {
    const el = h('input', { type: 'text', value: t, 'data-need': kind === 'bc' ? 'bracket' : 'bracketOpt', list: kind === 'bc' ? 'dl-bc' : null,
      'data-label': kind === 'bc' ? `Case ${c.n}, boundary condition ${String.fromCharCode(65 + k)}` : `Case ${c.n}, note ${k + 1}`,
      placeholder: kind === 'bc' ? 'e.g. Fixed support applied on the left shaft.' : 'e.g. Welds are not modelled.' });
    el.addEventListener('input', () => { items[k] = el.value; if (kind === 'bc') scheduleDerive(); updateNeeds(); });
    const lg = kind === 'bc' && c.bc_legend[k] ? c.bc_legend[k].replace(/mm\/s[?*"']?/, 'mm/s\u00b2').replace(/N-mm/g, 'N\u00b7mm') : '';
    return h('div', { class: 'line' }, h('span', { class: kind === 'bc' ? 'letter' : 'letter n' }, kind === 'bc' ? String.fromCharCode(65 + k) : String(k + 1)),
      h('div', { class: 'grow' }, el, lg ? h('small', { class: 'hint' }, `Picture legend: ${lg}`) : null),
      h('button', { class: 'icon', type: 'button', title: 'Remove this line', onclick: () => {
        items.splice(k, 1); if (kind === 'bc') c.bc_legend.splice(k, 1); renderLines(c, kind, box); updateNeeds(); scheduleDerive(); } }, '\u2715'));
  });
  const adds = [h('button', { class: 'link add', type: 'button', onclick: () => {
    items.push(''); if (kind === 'bc') c.bc_legend.push(''); renderLines(c, kind, box); updateNeeds();
    const ins = $$('input', box); if (ins.length) ins[ins.length - 1].focus(); } }, kind === 'bc' ? '+ Add a line' : '+ Add a note')];
  if (kind === 'notes') adds.push(h('button', { class: 'link add', type: 'button', style: 'margin-left:14px', onclick: () => {
    items.push('This load case is at the working temperature of [value - please confirm] \u00b0C.'); renderLines(c, kind, box); updateNeeds(); } }, '+ Working-temperature note'));
  box.replaceChildren(...rows, h('div', {}, adds));
}

// ----- additional views of a case
function renderExtras(c, box) {
  if (!c.extras.length) {
    box.replaceChildren(h('small', { class: 'hint' }, 'None. A second picture of the same kind (e.g. a section view of the stress) is added here automatically, or choose "Extra view" for a picture in the Pictures table.'));
    return;
  }
  box.replaceChildren(...c.extras.map(e => {
    const img = S.images.find(i => i.id === e.id) || {};
    const acts = [];
    if (['bc', 'deformation', 'stress'].includes(e.kind)) acts.push(h('button', { type: 'button', onclick: () => swapMain(c, e) }, 'Use as main plot'));
    acts.push(h('button', { type: 'button', onclick: () => dropPicture(e) }, 'Remove'));
    const meta = [];
    if (e.max !== null && e.max !== undefined) meta.push(`Max ${e.max} ${e.unit || ''}`);
    if (e.auto) meta.push('chosen automatically: the main plot has the higher Max');
    const where = h('select', { 'aria-label': `Where ${img.name} is placed`, class: 'sel' },
      h('option', { value: 'same', selected: !!e.attach }, `On the same slide as ${e.parent_name || 'its picture'}`),
      h('option', { value: 'own', selected: !e.attach }, 'On a slide of its own'));
    where.addEventListener('change', () => { e.attach = where.value === 'same'; scheduleSave(); scheduleBuild(); renderPreview(); });
    const side = h('select', { 'aria-label': `Which side ${img.name} goes on`, class: 'sel' },
      h('option', { value: 'auto', selected: (e.pos || 'auto') === 'auto' }, 'next to the region it shows'),
      h('option', { value: 'right', selected: e.pos === 'right' }, 'on the right'),
      h('option', { value: 'left', selected: e.pos === 'left' }, 'on the left'),
      h('option', { value: 'nomark', selected: e.pos === 'nomark' }, 'on the right, no region mark'));
    side.addEventListener('change', () => { e.pos = side.value; scheduleSave(); scheduleBuild(); renderPreview(); });
    const m = e.match, pend = !!(S.matchPending && S.matchPending.has(e.id));
    const found = pend ? 'Zoomed view: I am still looking for its region inside the parent picture - a second or two.'
      : m ? `I found this view inside "${e.parent_name}" - the marked region is ${m.side === 'left' ? 'left' : 'right'}, ` +
      `${m.row === 'top' ? 'upper' : 'lower'} half (${Math.round(m.score * 100)} % alike).`
      : (e.view_kind === 'detail' ? `Zoomed view: I could not find that region inside "${e.parent_name}", so no mark is drawn.`
                                  : (e.parent_why ? `Attached to ${e.parent_name} - ${e.parent_why}.` : `Attached to ${e.parent_name}.`));
    return h('div', { class: 'xitem' }, thumb(e.id, 'thumb', 220),
      h('div', {}, h('span', { class: 'kindtag' }, KIND_LABEL[e.kind] || e.kind),
        e.view_kind ? h('span', { class: 'kindtag view' }, e.view_kind === 'section' ? 'section' : 'zoom') : null,
        h('span', { class: 'meta' }, img.name || ''),
        field('Heading (also the slide title)', e, 'heading', { need: 'bracket', label: `Case ${c.n}: heading of the additional view ${img.name || ''}`, onchange: scheduleDerive }),
        field('Caption', e, 'caption', { optional: true, label: 'Caption' }),
        h('div', { class: 'xwhere' }, h('span', {}, 'Placement: '), where, ' ', h('span', {}, ' '), side),
        h('div', { class: 'meta' + (m && !pend ? ' ok' : '') }, found),
        h('div', { class: 'meta' }, meta.join('  \u00b7  '))),
      h('div', { class: 'acts' }, acts));
  }));
}

function swapMain(c, e) {
  const ex = S.images.find(i => i.id === e.id); if (!ex) return;
  const base = ex.role.replace(/^x_/, '');
  const old = S.images.find(i => i.role === base && i.case === ex.case);
  if (old) old.role = 'x_' + base;
  ex.role = base; regroup();
}
function dropPicture(e) {
  const ex = S.images.find(i => i.id === e.id); if (!ex) return;
  ex.role = 'unused'; ex.case = null; regroup();
}

// ----- how to report an over-limit peak (the singularity guard)
function renderBasisBoxes() {
  for (const c of S.cases) {
    const box = $(`[data-basisbox="${c.n}"]`); if (!box) continue;
    const active = ['exceeds', 'beyond_yield', 'suspect'].includes(c.tier);
    const sig = `${c.tier}|${c.basis}|${c.needsBasis}`;
    if (box.dataset.sig === sig) continue;
    if (box.contains(document.activeElement) && document.activeElement.tagName === 'INPUT' && document.activeElement.type === 'text') { box.dataset.sig = sig; continue; }
    box.dataset.sig = sig;
    if (!active) { box.replaceChildren(); continue; }
    const suspect = c.tier === 'suspect';
    const sy = num(govRow() && govRow().yield), sx = num(c.stress_max);
    const radios = BASIS_OPTIONS.map(([v, title, sub]) => {
      const checked = c.basis === v || (!suspect && !c.basis && v === 'asis');
      const r = h('input', { type: 'radio', name: `basis-${c.n}`, value: v, checked });
      r.addEventListener('change', () => { c.basis = v; box.dataset.sig = ''; renderBasisBoxes(); scheduleDerive(); updateNeeds(); });
      return h('label', { class: 'opt-row' }, r, h('span', {}, title, h('small', {}, sub)));
    });
    const subs = [];
    if (c.basis === 'singularity' || c.basis === 'refine') {
      subs.push(field('Where is the peak? (location)', c, 'sing_loc', { need: 'req', label: `Case ${c.n}: location of the peak stress`, onchange: scheduleDerive }));
      if (c.basis === 'singularity') subs.push(field('Stress AWAY from the peak (MPa)', c, 'stress_away', { need: 'num', label: `Case ${c.n}: stress away from the peak`, onchange: scheduleDerive,
        hint: 'Read it in ANSYS with a probe or by moving the legend. The app never guesses it.' }));
    }
    const inner = [
      h('h4', {}, suspect ? 'Check this before you trust the verdict' : 'How is the peak stress reported?'),
      h('p', {}, suspect
        ? `The peak stress (${c.stress_max} MPa)${sy && sx ? ' is ' + (sx / sy).toFixed(0) + ' times the YIELD strength' : ' is far above yield'}. In a real steel structure this is almost always a stress singularity (a point support, a sharp corner, a contact edge) or a load / unit error. Choose how the report should treat it.`
        : 'The peak stress is above the allowable. By default the report says so, as in your template. Change this only if you know the peak is not representative.'),
      ...radios];
    if (subs.length) inner.push(h('div', { class: 'sub' }, ...subs));
    const wrap = h('div', { class: 'basis' + (suspect ? '' : ' soft'), 'data-need': suspect ? 'basis' : null, 'data-bad': c.needsBasis ? '1' : '',
                            'data-label': `Case ${c.n}: how to report the peak stress` }, ...inner);
    box.replaceChildren(suspect ? wrap : h('details', { class: 'wording', open: !!c.basis }, h('summary', {}, 'The peak stress is above the allowable: how is it reported? (default: as in the template)'), h('div', { class: 'in' }, wrap)));
  }
}

function renderVerdicts() {
  const allow = govAllow();
  S.cases.forEach(c => {
    $$(`[data-badge="${c.n}"]`).forEach(b => {
      if (c.exceeds === null || c.exceeds === undefined) { b.hidden = true; return; }
      b.hidden = false;
      const t = c.tier;
      b.className = 'badge ' + (t === 'pass' ? 'ok' : 'bad');
      b.textContent = t === 'pass' ? '\u2714 Within the allowable stress' : t === 'exceeds' ? '\u2716 Exceeds the allowable stress'
        : t === 'beyond_yield' ? '\u2716 Above yield strength' : '\u2716 Peak far above yield: check it';
    });
    $$(`[data-verdict="${c.n}"]`).forEach(v => {
      v.textContent = allow === null ? 'Enter the yield stress to see the pass / fail result.' : `Allowable stress: ${allow} MPa` + (c.util ? `  \u00b7  utilisation ${c.util < 10 ? c.util.toFixed(1) : c.util.toFixed(0)} %` : '');
    });
  });
}

// ───────────────────────────── regroup (user changed a picture's role) ─────────────────────────────
async function regroup() {
  busy('Updating the draft ...');
  try {
    const r = await post('/api/regroup', { sid: S.sid, assign: S.images.map(i => {
      const e = extraOf(i.id);
      const g = S.geometry.find(x => x.id === i.id) || S.meshes.find(x => x.id === i.id);
      return { id: i.id, role: choiceOf(i), case: i.case, view_kind: i.view_kind || '', rkind: i.rkind || '',
               parent: e ? e.parent : (g ? g.parent : (i.parent || null)),
               attach: e ? e.attach : (g ? g.attach : (i.attach || null)), pos: e ? (e.pos || 'auto') : (i.pos || 'auto') };
    }) });
    S.images = r.images; S.warnings = r.warnings;
    const keep = (oldList, id) => oldList.find(o => o.id === id);
    const og = S.geometry, om = S.meshes;
    S.geometry = r.geometry.map(g => { const o = keep(og, g.id); return { id: g.id, heading: o ? o.heading : g.heading, caption: o ? o.caption : '',
      parent: o && o.parent !== undefined && o.parent !== null ? o.parent : g.parent, attach: o && o.attach !== undefined ? o.attach : !!g.attach }; });
    S.meshes = r.meshes.map(g => { const o = keep(om, g.id); return { id: g.id, heading: o ? o.heading : g.heading, caption: o ? o.caption : '',
      parent: o && o.parent !== undefined && o.parent !== null ? o.parent : g.parent, attach: o && o.attach !== undefined ? o.attach : !!g.attach }; });
    const old = new Map(S.cases.map(c => [c.n, c]));
    S.cases = r.cases.map(dc => {
      const o = old.get(dc.n);
      if (!o) return newCase(dc);
      return mergeCase(o, dc);
    });
    renderReview(); scheduleDerive();
  } catch (e) { showAlert(e.message); }
  finally { busy(false); }
}

// ───────────────────────────── live wording + checks ─────────────────────────────
let deriveTimer = null;
function scheduleDerive() { clearTimeout(deriveTimer); deriveTimer = setTimeout(() => { runDerive().catch(() => {}); }, 250); }
async function flushDerive() { clearTimeout(deriveTimer); await runDerive().catch(() => {}); }

function deriveBody() {
  const row = govRow() || {};
  return { sid: S.sid, allowable: govAllow(), yield: num(row.yield), fos: S.fos, short: S.short, show_utilisation: S.showUtil,
    def_limit: num(S.defLimit), material: { E: row.E, nu: row.nu, rho: row.rho }, mesh_stats: S.meshStats, assumptions: assumptionsList(),
    layouts: S.layouts, geometry: S.geometry.map(g => ({ id: g.id, parent: g.parent, attach: g.attach })),
    meshes: S.meshes.map(g => ({ id: g.id, parent: g.parent, attach: g.attach })),
    cases: S.cases.map(c => ({ n: c.n, def_max: num(c.def_max), stress_max: num(c.stress_max), red_px: c.stress_red_px, legend: c.stress_legend || [],
      stress_unit: c.stress_unit, def_unit: c.def_unit, basis: c.basis, sing_loc: c.sing_loc, stress_away: c.stress_away, loc_stress: c.loc_stress,
      loc_def: c.loc_def, react_applied: c.react_applied, react_sum: c.react_sum, gravity: c.gravity, legend_px: c.legend_px, layout: c.layout,
      bc_layout: c.bc_layout, bc_id: c.bc_id, def_id: c.def_id, stress_id: c.stress_id, bc_items: c.bc_items, notes: c.notes,
      extras: c.extras.map(e => ({ id: e.id, kind: e.kind, heading: e.heading, max: e.max, attach: e.attach, pos: e.pos || 'auto',
                                   parent: e.parent, match: e.match || null })) })) };
}

async function runDerive() {
  if (!S.sid) return;
  const r = await post('/api/derive', deriveBody());
  S.checks = r.checks; S.suggest = r.suggest; S.layoutPlan = r.layout;
  for (const c of S.cases) {
    const d = r.cases[String(c.n)]; if (!d) continue;
    c.exceeds = d.exceeds; c.tier = d.tier; c.needsBasis = d.needs_basis; c.util = d.util;
    const t = d.texts, auto = { deformation_caption: t.deformation_caption, stress_caption: t.stress_caption, conclusion: t.conclusion };
    for (const [key, val] of Object.entries(auto)) {
      if (c.dirty[key]) continue;
      c.texts[key] = val;
      $$(`[data-case="${c.n}"][data-key="${key}"]`).forEach(el => { el.value = val; });
    }
    if (!c.dirty.obs) {
      const same = JSON.stringify(c.obs) === JSON.stringify(t.observations);
      c.obs = t.observations.slice();
      const box = $(`[data-obsbox="${c.n}"]`);
      if (box && !same && !box.contains(document.activeElement)) renderObs(c, box, null);
    }
  }
  renderChecks(); renderVerdicts(); renderHint(); renderLayoutCard(); renderBasisBoxes(); updateNeeds();
}

// ───────────────────────────── "needs your input" tracking ─────────────────────────────
function updateNeeds() {
  const names = S.cases.map(c => (c.name || '').trim().toLowerCase());
  $$('#s-review [data-need]').forEach(el => {
    const v = (el.value || '').trim(); let bad = false;
    switch (el.dataset.need) {
      case 'req': bad = v === ''; break;
      case 'bracket': bad = v === '' || /\[[^\]]*\]/.test(v); break;
      case 'bracketOpt': bad = /\[[^\]]*\]/.test(v); break;
      case 'num': bad = !isNum(v); break;
      case 'optnum': bad = v !== '' && !isNum(v); break;
      case 'title': bad = v === '' || /\bof$/i.test(v); break;
      case 'role': bad = v === 'unknown'; break;
      case 'casename': bad = v === '' || names.filter(n => n === v.toLowerCase()).length > 1; break;
      case 'basis': bad = el.dataset.bad === '1'; break;
    }
    el.classList.toggle('need', bad);
  });
  const n = $$('#s-review .need').length, msg = $('#bar-msg');
  if (n === 0) { msg.className = 'bar-msg ok'; msg.textContent = '\u2713 Nothing left to fill in.'; }
  else {
    msg.className = 'bar-msg todo';
    msg.replaceChildren(`${n} ${n === 1 ? 'item needs' : 'items need'} your input (yellow). You can download anyway.`,
      h('button', { class: 'link', type: 'button', onclick: gotoNeed }, 'Show me'));
  }
  renderTodo();
  scheduleSave();
}
function gotoNeed() {
  const el = $('#s-review .need'); if (!el) return;
  el.scrollIntoView({ block: 'center' }); if (el.focus) el.focus();
}

// ───────────────────────────── generate + done ─────────────────────────────
function textsPayload(c) {
  const t = {}, x = c.texts;
  if (c.dirty.deformation_caption) t.deformation_caption = x.deformation_caption || '';
  if (c.dirty.stress_caption) t.stress_caption = x.stress_caption || '';
  if (c.dirty.obs) t.observations = c.obs.filter(Boolean);
  if (c.dirty.conclusion) t.conclusion = x.conclusion || '';
  return t;
}

function buildPayload() {
  const row = govRow() || {};
  return {
    sid: S.sid, cover: S.cover, short: S.short, fos: S.fos, yield: num(row.yield), allowable: govAllow(), show_utilisation: S.showUtil, def_limit: num(S.defLimit),
    material: { rows: S.mat.rows.map(r => [r.material, r.item, r.E, r.nu, r.rho, r.yield, String(allowOf(r) ?? '')]), units: S.units },
    geometry: S.geometry.map(g => ({ id: g.id, heading: g.heading, caption: g.caption, parent: g.parent, attach: g.attach })),
    meshes: S.meshes.map(g => ({ id: g.id, heading: g.heading, caption: g.caption, parent: g.parent, attach: g.attach })), mesh_stats: S.meshStats,
    assumptions: assumptionsList(), layouts: S.layouts,
    cases: S.cases.map(c => ({ n: c.n, name: c.name, subtitle: c.subtitle, short_name: c.short_name, bc_id: c.bc_id, def_id: c.def_id,
      stress_id: c.stress_id, bc_items: c.bc_items, notes: c.notes, def_max: c.def_max, stress_max: c.stress_max, basis: c.basis, sing_loc: c.sing_loc,
      stress_away: c.stress_away, loc_stress: c.loc_stress, loc_def: c.loc_def, react_applied: c.react_applied, react_sum: c.react_sum,
      layout: c.layout, bc_layout: c.bc_layout, legend_px: c.legend_px,
      extras: c.extras.map(e => ({ id: e.id, kind: e.kind, heading: e.heading, caption: e.caption, max: e.max,
                                   attach: e.attach, pos: e.pos || 'auto', parent: e.parent, match: e.match || null })),
      texts: textsPayload(c) })),
    missing: S.missing,
  };
}

// ───────────────────────────── the live preview of the real report ─────────────────────────────
const slideUrl = (n, w) => `/api/slide/${S.sid}/${S.build.key}/${n}?w=${w}`;
function slideWidth() {
  const f = $('#pv-frame'), css = f && f.clientWidth ? f.clientWidth : 900;
  return Math.min(2000, Math.max(640, Math.ceil(css * (window.devicePixelRatio || 1) / 160) * 160));
}

function previewCard() {
  const nav = (cls, label, txt, delta) => h('button', { class: 'pv-nav ' + cls, type: 'button', id: 'pv-' + cls, 'aria-label': label,
    onclick: e => { e.stopPropagation(); gotoSlide(S.build.cur + delta); } }, txt);
  const frame = h('div', { class: 'pv-frame loading', id: 'pv-frame', title: 'Click to see the slide full screen', onclick: openSlideBox },
    h('img', { id: 'pv-img', alt: 'Preview of the slide' }), h('div', { class: 'pv-wait', id: 'pv-wait' }));
  const missing = h('select', { id: 'pv-missing', 'aria-label': 'What to do when a picture is missing' },
    h('option', { value: 'skip', selected: S.missing === 'skip' }, 'Leave that slide out'),
    h('option', { value: 'frame', selected: S.missing === 'frame' }, 'Keep an empty frame to fill in PowerPoint'));
  missing.addEventListener('change', () => { S.missing = missing.value; saveSaved(); scheduleBuild(150); scheduleSave(); });
  return h('section', { class: 'card pv', id: 'pv-card' },
    h('div', { class: 'pv-head' }, h('h2', {}, 'Report preview'),
      h('span', { class: 'pv-status wait', id: 'pv-status' }, h('i'), h('span', { id: 'pv-status-text' }, 'Preparing ...')),
      h('button', { class: 'primary', type: 'button', id: 'pv-refresh', onclick: () => buildNow().catch(() => {}) }, ico('refresh'), h('span', { id: 'pv-refresh-lbl' }, 'Build the preview')),
      h('button', { class: 'secondary', type: 'button', id: 'pv-full', onclick: openSlideBox }, ico('expand'), 'Full screen')),
    h('p', { class: 'sub' }, 'This is your real report, drawn from the PowerPoint file that is built from what you typed above. ',
      h('b', {}, 'It never rebuilds by itself'), ' - press the button here (or "Preview" in the bar at the bottom) when you want to see it.'),
    h('div', { class: 'pv-stage' }, frame, nav('prev', 'Previous slide', '\u2039', -1), nav('next', 'Next slide', '\u203A', 1)),
    h('div', { class: 'pv-bar' }, h('span', { class: 'pv-count', id: 'pv-count' }), h('span', { class: 'pv-title', id: 'pv-title' })),
    h('div', { class: 'pv-strip', id: 'pv-strip' }),
    h('div', { class: 'pv-notes', id: 'pv-notes', hidden: true }),
    h('div', { class: 'pv-todo', id: 'pv-todo', hidden: true }),
    h('div', { class: 'pv-actions' },
      h('label', { class: 'miss' }, h('span', {}, 'If a picture is missing:'), missing),
      h('span', { class: 'grow' }),
      h('button', { class: 'secondary', type: 'button', 'data-dl': 'pdf', onclick: () => downloadReport('pdf') }, ico('download'), 'Download PDF'),
      h('button', { class: 'primary', type: 'button', 'data-dl': 'pptx', onclick: () => downloadReport('pptx') }, ico('download'), 'Download PowerPoint')));
}

function renderPreview() {
  const card = $('#pv-card'); if (!card) return;
  const B = S.build, n = B.slides.length, has = !!(B.key && n);
  const stale = has && (B.status === 'stale' || B.status === 'building');
  // NOTE: never 'busy' here - .busy is the FULL-SCREEN waiting overlay; on the chip it would cover the whole page
  const MAP = { idle: ['wait', 'Not built yet - press "Build the preview"'], building: ['wait', has ? 'Updating the preview ...' : 'Building the preview ...'],
                stale: ['wait', 'Out of date - press "Update preview"'],
                ok: ['ok', 'Preview is up to date'], error: ['bad', 'The preview could not be made'] };
  const [cls, text] = MAP[B.status] || MAP.idle;
  $('#pv-status').className = 'pv-status ' + cls; $('#pv-status-text').textContent = text;
  const frame = $('#pv-frame'), img = $('#pv-img'), wait = $('#pv-wait');
  frame.classList.toggle('stale', stale);
  wait.hidden = has && !stale;
  const lbl = $('#pv-refresh-lbl');
  if (lbl) lbl.textContent = !has ? 'Build the preview' : (stale ? 'Update preview' : 'Refresh');
  const pvb = $('#btn-preview');
  if (pvb) { pvb.classList.toggle('attn', stale || !has); const l = pvb.querySelector('.lbl'); if (l) l.textContent = !has ? 'Preview' : (stale ? 'Update preview' : 'Preview'); }
  if (!has) {
    frame.classList.remove('loading');
    if (B.status === 'error') wait.replaceChildren(h('b', {}, 'The report could not be built.'), h('span', {}, B.error),
      h('button', { class: 'secondary', type: 'button', onclick: () => buildNow().catch(() => {}) }, 'Try again'));
    else if (B.status === 'building') wait.replaceChildren(h('div', { class: 'spin' }), h('span', {}, 'Building the preview ...'));
    else wait.replaceChildren(ico('eye'), h('b', {}, 'The preview stays empty until you ask for it.'),
      h('span', {}, 'Work through the cards above; when you want to see the report, build it here. ' +
                    'Downloading the PowerPoint or the PDF builds it too, with exactly what is on the screen.'),
      h('button', { class: 'primary big', type: 'button', onclick: () => buildNow().catch(() => {}) }, ico('refresh'), 'Build the preview'));
  } else {
    if (stale) wait.replaceChildren(h('b', {}, 'What you see is the PREVIOUS build.'),
      h('span', {}, 'Something above changed since. Press "Update preview" (or download) to see the report as it is now.'),
      h('button', { class: 'primary', type: 'button', onclick: () => buildNow().catch(() => {}) }, ico('refresh'), 'Update preview'));
    const w = slideWidth(), url = slideUrl(B.cur, w);
    if (img.dataset.url !== url) {
      frame.classList.add('loading'); img.dataset.url = url;
      img.onload = img.onerror = () => frame.classList.remove('loading');
      img.src = url;
    }
    img.alt = `Slide ${B.cur + 1}: ${B.slides[B.cur].title}`;
    if (B.cur + 1 < n) { const pre = new Image(); pre.src = slideUrl(B.cur + 1, w); }
  }
  $('#pv-prev').disabled = !has || B.cur <= 0; $('#pv-next').disabled = !has || B.cur >= n - 1;
  $('#pv-count').textContent = has ? `Slide ${B.cur + 1} of ${n}` : '';
  $('#pv-title').textContent = has ? B.slides[B.cur].title : '';
  const strip = $('#pv-strip'), sig = `${B.key}|${n}`;
  if (has && strip.dataset.sig !== sig) {
    strip.dataset.sig = sig;
    strip.replaceChildren(...B.slides.map((sl, i) => h('button', { type: 'button', 'data-i': i, title: sl.title, onclick: () => gotoSlide(i) },
      h('img', { alt: `Slide ${i + 1}`, src: slideUrl(i, 240) }), h('span', {}, String(i + 1)))));
  }
  $$('#pv-strip button').forEach(btn => {
    const on = +btn.dataset.i === B.cur; btn.classList.toggle('on', on);
    if (on) { const r = btn.offsetLeft - strip.clientWidth / 2 + btn.clientWidth / 2; strip.scrollTo({ left: Math.max(0, r) }); }
  });
  const nb = $('#pv-notes'), items = [...B.notes];
  if (B.status === 'error' && has) {
    nb.hidden = false; nb.style.background = 'var(--bad-bg)'; nb.style.color = '#7a1f14';
    nb.replaceChildren(h('b', {}, 'The last change could not be put into the report: '), B.error, ' ',
      h('button', { class: 'link', type: 'button', onclick: () => buildNow().catch(() => {}) }, 'Try again'));
  } else {
    nb.style.background = ''; nb.style.color = ''; nb.hidden = !items.length;
    nb.replaceChildren(h('b', {}, 'Left out or simplified because something is missing:'), h('ul', {}, items.map(t => h('li', {}, t))));
  }
  renderTodo();
  if (!$('#slidebox').hidden) renderSlideBox();
}

function renderTodo() {
  const box = $('#pv-todo'); if (!box) return;
  const left = $$('#s-review .need').length;
  box.hidden = left === 0;
  if (left) box.replaceChildren(`${left} ${left === 1 ? 'item still needs' : 'items still need'} your input (yellow boxes below). You can download now: anything not filled in shows as [text in brackets] in the report. `,
    h('button', { class: 'link', type: 'button', onclick: gotoNeed }, 'Show me the first one'));
}

function gotoSlide(i) {
  const B = S.build; if (!B.slides.length) return;
  B.cur = Math.max(0, Math.min(B.slides.length - 1, i)); renderPreview();
}

function openSlideBox() { if (!S.build.key) return; $('#slidebox').hidden = false; renderSlideBox(); }
function closeSlideBox() { $('#slidebox').hidden = true; }
function renderSlideBox() {
  const B = S.build, box = $('#slidebox'); if (!B.key || !B.slides.length) return;
  const w = Math.min(2400, Math.max(1400, Math.ceil(window.innerWidth * (window.devicePixelRatio || 1) / 200) * 200)), img = $('img', box), url = slideUrl(B.cur, w);
  if (img.dataset.url !== url) { img.dataset.url = url; img.src = url; }
  $('figcaption', box).textContent = `Slide ${B.cur + 1} of ${B.slides.length}: ${B.slides[B.cur].title}`;
  $('.sb-nav.prev', box).disabled = B.cur <= 0; $('.sb-nav.next', box).disabled = B.cur >= B.slides.length - 1;
}
function bindSlideBox() {
  const box = $('#slidebox');
  $('.sb-nav.prev', box).addEventListener('click', () => gotoSlide(S.build.cur - 1));
  $('.sb-nav.next', box).addEventListener('click', () => gotoSlide(S.build.cur + 1));
  $('.sb-close', box).addEventListener('click', closeSlideBox);
  box.addEventListener('click', e => { if (e.target === box) closeSlideBox(); });
  document.addEventListener('keydown', e => {
    const open = !box.hidden, tag = (document.activeElement && document.activeElement.tagName) || '';
    if (e.key === 'Escape') closeSlideBox();
    else if ((e.key === 'ArrowLeft' || e.key === 'ArrowRight') && (open || (!/INPUT|TEXTAREA|SELECT/.test(tag) && $('#pv-card') && !$('#s-review').hidden))) {
      if (!open) { const r = $('#pv-card').getBoundingClientRect(); if (r.bottom < 0 || r.top > innerHeight) return; }
      gotoSlide(S.build.cur + (e.key === 'ArrowRight' ? 1 : -1)); e.preventDefault();
    }
  });
}

// ───────────────────────────── building the report in the background ─────────────────────────────
let buildTimer = null;
function scheduleBuild(_delay = 1400) {
  // the preview updates ONLY when the engineer says so (presses Update preview / Download).  Here we just remember that
  // what is on the screen no longer matches the report (council items U1 + U3).
  if (!S.sid || $('#s-review').hidden) return;
  const B = S.build;
  if (!B.key || B.status === 'idle' || B.status === 'building') return;
  if (JSON.stringify(buildPayload()) === B.sig && B.status === 'ok') return;                                  // nothing changed
  if (B.status === 'ok') { B.status = 'stale'; renderPreview(); }
  if (B.downloaded) { B.downloaded = false; setStep('review'); }
}

// One build at a time; if something changes while it runs, one more build follows (the latest state always wins).
async function buildNow() {
  clearTimeout(buildTimer);
  const B = S.build;
  if (!S.sid) return null;
  if (B.running) { B.again = true; return B.promise; }
  B.running = true;
  B.promise = (async () => {
    try {
      do {
        B.again = false;
        const body = buildPayload(), sig = JSON.stringify(body);
        if (sig === B.sig && B.status === 'ok' && B.key) continue;
        B.status = 'building'; renderPreview();
        try {
          const r = await post('/api/build', body);
          Object.assign(B, { sig, key: r.key, slides: r.slides, notes: r.notes || [], name: r.name || B.name, status: 'ok', error: '' });
          if (B.cur >= B.slides.length) B.cur = Math.max(0, B.slides.length - 1);
        } catch (e) { B.status = 'error'; B.error = e.message; B.again = false; }
        renderPreview();
      } while (B.again);
      return B;
    } finally { B.running = false; }
  })();
  return B.promise;
}

// ───────────────────────────── download: a normal browser download into the Downloads folder ─────────────────────────────
function setDlBusy(on) { S.dlBusy = on; $$('[data-dl]').forEach(b => { b.disabled = on; b.classList.toggle('working', on); }); }

function startDownload(url, file) {
  const a = document.createElement('a');
  a.href = url; a.download = file || ''; a.rel = 'noopener'; a.style.display = 'none';
  document.body.append(a); a.click(); setTimeout(() => a.remove(), 3000);
}

async function checkFile(url) {
  let r;
  try { r = await fetch(url, { method: 'HEAD', cache: 'no-store' }); }
  catch (e) { throw new Error('Cannot reach the program. Is the black program window still open?'); }
  if (!r.ok) {
    S.build.sig = '';                                             // forces a fresh build the next time
    throw new Error('The report file is not on the program any more (was it restarted?). Press the download button again - it will be built again.');
  }
}

const inFrame = (() => { try { return window.self !== window.top; } catch (e) { return true; } })();

async function openDownloads() {
  try {
    const r = await post('/api/open_downloads', {});
    if (!r.ok) toast(r.message || 'Could not open it. Open File Explorer > Downloads yourself.', 'info', 9000);
  } catch (e) { toast('Could not open it. Open File Explorer > Downloads yourself.', 'info', 9000); }
}

function downloadToast(url, file) {
  const st = S.status || {};
  const how = [];
  if (st.can_open_folder) how.push(h('button', { class: 'link', type: 'button', id: 'btn-open-dl', onclick: openDownloads }, 'Open the Downloads folder'), ' \u00b7 ');
  else how.push('Windows: open File Explorer > Downloads, or press Ctrl+J in the browser. ');
  how.push(h('a', { href: url, download: file }, 'Nothing happened? Click here.'));
  const kids = [h('b', {}, 'Download started'), h('br'), `${file} is being saved in your Downloads folder.`, h('small', {}, how)];
  if (inFrame) {                                                  // e.g. the preview window of a website: it may block downloads
    kids.push(h('small', { class: 'frame-note' }, 'This page is shown inside another window. If that window blocks downloads, no file arrives - ',
      'a copy of the report is also kept in the program\'s folder: ', h('b', {}, `${st.outputs || 'outputs'}`), '.'));
  }
  $$('#toasts .toast.dl').forEach(t => t.remove());               // one download message at a time
  toast(h('div', {}, kids), 'ok dl', inFrame ? 30000 : 16000);
}

async function downloadReport(kind) {
  if (!S.sid || S.dlBusy) return;
  setDlBusy(true);
  try {
    clearTimeout(buildTimer);
    await flushDerive();
    const B = await buildNow();                      // makes sure the file matches what is on the screen right now
    if (!B || B.status !== 'ok' || !B.key) throw new Error(S.build.error || 'The report could not be built.');
    if (kind === 'pdf') {
      busy('Making the PDF ...');
      let r; try { r = await post('/api/pdf', { sid: S.sid, key: B.key }); } finally { busy(false); }
      B.pdfMethod = r.method;
      if (r.method === 'images' && r.message) toast(r.message, 'info', 16000);
    }
    const url = `/download/${S.sid}/${B.key}/${kind}`, file = `${B.name}.${kind}`;
    await checkFile(url);                                         // the file really is there (a clear message instead of the browser's "Failed - No file")
    startDownload(url, file);
    saveSaved();
    pushHist({ client: S.cover.client, short: S.short, bc: S.cases.flatMap(c => c.bc_items), prepared_by: S.cover.prepared_by, checked_by: S.cover.checked_by });
    B.downloaded = true; setStep('done'); hideAlert();
    $$('#toasts .toast.dl').forEach(t => t.remove());                 // one download message at a time
    downloadToast(url, file);
  } catch (e) { busy(false); showAlert(e.message); }
  finally { setDlBusy(false); }
}

boot();
