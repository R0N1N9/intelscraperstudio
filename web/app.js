// intelscraperstudio UI: talks to the local engine at /api.
const $ = (s, el = document) => el.querySelector(s);
const $$ = (s, el = document) => [...el.querySelectorAll(s)];
const esc = (s) => String(s ?? '').replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));

const VIBES = { cinematic: 'Cinematic', energetic: 'Energetic', chill: 'Chill', dreamy: 'Dreamy', nostalgic: 'Nostalgic', adventure: 'Adventure' };
const ICON = {
  card: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7"><path d="M6 2h9l4 4v16H6z"/><path d="M9 6v3M12 6v3M15 6v3"/></svg>',
  drone: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7"><circle cx="5" cy="5" r="2.5"/><circle cx="19" cy="5" r="2.5"/><circle cx="5" cy="19" r="2.5"/><circle cx="19" cy="19" r="2.5"/><rect x="9" y="9" width="6" height="6" rx="1"/><path d="M7 7l2 2M17 7l-2 2M7 17l2-2M17 17l-2-2"/></svg>',
  folder: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7"><path d="M3 7a2 2 0 0 1 2-2h4l2 2h8a2 2 0 0 1 2 2v8a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z"/></svg>',
  music: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7"><path d="M9 18V5l11-2v13"/><circle cx="6" cy="18" r="3"/><circle cx="17" cy="16" r="3"/></svg>',
  spark: '<svg viewBox="0 0 24 24" fill="currentColor"><path d="M12 2l2.2 6.6L21 11l-6.8 2.4L12 20l-2.2-6.6L3 11l6.8-2.4z"/></svg>',
};

const state = { status: null, shoots: [], tracks: [], jobs: [], sources: [], shootId: null, clips: [], sel: 0, filter: 'all', media: 'all', vibe: 'all', playing: null };

// ------------------------------------------------------------ helpers
async function api(path, opts = {}) {
  const res = await fetch('/api' + path, { headers: { 'Content-Type': 'application/json' }, ...opts, body: opts.body ? JSON.stringify(opts.body) : undefined });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(data.detail || `Request failed (${res.status})`);
  return data;
}
function toast(msg) { const t = $('#toast'); t.textContent = msg; t.classList.add('show'); clearTimeout(toast._t); toast._t = setTimeout(() => t.classList.remove('show'), 2800); }
const fmtBytes = (b) => { if (!b) return '0 GB'; const u = ['B', 'KB', 'MB', 'GB', 'TB']; let i = 0; while (b >= 1024 && i < 4) { b /= 1024; i++; } return `${b.toFixed(b < 10 && i > 2 ? 1 : 0)} ${u[i]}`; };
const fmtDur = (s) => { if (s == null) return '–'; s = Math.round(s); return `${Math.floor(s / 60)}:${String(s % 60).padStart(2, '0')}`; };
const fmtDate = (iso, opts = { month: 'short', day: 'numeric', year: 'numeric' }) => iso ? new Date(iso).toLocaleDateString(undefined, opts) : '';
const fmtTime = (iso) => iso ? new Date(iso).toLocaleString(undefined, { month: 'short', day: 'numeric', hour: 'numeric', minute: '2-digit' }) : '–';
const coord = (lat, lon) => lat == null ? '' : `${Math.abs(lat).toFixed(3)}°${lat >= 0 ? 'N' : 'S'} ${Math.abs(lon).toFixed(3)}°${lon >= 0 ? 'E' : 'W'}`;
const scoreCls = (n) => n >= 70 ? 'hi' : n > 42 ? 'mid' : 'lo';
const camIcon = (c) => /dji|drone|mavic/i.test(c || '') ? ICON.drone : ICON.card;
const pct = (v) => `${Math.max(0, Math.min(100, v))}%`;
const plural = (n, one, many = one + 's') => `${n} ${n === 1 ? one : many}`;

// ------------------------------------------------------------ router
const VIEWS = ['studio', 'review', 'music', 'settings'];
function route() {
  const [v, arg] = (location.hash.slice(1) || 'studio').split('/');
  if (v === 'soon') return;
  const view = VIEWS.includes(v) ? v : 'studio';
  if (view === 'review' && arg) state.shootId = +arg;
  VIEWS.forEach((n) => ($('#v-' + n).hidden = n !== view));
  $$('#nav a').forEach((a) => a.classList.toggle('on', a.dataset.v === view));
  render[view]();
  $('#main').scrollTop = 0;
}
$$('#nav a.soon').forEach((a) => a.addEventListener('click', (e) => { e.preventDefault(); toast(`${a.dataset.what} arrives in the next build step`); }));
addEventListener('hashchange', route);
const current = () => VIEWS.find((n) => !$('#v-' + n).hidden);

// ------------------------------------------------------------ folder picker
function pickFolder(title, hint = '') {
  return new Promise((resolve) => {
    const bg = document.createElement('div');
    bg.className = 'modal-bg';
    bg.innerHTML = `<div class="panel modal" role="dialog" aria-modal="true" aria-label="${esc(title)}">
      <div class="head"><div class="t"><h2>${esc(title)}</h2><span class="dim" style="font-size:12.5px">${esc(hint)}</span></div></div>
      <div class="row-in"><button class="btn sm" data-up>↑ Up</button><input class="input mono" data-path style="font-size:12.5px" placeholder="Type or paste a folder path" aria-label="Folder path"><button class="btn sm" data-go>Go</button></div>
      <div class="dirs" data-dirs></div>
      <div class="row-sw"><span class="dim" data-info style="font-size:12.5px"></span><span style="display:flex;gap:8px"><button class="btn" data-cancel>Cancel</button><button class="btn primary" data-ok>Choose this folder</button></span></div></div>`;
    document.body.append(bg);
    let cur = '';
    const close = (v) => { bg.remove(); removeEventListener('keydown', onKey); resolve(v); };
    const onKey = (e) => { if (e.key === 'Escape') close(null); };
    addEventListener('keydown', onKey);
    async function load(p) {
      try {
        const d = await api('/fs' + (p ? '?path=' + encodeURIComponent(p) : ''));
        cur = d.path; $('[data-path]', bg).value = d.path;
        $('[data-up]', bg).disabled = !d.path; $('[data-up]', bg).dataset.to = d.parent ?? '';
        $('[data-ok]', bg).disabled = !d.path;
        const bits = [];
        if (d.video_files) bits.push(`${d.video_files} videos here`);
        if (d.audio_files) bits.push(`${d.audio_files} songs here`);
        $('[data-info]', bg).textContent = d.path ? (bits.join(' · ') || 'Subfolders are included') : 'Pick a drive or folder';
        $('[data-dirs]', bg).innerHTML = d.dirs.map((x) => `<button data-p="${esc(x.path)}">${ICON.folder}<span>${esc(x.name)}</span></button>`).join('') || '<p class="dim" style="padding:14px">No subfolders</p>';
        $$('[data-dirs] button', bg).forEach((b) => (b.onclick = () => load(b.dataset.p)));
      } catch (e) { toast(e.message); }
    }
    $('[data-up]', bg).onclick = (e) => load(e.currentTarget.dataset.to);
    $('[data-go]', bg).onclick = () => load($('[data-path]', bg).value.trim());
    $('[data-path]', bg).onkeydown = (e) => { if (e.key === 'Enter') load(e.target.value.trim()); };
    $('[data-cancel]', bg).onclick = () => close(null);
    $('[data-ok]', bg).onclick = () => close(cur || null);
    bg.onclick = (e) => { if (e.target === bg) close(null); };
    load('');
  });
}

async function startImport(path) {
  try { await api('/import', { method: 'POST', body: { path } }); toast('Import started. Your card is only read, never changed.'); pollJobs(true); }
  catch (e) { toast(e.message); }
}
async function startMusic(path) {
  try { await api('/music/import', { method: 'POST', body: { path } }); toast('Listening to your music: tempo, sections and mood'); pollJobs(true); }
  catch (e) { toast(e.message); }
}

// ------------------------------------------------------------ studio
const render = {};
render.studio = () => {
  const v = $('#v-studio');
  const s = state.status;
  const analyzed = state.tracks.filter((t) => t.status === 'analyzed');
  const folders = s?.config?.music_folders || [];
  const sources = state.sources.map((src) => `
    <div class="card-detect">
      <div class="ico">${camIcon(src.camera)}</div>
      <div class="txt"><b>${esc(src.camera)} · ${esc(src.label)}</b><small>${src.new_files ? `${src.new_files} new files · ${fmtBytes(src.new_bytes)}` : 'Everything on this card is already imported'} · <span class="mono">${esc(src.path)}</span></small></div>
      ${src.new_files ? `<button class="btn primary" data-import="${esc(src.path)}">Import &amp; analyze</button>` : '<span class="chip">Up to date</span>'}
    </div>`).join('') || `
    <div class="card-detect"><div class="ico">${ICON.card}</div><div class="txt"><b>No camera card detected</b><small>Insert your A7C or Mavic 3 card and it shows up here. Photos and clips are both imported, scored and sorted into Stock and Portfolio.</small></div></div>`;
  const vibeCounts = Object.entries(VIBES).map(([k, n]) => `<span class="chip">${n} <b>${analyzed.filter((t) => t.vibe === k).length}</b></span>`).join('');
  v.innerHTML = `
    <div class="head"><div class="t"><span class="eyebrow">${new Date().toLocaleDateString(undefined, { weekday: 'long', month: 'short', day: 'numeric' })}</span><h1>Studio</h1></div>
      <span class="muted" style="font-size:13px">Import footage and music. Nothing is posted anywhere without your approval.</span></div>
    <div class="studio-top">
      <div class="panel pad stack">
        <div class="head"><h2>Import</h2>${s?.config?.auto_import ? '<span class="chip good"><span class="dot" style="margin-right:0"></span>Auto-import on</span>' : '<a class="chip" href="#settings" title="Import as soon as a card is plugged in">Auto-import off</a>'}</div>
        ${sources}
        <div><button class="btn" data-pick-footage>${ICON.folder}Import a folder…</button></div>
        <div class="sep"></div>
        <div class="card-detect">
          <div class="ico">${ICON.music}</div>
          <div class="txt"><b>Music library</b><small>${analyzed.length ? `${analyzed.length} songs analyzed · tempo, sections and mood` : 'Add the folder with songs you like to edit to'}${folders.length ? ` · <span class="mono">${esc(folders.map((f) => f.split(/[\\/]/).pop()).join(', '))}</span>` : ''}</small></div>
          <button class="btn" data-pick-music>${ICON.folder}Import music folder…</button>
        </div>
        ${analyzed.length ? `<div class="vibe-counts">${vibeCounts}</div>` : ''}
      </div>
      <div class="panel pad stack"><div class="t"><span class="eyebrow">Processing</span><h2>Jobs</h2></div><div id="jobList"></div></div>
    </div>
    <div class="stack">
      <div class="head"><h2>Your shoots</h2>${state.shoots.length ? `<a class="btn ghost sm" href="#review">Review &amp; sort</a>` : ''}</div>
      ${state.shoots.length ? `<div class="shoots">${state.shoots.map(shootCard).join('')}</div>` : `<div class="empty"><b>No shoots yet</b><span>Import a card or a folder. intelscraperstudio copies and backs everything up, scores every photo and clip, and sorts the best into Stock and Portfolio.</span></div>`}
    </div>`;
  $$('[data-import]', v).forEach((b) => (b.onclick = () => startImport(b.dataset.import)));
  $('[data-pick-footage]', v).onclick = async () => { const p = await pickFolder('Import a folder', 'Choose a card, or any folder of photos and clips. Subfolders are included.'); if (p) startImport(p); };
  $('[data-pick-music]', v).onclick = async () => { const p = await pickFolder('Import a music folder', 'Choose the folder with songs you want to edit to.'); if (p) startMusic(p); };
  $$('[data-shoot]', v).forEach((b) => (b.onclick = () => (location.hash = 'review/' + b.dataset.shoot)));
  drawJobs();
};

function shootCard(s) {
  return `<button class="panel shoot" data-shoot="${s.id}">
    <div class="frame">${s.cover ? `<img src="/api/clips/${s.cover}/thumb" alt="" loading="lazy">` : '<div class="ph">Analyzing…</div>'}<span class="badge b-br">${[s.photos && plural(s.photos, 'photo'), s.clips && plural(s.clips, 'clip')].filter(Boolean).join(' · ')}</span></div>
    <div class="body">
      <b>${esc(s.name)}</b>
      <div class="dim" style="font-size:12px">${fmtDate(s.started_at)} · ${esc(s.cameras.join(' · '))}${s.clips ? ` · ${s.minutes} min` : ''}</div>
      ${s.lat != null ? `<span class="coord">${coord(s.lat, s.lon)}</span>` : ''}
      <div class="meta">${s.pending ? `<span class="chip amber">${s.pending} analyzing</span>` : ''}${s.portfolio ? `<span class="chip amber">${s.portfolio} portfolio</span>` : ''}${s.stock ? `<span class="chip info">${s.stock} stock</span>` : ''}<span class="chip good">${plural(s.keep, 'keeper')}</span>${s.review ? `<span class="chip">${plural(s.review, 'close call')}</span>` : ''}</div>
    </div></button>`;
}

function drawJobs() {
  const el = $('#jobList');
  if (!el) return;
  const js = state.jobs.slice(0, 6);
  el.innerHTML = js.map((j) => {
    const p = j.total ? Math.round((j.done / j.total) * 100) : j.status === 'done' ? 100 : 0;
    return `<div class="job ${j.status}"><div class="row"><b style="font-weight:500">${esc(j.title)}</b><span class="mono dim" style="font-size:11.5px">${j.status === 'running' ? p + '%' : j.status}</span></div>
      <span class="dim" style="font-size:12px">${esc(j.status === 'done' ? summary(j) : j.message)}</span><div class="meter"><i style="width:${j.status === 'done' ? 100 : p}%"></i></div></div>`;
  }).join('') || '<p class="dim" style="font-size:13px">Nothing running. Plug in a card or pick a folder to start.</p>';
}
function summary(j) {
  const r = j.result || {};
  if (j.kind === 'music') return `${r.analyzed} songs analyzed${r.unchanged ? `, ${r.unchanged} unchanged` : ''}${r.errors ? `, ${r.errors} could not be read` : ''}`;
  if (j.kind === 'import') return `${r.imported} new files imported and analyzed${r.skipped ? `, ${r.skipped} already in the library` : ''}`;
  if (j.kind === 'export') return `${plural(r.items, 'item')} collected in ${r.folder}`;
  return j.message;
}

// ------------------------------------------------------------ review & sort
async function loadClips() {
  if (!state.shootId && state.shoots.length) state.shootId = state.shoots[0].id;
  if (!state.shootId) { state.clips = []; return; }
  state.clips = await api(`/shoots/${state.shootId}/clips`);
}
const ofMedia = () => state.clips.filter((c) => state.media === 'all' || c.kind === state.media);
const matches = (c, f) => f === 'all' || (f === 'stock' ? c.stock : f === 'portfolio' ? c.portfolio : c.final === f);
const visible = () => ofMedia().filter((c) => matches(c, state.filter));
const FILTERS = [['all', 'All'], ['portfolio', 'Portfolio & print'], ['stock', 'Stock'], ['keep', 'Keepers'], ['review', 'Close calls'], ['reject', 'Rejected']];

render.review = async () => {
  const v = $('#v-review');
  if (!state.shoots.length) {
    v.innerHTML = `<div class="head"><div class="t"><h1>Review &amp; sort</h1></div></div><div class="empty"><b>Nothing to review yet</b><span>Import a card or folder from the Studio screen first.</span><a class="btn primary" href="#studio">Go to Studio</a></div>`;
    return;
  }
  await loadClips();
  const shoot = state.shoots.find((s) => s.id === state.shootId) || state.shoots[0];
  const photos = state.clips.filter((c) => c.kind === 'photo').length;
  const videos = state.clips.length - photos;
  if (state.media === 'photo' && !photos) state.media = 'all';
  if (state.media === 'video' && !videos) state.media = 'all';
  const base = ofMedia();
  const n = (f) => base.filter((c) => matches(c, f)).length;
  state.sel = Math.min(state.sel, Math.max(0, visible().length - 1));
  v.innerHTML = `
    <div class="head">
      <div class="t"><span class="eyebrow">${fmtDate(shoot.started_at)} · ${esc(shoot.cameras.join(' + '))}${shoot.lat != null ? ' · ' + coord(shoot.lat, shoot.lon) : ''}</span>
        <h1 id="shootName" title="Click to rename" style="cursor:text">${esc(shoot.name)}</h1></div>
      <div style="display:flex;gap:8px;flex-wrap:wrap;align-items:center">
        <select class="input" id="shootSel" aria-label="Shoot" style="width:auto">${state.shoots.map((s) => `<option value="${s.id}" ${s.id === shoot.id ? 'selected' : ''}>${esc(s.name)}</option>`).join('')}</select>
        <button class="btn" data-export="stock" ${shoot.stock ? '' : 'disabled'}>Export stock <span class="mono dim">${shoot.stock}</span></button>
        <button class="btn primary" data-export="portfolio" ${shoot.portfolio ? '' : 'disabled'}>Export portfolio <span class="mono">${shoot.portfolio}</span></button>
      </div>
    </div>
    <div class="row-sw" style="flex-wrap:wrap;justify-content:flex-start">
      ${photos && videos ? `<div class="seg" id="mediaSeg">${[['all', `All ${state.clips.length}`], ['photo', `Photos ${photos}`], ['video', `Clips ${videos}`]].map(([k, l]) => `<button data-m="${k}" class="${state.media === k ? 'on' : ''}">${l}</button>`).join('')}</div>` : ''}
      <div class="seg" id="filters">${FILTERS.map(([k, l]) => `<button data-f="${k}" class="${state.filter === k ? 'on' : ''}">${l} <span class="mono dim">${n(k)}</span></button>`).join('')}</div>
    </div>
    <div class="ai-note">${ICON.spark}<span>Everything was scored and sorted. <b style="color:var(--fg);font-weight:500">${plural(n('portfolio'), 'portfolio pick')}</b> (sharp, clean, high resolution and strong impact) and <b style="color:var(--fg);font-weight:500">${n('stock')} stock-ready</b> (technically clean${videos ? ', steady 1080p+ clips of 5 s or more' : ''}). ${n('review') ? `Check the ${plural(n('review'), 'close call')}; ` : ''}your calls always win.
      <span class="keys" style="margin-left:6px"><span><kbd>P</kbd> portfolio</span><span><kbd>S</kbd> stock</span><span><kbd>K</kbd> keep</span><span><kbd>X</kbd> reject</span><span><kbd>U</kbd> undo</span><span><kbd>←</kbd><kbd>→</kbd> move</span>${videos ? '<span><kbd>Space</kbd> best moment</span>' : ''}</span></span></div>
    <div class="split"><div class="clips" id="clipGrid"></div><aside class="panel pad detail" id="clipDetail"></aside></div>`;
  $('#shootSel').onchange = (e) => { location.hash = 'review/' + e.target.value; state.sel = 0; };
  $$('#filters button').forEach((b) => (b.onclick = () => { state.filter = b.dataset.f; state.sel = 0; render.review(); }));
  $$('#mediaSeg button').forEach((b) => (b.onclick = () => { state.media = b.dataset.m; state.sel = 0; render.review(); }));
  $$('[data-export]', v).forEach((b) => (b.onclick = () => startExport(b.dataset.export, shoot.id)));
  $('#shootName').onclick = () => renameShoot(shoot);
  drawClips();
};

async function startExport(collection, shootId) {
  try { await api('/export', { method: 'POST', body: { collection, shoot_id: shootId } }); toast(`Collecting your ${collection === 'stock' ? 'stock' : 'portfolio'} picks into a folder…`); pollJobs(true); }
  catch (e) { toast(e.message); }
}

function renameShoot(shoot) {
  const h = $('#shootName');
  const input = document.createElement('input');
  input.className = 'input'; input.value = shoot.name; input.style.fontSize = '22px'; input.setAttribute('aria-label', 'Shoot name');
  h.replaceWith(input); input.focus(); input.select();
  const save = async () => { const name = input.value.trim(); if (name && name !== shoot.name) { await api(`/shoots/${shoot.id}`, { method: 'PATCH', body: { name } }); await refreshShoots(); toast('Shoot renamed'); } render.review(); };
  input.onkeydown = (e) => { if (e.key === 'Enter') save(); if (e.key === 'Escape') render.review(); };
  input.onblur = save;
}

function useTags(c) {
  return `${c.portfolio ? '<span class="chip amber">Portfolio</span>' : ''}${c.stock ? '<span class="chip info">Stock</span>' : ''}`;
}

function drawClips() {
  const list = visible();
  $('#clipGrid').innerHTML = list.map((c, i) => {
    const mark = c.final === 'keep' ? '✓' : c.final === 'reject' ? '✕' : '?';
    const kindBadge = c.kind === 'photo' ? `${esc(c.codec || 'Photo')}${c.companion ? '+JPG' : ''}` : fmtDur(c.duration);
    return `<button class="clip ${i === state.sel ? 'sel' : ''} ${c.final === 'reject' ? 'rejected' : ''}" data-i="${i}">
      ${c.status === 'analyzed' ? `<span class="verdict ${c.final} ${c.user_verdict ? 'mine' : ''}" title="${c.user_verdict ? 'Your call' : 'App call'}">${mark}</span>` : ''}
      <div class="frame">${c.thumb ? `<img src="/api/clips/${c.id}/thumb" alt="" loading="lazy">` : `<div class="ph">${c.status === 'error' ? 'Could not analyze' : 'Analyzing…'}</div>`}
        <span class="badge b-bl">${kindBadge}</span><span class="badge b-tl">${esc((c.camera || '').replace('Sony ', '').replace('DJI ', ''))}</span></div>
      <div class="ln"><span class="mono" style="font-size:11.5px">${esc(c.src_name)}</span>${c.score != null ? `<span class="score ${scoreCls(c.score)}">${c.score}</span>` : ''}</div>
      <div class="tags">${useTags(c)}</div>
      <div class="why">${esc((c.reasons || [])[0] || c.error || '')}</div></button>`;
  }).join('') || '<p class="muted">Nothing in this group.</p>';
  $$('#clipGrid .clip').forEach((b) => (b.onclick = () => { state.sel = +b.dataset.i; drawClips(); }));
  drawDetail();
}

function useRow(c, key, label) {
  const notes = (c.uses || {}).notes || {};
  if (key === 'portfolio' && c.kind === 'video') return '';
  const mine = (c.user_uses || {})[key];
  const on = !!c[key];
  return `<div class="use"><div class="row-sw"><span><b style="font-weight:500">${label}</b> ${mine !== undefined ? `<span class="chip">Your call</span>` : ''}</span>
      <button class="switch" role="switch" aria-checked="${on}" data-use="${key}" aria-label="${label}"></button></div>
    <span class="dim" style="font-size:12px">${esc(notes[key] || '')}</span>
    ${mine !== undefined ? `<button class="linkish" data-reset-use="${key}">Use the app's call</button>` : ''}</div>`;
}

function drawDetail() {
  const el = $('#clipDetail');
  const c = visible()[state.sel];
  if (!c) { el.innerHTML = '<p class="muted">Select an item.</p>'; return; }
  const m = c.metrics || {};
  const meta = c.meta || {};
  const photo = c.kind === 'photo';
  const bar = (label, val) => val == null ? '' : `<div class="bar-row ${val < 50 ? 'badv' : val < 75 ? 'warn' : ''}"><span class="muted">${label}</span><div class="meter"><i style="width:${val}%"></i></div><span class="mono dim">${val}</span></div>`;
  const q = (m.quality_curve || []).map((x) => `<i style="height:${Math.max(4, x * 100)}%"></i>`).join('');
  const dur = c.duration || 1;
  const exposure = [meta.iso && `ISO ${meta.iso}`, meta.shutter, meta.aperture, meta.focal_mm && `${meta.focal_mm} mm`].filter(Boolean).join(' · ');
  const pr = (c.uses || {}).print;
  const media = photo
    ? `<div class="frame vf photo"><img src="/api/clips/${c.id}/preview" alt="${esc(c.src_name)}"><span class="badge b-tl">${esc(c.codec || '')}${c.companion ? ' + JPG' : ''}</span></div>`
    : `<div class="frame vf"><video id="vid" preload="metadata" playsinline controls poster="${c.thumb ? `/api/clips/${c.id}/thumb` : ''}" src="/api/clips/${c.id}/video"></video><span class="badge b-tl">${esc(c.color_profile || '')}</span></div>`;
  el.innerHTML = `
    ${media}
    <div class="head"><div class="t"><b class="mono">${esc(c.src_name)}</b><span class="dim" style="font-size:12px">${esc(c.camera)} · ${fmtTime(c.recorded_at)}</span>${c.lat != null ? `<span class="coord">${coord(c.lat, c.lon)}${meta.altitude_m ? ` · ${Math.round(meta.altitude_m)} m up` : ''}</span>` : ''}</div>
      ${c.score != null ? `<span class="score ${scoreCls(c.score)}" style="font-size:13px">${c.score}</span>` : ''}</div>
    ${(c.reasons || []).length ? `<p class="muted" style="font-size:13px">${esc(c.reasons.join('. '))}.</p>` : ''}
    ${c.status === 'analyzed' ? `<div class="uses">${useRow(c, 'portfolio', 'Portfolio &amp; print')}${useRow(c, 'stock', 'Stock')}</div>` : ''}
    ${!photo && c.best_end ? `<div class="stack" style="gap:6px"><div class="row-sw"><span class="eyebrow">Best moment · ${fmtDur(c.best_start)}–${fmtDur(c.best_end)}</span><button class="btn sm" id="playBest">▶ Play it</button></div>
      <div class="moment" id="moment" title="Click to jump"><div class="q">${q}</div><div class="win" style="left:${pct((c.best_start / dur) * 100)};width:${pct(((c.best_end - c.best_start) / dur) * 100)}"></div><div class="head-pos" id="headPos" style="left:0"></div></div></div>` : ''}
    <div class="bars">${photo ? bar('Focus', m.focus) + bar('Light', m.light) + bar('Noise', m.noise) + bar('Impact', m.impact) : bar('Steadiness', m.steady) + bar('Focus', m.focus) + bar('Light', m.light)}</div>
    <dl class="kv">
      <dt>${photo ? 'Size' : 'Format'}</dt><dd>${c.width ? `${c.width}×${c.height}` : '–'}${photo && c.megapixels ? ` · ${c.megapixels} MP` : ''}${c.fps ? ` · ${c.fps}p` : ''}</dd>
      ${photo ? '' : `<dt>Codec</dt><dd>${esc(c.codec || '–')}${c.bit_depth ? ` · ${c.bit_depth}-bit` : ''}</dd>`}
      <dt>Color</dt><dd>${esc(c.color_profile || '–')}${meta.gamut ? ` · ${esc(meta.gamut)}` : ''}</dd>
      ${meta.lens ? `<dt>Lens</dt><dd>${esc(meta.lens)}</dd>` : ''}
      ${exposure ? `<dt>Exposure</dt><dd>${esc(exposure)}</dd>` : ''}
      ${photo && pr ? `<dt>Print</dt><dd>${pr.in_300[0]}×${pr.in_300[1]} in @300 dpi</dd>` : ''}
      ${photo && m.tilt_deg != null && Math.abs(m.tilt_deg) >= 0.8 ? `<dt>Horizon</dt><dd>tilted ${Math.abs(m.tilt_deg).toFixed(1)}°</dd>` : ''}
      ${photo ? '' : `<dt>Length</dt><dd>${fmtDur(c.duration)}</dd>`}
      ${c.similar_to ? `<dt>Better ${photo ? 'frame' : 'take'}</dt><dd>${esc(state.clips.find((x) => x.id === c.similar_to)?.src_name || '#' + c.similar_to)}</dd>` : ''}
    </dl>
    <div style="display:flex;gap:8px"><button class="btn" style="flex:1;justify-content:center" id="dX">✕ Reject <kbd>X</kbd></button><button class="btn primary" style="flex:1;justify-content:center" id="dK">✓ Keep <kbd>K</kbd></button></div>
    <div class="row-sw"><button class="btn ghost sm" id="dU" ${c.user_verdict ? '' : 'disabled'}>Use the app's call</button><button class="btn ghost sm" id="reveal">Show file</button></div>`;
  $('#dK').onclick = () => setVerdict('keep');
  $('#dX').onclick = () => setVerdict('reject');
  $('#dU').onclick = () => setVerdict(null);
  $('#reveal').onclick = () => api(`/clips/${c.id}/reveal`, { method: 'POST' }).catch((e) => toast(e.message));
  $$('[data-use]', el).forEach((b) => (b.onclick = () => setUse(b.dataset.use, b.getAttribute('aria-checked') !== 'true')));
  $$('[data-reset-use]', el).forEach((b) => (b.onclick = () => setUse(b.dataset.resetUse, null)));
  const vid = $('#vid');
  if (!vid) return;
  vid.onerror = () => toast('This clip can’t play in the browser yet. Turn on preview files in Settings.');
  if ($('#playBest')) {
    $('#playBest').onclick = () => playBest(c);
    $('#moment').onclick = (e) => { const r = e.currentTarget.getBoundingClientRect(); vid.currentTime = ((e.clientX - r.left) / r.width) * dur; vid.play().catch(() => {}); };
    vid.ontimeupdate = () => { $('#headPos').style.left = pct((vid.currentTime / dur) * 100); if (state.playing === c.id && vid.currentTime >= c.best_end) { vid.pause(); state.playing = null; } };
  }
}
function playBest(c) { const vid = $('#vid'); if (!vid) return; vid.currentTime = c.best_start || 0; state.playing = c.id; vid.play().catch(() => {}); }

async function setVerdict(verdict) {
  const list = visible();
  const c = list[state.sel];
  if (!c) return;
  await api(`/clips/${c.id}/verdict`, { method: 'POST', body: { verdict } });
  c.user_verdict = verdict; c.final = verdict || c.verdict;
  const next = list.findIndex((x, i) => i > state.sel && x.final === 'review');
  if (verdict && next > -1 && state.filter === 'all') state.sel = next;
  if (state.filter !== 'all') render.review(); else drawClips();
  refreshShoots();
}

async function setUse(collection, value) {
  const c = visible()[state.sel];
  if (!c || c.status !== 'analyzed' || (collection === 'portfolio' && c.kind === 'video')) return;
  try {
    await api(`/clips/${c.id}/uses`, { method: 'POST', body: { collection, value } });
    c.user_uses = { ...(c.user_uses || {}) };
    if (value === null) delete c.user_uses[collection]; else c.user_uses[collection] = value;
    c[collection] = value === null ? !!(c.uses || {})[collection] : value;
    const label = collection === 'stock' ? 'Stock' : 'Portfolio';
    toast(value === null ? `${label}: back to the app's call` : value ? `Added to ${label}` : `Removed from ${label}`);
    await refreshShoots();
    if (state.filter === collection) render.review(); else drawClips();
  } catch (e) { toast(e.message); }
}

addEventListener('keydown', (e) => {
  if (current() !== 'review' || /INPUT|TEXTAREA|SELECT/.test(e.target.tagName) || $('.modal-bg')) return;
  const k = e.key.toLowerCase();
  const c = visible()[state.sel];
  if (k === 'k') setVerdict('keep');
  else if (k === 'x') setVerdict('reject');
  else if (k === 'u') setVerdict(null);
  else if (k === 's' && c) setUse('stock', !c.stock);
  else if (k === 'p' && c) setUse('portfolio', !c.portfolio);
  else if (e.key === 'ArrowRight') { state.sel = Math.min(visible().length - 1, state.sel + 1); drawClips(); }
  else if (e.key === 'ArrowLeft') { state.sel = Math.max(0, state.sel - 1); drawClips(); }
  else if (e.code === 'Space' && e.target.tagName !== 'VIDEO') { e.preventDefault(); if (c && c.kind === 'video') playBest(c); }
});

// ------------------------------------------------------------ music
const audio = new Audio();
let playingTrack = null;
audio.onended = () => { playingTrack = null; drawTracks(); };

render.music = () => {
  const v = $('#v-music');
  const all = state.tracks;
  const ok = all.filter((t) => t.status === 'analyzed');
  v.innerHTML = `
    <div class="head"><div class="t"><span class="eyebrow">${ok.length} songs analyzed${all.length - ok.length ? ` · ${all.length - ok.length} pending or unreadable` : ''}</span><h1>Music library</h1></div>
      <button class="btn primary" id="addMusic">${ICON.folder}Import music folder…</button></div>
    <div class="ai-note">${ICON.spark}<span>Each song's tempo, beat grid, sections and energy are measured, and its vibe is estimated. If a vibe looks wrong, change it here. The editor will use your choice when it picks songs for each edit.</span></div>
    <div class="vibe-filter" id="vibeFilter">
      <button class="chip ${state.vibe === 'all' ? 'on' : ''}" data-v="all">All <b>${ok.length}</b></button>
      ${Object.entries(VIBES).map(([k, n]) => `<button class="chip ${state.vibe === k ? 'on' : ''}" data-v="${k}">${n} <b>${ok.filter((t) => t.vibe === k).length}</b></button>`).join('')}
    </div>
    <div class="panel"><div class="tracks" id="trackList"></div></div>`;
  $('#addMusic').onclick = async () => { const p = await pickFolder('Import a music folder', 'Choose the folder with songs you want to edit to.'); if (p) startMusic(p); };
  $$('#vibeFilter button').forEach((b) => (b.onclick = () => { state.vibe = b.dataset.v; render.music(); }));
  drawTracks();
};

function ribbon(t) {
  const d = t.duration || 1;
  const e = t.energy || [];
  const n = 80;
  const curve = Array.from({ length: n }, (_, i) => { const a = Math.floor((i / n) * e.length), b = Math.max(a + 1, Math.floor(((i + 1) / n) * e.length)); const s = e.slice(a, b); return s.length ? s.reduce((x, y) => x + y, 0) / s.length : 0; });
  const secs = (t.sections || []).map((s) => `<span class="sec" style="left:${(s.start / d) * 100}%;width:${((s.end - s.start) / d) * 100}%;background:rgba(232,176,74,${(0.05 + s.energy * 0.22).toFixed(2)})" title="${esc(s.name)} · ${fmtDur(s.start)}–${fmtDur(s.end)}">${(s.end - s.start) / d > 0.09 ? esc(s.name) : ''}</span>`).join('');
  return `<div class="ribbon">${secs}<div class="curve">${curve.map((x) => `<i style="height:${Math.round(x * 100)}%"></i>`).join('')}</div></div>`;
}

function drawTracks() {
  const el = $('#trackList');
  if (!el) return;
  const ts = state.tracks.filter((t) => state.vibe === 'all' || t.vibe === state.vibe);
  if (!state.tracks.length) { el.innerHTML = `<div class="empty" style="border:0"><b>No music yet</b><span>Import the folder with songs you like to edit to. Every song gets its tempo, sections and vibe measured.</span></div>`; return; }
  el.innerHTML = `<div class="trk th"><span></span><span>Song</span><span>Vibe</span><span>Tempo</span><span class="len">Length</span><span class="ribbon-h">Sections &amp; energy</span></div>` +
    ts.map((t) => `<div class="trk">
      <button class="pbtn ${playingTrack === t.id ? 'on' : ''}" data-play="${t.id}" aria-label="Play ${esc(t.title)}">${playingTrack === t.id ? '■' : '▶'}</button>
      <span class="nm"><b title="${esc(t.title)}">${esc(t.title)}</b><small>${t.status === 'analyzed' ? esc((t.sections || []).map((s) => s.name).join(' → ')) : t.status === 'error' ? esc(t.error || 'Could not read this file') : 'Waiting to be analyzed'}</small></span>
      ${t.status === 'analyzed' ? `<select class="input" data-vibe="${t.id}" aria-label="Vibe for ${esc(t.title)}">${Object.entries(VIBES).map(([k, n]) => `<option value="${k}" ${t.vibe === k ? 'selected' : ''}>${n}${t.vibe_auto === k ? ' (auto)' : ''}</option>`).join('')}</select>` : '<span></span>'}
      <span class="tempo"><span class="mono" style="font-size:12.5px">${t.bpm ? Math.round(t.bpm) + ' BPM' : '–'}</span>${t.status === 'analyzed' ? `<span class="oct"><button data-tempo="half" data-t="${t.id}" title="Tempo reads too fast? Halve it">½×</button><button data-tempo="double" data-t="${t.id}" title="Tempo reads too slow? Double it">2×</button></span>` : ''}</span>
      <span class="mono dim len" style="font-size:12.5px">${fmtDur(t.duration)}</span>
      ${t.status === 'analyzed' ? ribbon(t) : '<span></span>'}
    </div>`).join('');
  $$('[data-play]', el).forEach((b) => (b.onclick = () => {
    const id = +b.dataset.play;
    if (playingTrack === id) { audio.pause(); playingTrack = null; }
    else { audio.src = `/api/music/${id}/audio`; audio.play().catch(() => toast('This file type can’t play in the browser, but it was still analyzed.')); playingTrack = id; }
    drawTracks();
  }));
  $$('[data-tempo]', el).forEach((b) => (b.onclick = async () => {
    try { await api(`/music/${b.dataset.t}`, { method: 'PATCH', body: { tempo: b.dataset.tempo } }); await refreshTracks(); drawTracks(); toast('Tempo corrected. Beats re-gridded'); } catch (e) { toast(e.message); }
  }));
  $$('[data-vibe]', el).forEach((s) => (s.onchange = async () => {
    await api(`/music/${s.dataset.vibe}`, { method: 'PATCH', body: { vibe: s.value } });
    const t = state.tracks.find((x) => x.id === +s.dataset.vibe); t.vibe = s.value;
    toast(`Saved. “${t.title}” is now ${VIBES[s.value]}`); render.music();
  }));
}

// ------------------------------------------------------------ settings
render.settings = () => {
  const v = $('#v-settings');
  const c = state.status?.config || {};
  const sw = (id, on, label, hint) => `<div class="row-sw"><span><b style="font-weight:500">${label}</b><br><span class="dim" style="font-size:12.5px">${hint}</span></span><button class="switch" role="switch" id="${id}" aria-checked="${!!on}" aria-label="${label}"></button></div>`;
  v.innerHTML = `
    <div class="head"><div class="t"><span class="eyebrow">Setup</span><h1>Settings</h1></div></div>
    <div class="panel pad stack" style="max-width:760px">
      <div class="field"><label for="libRoot">Library folder: where imported footage is copied</label><div class="row-in"><input class="input mono" id="libRoot" value="${esc(c.library_root)}"><button class="btn" data-browse="libRoot">Browse…</button></div></div>
      <div class="field"><label for="bakRoot">Backup folder: a second copy on another drive (recommended)</label><div class="row-in"><input class="input mono" id="bakRoot" value="${esc(c.backup_root)}" placeholder="No backup copy"><button class="btn" data-browse="bakRoot">Browse…</button></div></div>
      <div class="sep"></div>
      ${sw('swVerify', c.verify_copies, 'Verify every copy', 'Re-reads each copied file and compares it with the card. Slower, but you can trust the backup.')}
      ${sw('swProxy', c.make_proxies, 'Make preview files', 'Small 540p copies for smooth playback while reviewing. Uses your NVIDIA GPU when available.')}
      ${sw('swAuto', c.auto_import, 'Import automatically', 'Start importing, analyzing and sorting as soon as a card with new files is plugged in.')}
      <div class="field"><label for="gap">Start a new shoot after a break of (hours)</label><input class="input" id="gap" type="number" min="1" max="72" step="1" value="${c.shoot_gap_hours ?? 6}" style="max-width:120px"></div>
      <div class="sep"></div>
      <div class="field"><label for="share">Portfolio picks: share of each shoot's best photos (percent)</label><input class="input" id="share" type="number" min="2" max="50" step="1" value="${Math.round((c.portfolio_share ?? 0.15) * 100)}" style="max-width:120px"></div>
      <div class="field"><label for="colRoot">Collections folder: where Stock and Portfolio exports go</label><div class="row-in"><input class="input mono" id="colRoot" value="${esc(c.collections_root)}" placeholder="${esc((c.library_root || '') + (/\\/.test(c.library_root || '') ? '\\' : '/') + 'Collections')}"><button class="btn" data-browse="colRoot">Browse…</button></div></div>
      <div><button class="btn primary" id="saveSettings">Save settings</button></div>
    </div>
    <div class="panel pad stack" style="max-width:760px"><h2>System</h2>
      <dl class="kv"><dt>FFmpeg</dt><dd>${state.status?.ffmpeg ? 'Installed' : 'Missing. Install with: winget install Gyan.FFmpeg'}</dd>
      <dt>GPU</dt><dd>${esc(state.status?.gpu?.name || 'No NVIDIA GPU detected')}</dd>
      <dt>Library</dt><dd>${state.status?.counts?.clips ?? 0} files · ${fmtBytes(state.status?.counts?.bytes)}</dd>
      <dt>Version</dt><dd>${esc(state.status?.version || '')}</dd></dl></div>`;
  $$('.switch', v).forEach((s) => (s.onclick = () => s.setAttribute('aria-checked', s.getAttribute('aria-checked') !== 'true')));
  $$('[data-browse]', v).forEach((b) => (b.onclick = async () => { const p = await pickFolder('Choose a folder'); if (p) $('#' + b.dataset.browse).value = p; }));
  $('#saveSettings').onclick = async () => {
    try {
      await api('/config', { method: 'PUT', body: { library_root: $('#libRoot').value.trim(), backup_root: $('#bakRoot').value.trim(), verify_copies: $('#swVerify').getAttribute('aria-checked') === 'true', make_proxies: $('#swProxy').getAttribute('aria-checked') === 'true', auto_import: $('#swAuto').getAttribute('aria-checked') === 'true', shoot_gap_hours: +$('#gap').value || 6, portfolio_share: (+$('#share').value || 15) / 100, collections_root: $('#colRoot').value.trim() } });
      await refreshStatus(); toast('Settings saved');
    } catch (e) { toast(e.message); }
  };
};

// ------------------------------------------------------------ polling
async function refreshStatus() {
  try {
    const s = (state.status = await api('/status'));
    $('#devOs').textContent = s.platform.startsWith('win') ? 'Windows' : s.platform === 'darwin' ? 'macOS' : 'Linux';
    $('#gpuName').textContent = s.gpu ? s.gpu.name.replace('NVIDIA GeForce ', '') : 'No NVIDIA GPU found';
    $('#gpuPct').textContent = s.gpu ? s.gpu.util + '%' : '';
    $('#gpuBar').style.width = (s.gpu ? s.gpu.util : 0) + '%';
    $('#devDot').classList.toggle('off', !s.ffmpeg);
    $('#diskTxt').textContent = s.disk ? `${fmtBytes(s.disk.used)} / ${fmtBytes(s.disk.total)}` : '–';
  } catch { $('#devDot').classList.add('off'); }
}
async function refreshShoots() {
  state.shoots = await api('/shoots');
  const pending = state.shoots.reduce((a, s) => a + s.review, 0);
  $('#navReview').hidden = !pending; $('#navReview').textContent = pending;
}
async function refreshTracks() { state.tracks = await api('/music'); }

let lastRunning = new Set();
let jobTimer = 0;
async function pollJobs(soon = false) {
  clearTimeout(jobTimer);
  try {
    state.jobs = await api('/jobs');
    const running = new Set(state.jobs.filter((j) => j.status === 'running' || j.status === 'queued').map((j) => j.id));
    const finished = [...lastRunning].filter((id) => !running.has(id));
    if (finished.length) {
      await Promise.all([refreshShoots(), refreshTracks(), refreshStatus()]);
      finished.forEach((id) => { const j = state.jobs.find((x) => x.id === id); if (j) toast(j.status === 'error' ? `Problem: ${j.error}` : summary(j)); });
      if (current() !== 'review') render[current()](); else render.review();
    } else if (running.size && current() === 'studio') {
      await refreshShoots();
      const grid = $('.shoots'); if (grid) grid.innerHTML = state.shoots.map(shootCard).join('');
    }
    lastRunning = running;
    drawJobs();
    jobTimer = setTimeout(pollJobs, running.size || soon ? 1200 : 4000);
  } catch { jobTimer = setTimeout(pollJobs, 5000); }
}
async function pollSources() {
  try {
    const before = JSON.stringify(state.sources);
    state.sources = await api('/sources');
    if (JSON.stringify(state.sources) !== before && current() === 'studio') {
      if (state.sources.length > JSON.parse(before || '[]').length) toast(`Card detected: ${state.sources.at(-1).camera}`);
      render.studio();
    }
  } catch { /* engine restarting */ }
  setTimeout(pollSources, 6000);
}

// ------------------------------------------------------------ topographic background
function drawTopo() {
  const m = $('#main'); const w = Math.min(1800, m.clientWidth || 1200), h = Math.min(1300, m.clientHeight || 900);
  const c = document.createElement('canvas'); c.width = w; c.height = h; const x = c.getContext('2d'); if (!x) return;
  const hash = (i, j) => { const n = Math.sin(i * 127.1 + j * 311.7 + 17.3) * 43758.5453; return n - Math.floor(n); };
  const sm = (t) => t * t * (3 - 2 * t);
  const noise = (px, py) => { const i = Math.floor(px), j = Math.floor(py), fx = sm(px - i), fy = sm(py - j); const a = hash(i, j), b = hash(i + 1, j), cc = hash(i, j + 1), d = hash(i + 1, j + 1); return a + (b - a) * fx + (cc - a) * fy + (a - b - cc + d) * fx * fy; };
  const fbm = (px, py) => noise(px, py) * 0.6 + noise(px * 2.1 + 5, py * 2.1 + 9) * 0.28 + noise(px * 4.3 + 11, py * 4.3 + 3) * 0.12;
  const S = 7, cols = Math.ceil(w / S) + 1, rows = Math.ceil(h / S) + 1, F = new Float32Array(cols * rows), sc = 1 / 300;
  let lo = 9, hi = -9;
  for (let j = 0; j < rows; j++) for (let i = 0; i < cols; i++) { const v = fbm(i * S * sc + 3.7, j * S * sc + 1.3); F[j * cols + i] = v; if (v < lo) lo = v; if (v > hi) hi = v; }
  const T = { 1: [[3, 2]], 2: [[2, 1]], 3: [[3, 1]], 4: [[0, 1]], 5: [[3, 0], [2, 1]], 6: [[0, 2]], 7: [[3, 0]], 8: [[3, 0]], 9: [[0, 2]], 10: [[0, 1], [3, 2]], 11: [[0, 1]], 12: [[3, 1]], 13: [[2, 1]], 14: [[3, 2]] };
  const step = (hi - lo) / 22; let k = 0;
  for (let L = lo + step; L < hi; L += step, k++) {
    x.beginPath(); x.strokeStyle = k % 5 === 4 ? 'rgba(236,235,231,.075)' : 'rgba(236,235,231,.035)'; x.lineWidth = k % 5 === 4 ? 1.1 : 0.8;
    for (let j = 0; j < rows - 1; j++) for (let i = 0; i < cols - 1; i++) {
      const tl = F[j * cols + i], tr = F[j * cols + i + 1], bl = F[(j + 1) * cols + i], br = F[(j + 1) * cols + i + 1];
      const segs = T[(tl > L) * 8 + (tr > L) * 4 + (br > L) * 2 + (bl > L)]; if (!segs) continue;
      const x0 = i * S, y0 = j * S;
      const pt = (e) => e === 0 ? [x0 + (S * (L - tl)) / (tr - tl), y0] : e === 1 ? [x0 + S, y0 + (S * (L - tr)) / (br - tr)] : e === 2 ? [x0 + (S * (L - bl)) / (br - bl), y0 + S] : [x0, y0 + (S * (L - tl)) / (bl - tl)];
      for (const [e1, e2] of segs) { const p1 = pt(e1), p2 = pt(e2); x.moveTo(p1[0], p1[1]); x.lineTo(p2[0], p2[1]); }
    }
    x.stroke();
  }
  m.style.backgroundImage = `url(${c.toDataURL('image/png')})`;
}

// ------------------------------------------------------------ boot
(async function boot() {
  drawTopo();
  let t = 0; addEventListener('resize', () => { clearTimeout(t); t = setTimeout(drawTopo, 300); });
  await Promise.all([refreshStatus(), refreshShoots().catch(() => {}), refreshTracks().catch(() => {})]);
  route();
  pollJobs(); pollSources();
  setInterval(refreshStatus, 5000);
})();
