// Startansicht: Tablet (eine Kamera im Vollbild), Monitor (eine groß, Rest in
// einer Leiste) oder Raster (alle gleich groß). Live-Bild über go2rtc (MSE),
// Zustand über WebSocket. Übersicht und Kamera-Detail lädt details.js nach.
import { createVideo, closeVideo } from './video.js';

const params = new URLSearchParams(location.search);
const viewParam = params.get('view');
const root = document.getElementById('root');

let S = null;              // letzter Stand vom Server
let connected = false;
let rotIdx = 0;
let rotStart = Date.now();
let pin = null;            // { cam, until } – nur auf diesem Bildschirm
let menuOpen = false;
let rotateOn = true;
let built = '';            // Signatur des aufgebauten Gerüsts
let shownMenu = '';        // zuletzt gezeichnetes Menü – nur bei Änderung neu, sonst springt der Scroll

// Bildschirmeigene Einstellung; ohne Browser-Speicher gilt der Standard
const local = {
  get(k) { try { return localStorage.getItem('cam-recorder:' + k); } catch (e) { return null; } },
  set(k, v) { try { localStorage.setItem('cam-recorder:' + k, v); } catch (e) { /* egal */ } },
};
let gridOn = local.get('grid') === '1';

const esc = (s) => String(s).replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
const view = () => viewParam || (gridOn ? 'grid' : innerWidth >= 1400 ? 'monitor' : 'tablet');
const now = () => Date.now();

// ---------------------------------------------------------------- Video-Pool
const videos = new Map();
function video(cam, quality) {
  const key = cam + '|' + quality;
  let el = videos.get(key);
  if (!el) {
    el = createVideo(quality === 'main' ? cam : cam + '_sub');
    videos.set(key, el);
  }
  return el;
}
function dropVideo(key) {
  const el = videos.get(key);
  if (!el) return;
  closeVideo(el);
  videos.delete(key);
}
function mount(slot, cam, quality) {
  const el = video(cam, quality);
  if (el.parentElement !== slot) slot.appendChild(el);
}

// ---------------------------------------------------------------- Ableitungen
function derive() {
  const cams = S.cams;
  const byId = Object.fromEntries(cams.map((c) => [c.id, c]));
  if (pin && (pin.until <= now() || !byId[pin.cam])) pin = null;
  const motions = cams.filter((c) => c.motion_since).sort((a, b) => a.motion_since - b.motion_since).map((c) => c.id);
  const newest = motions.length ? motions[motions.length - 1] : null;
  const rotList = cams.filter((c) => !c.off).map((c) => c.id);
  const ring = rotList.length ? rotList : cams.map((c) => c.id);
  const rotCam = ring[rotIdx % ring.length];
  const nextCam = ring[(rotIdx + 1) % ring.length];
  const tablet = view() === 'tablet';
  const grid = view() === 'grid';
  if (grid) pin = null;
  const split = tablet && !pin && motions.length === 2;
  const main = pin ? pin.cam : (newest || (rotateOn ? rotCam : ring[0]));
  const showAlso = tablet && !split && (motions.length >= 3 || (pin && motions.length >= 1));
  const also = showAlso ? motions.filter((id) => id !== main).reverse() : [];
  const rotating = !grid && !split && !pin && !newest && rotateOn && ring.length > 1;
  return { cams, byId, motions, newest, main, nextCam, split, also, rotating, tablet, grid };
}

function sigInfo(c) {
  if (c.lost) return { text: 'kein Empfang', cls: 'lost' };
  if (c.signal === null || c.signal === undefined) return null;
  const d = Math.round(c.signal);
  return { text: (d < 0 ? '−' + Math.abs(d) : d) + ' dBm', cls: d > -65 ? 'good' : d > -70 ? 'mid' : 'weak' };
}
const offWord = (c) => c.off_reason === 'privacy' ? 'Privat · Kamera aus' : 'Pausiert';

// ---------------------------------------------------------------- Gerüst
function buildSkeleton(d) {
  const sig = view() + ':' + d.cams.map((c) => c.id).join(',');
  if (sig === built) return;
  built = sig;
  shownMenu = '';
  for (const key of [...videos.keys()]) dropVideo(key);
  if (d.grid) {
    const n = d.cams.length;
    const cols = n <= 1 ? 1 : n <= 4 ? 2 : n <= 9 ? 3 : 4;
    root.innerHTML = `<div class="grid" style="grid-template-columns:repeat(${cols},minmax(0,1fr));grid-template-rows:repeat(${Math.ceil(n / cols)},minmax(0,1fr))">${d.cams.map((c) =>
      `<button type="button" class="tile gtile" data-open="${esc(c.id)}" aria-label="${esc(c.label)} öffnen"><div class="slot"></div><span class="tov"></span></button>`).join('')}</div>
      <div id="ov"></div><div id="menu"></div>`;
  } else if (d.tablet) {
    root.innerHTML = d.cams.map((c) => `<div class="layer" data-layer="${esc(c.id)}"><div class="slot"></div></div>`).join('') +
      '<div id="ov"></div><div id="menu"></div>';
  } else {
    root.innerHTML = `<div class="monitor"><div class="big" id="big">${d.cams.map((c) =>
      `<div class="layer" data-layer="${esc(c.id)}"><div class="slot"></div></div>`).join('')}<div id="bigov"></div></div>
      <div class="strip" id="strip">${d.cams.map((c) =>
      `<button type="button" class="tile" data-tile="${esc(c.id)}" aria-label="${esc(c.label)} hier anheften"><div class="slot"></div><span class="tov"></span></button>`).join('')}</div></div>
      <div id="ov"></div><div id="menu"></div>`;
  }
}

// ---------------------------------------------------------------- Rendern
let details = null;  // Detailansicht (lazy geladen)
const onDetails = () => /^#(overview|details|cam\/|play\/)/.test(location.hash);

function render() {
  if (!S) return;
  if (onDetails()) {
    if (built) { for (const key of [...videos.keys()]) dropVideo(key); built = ''; root.innerHTML = ''; }
    import('./details.js').then((m) => { details = m; m.render(root, S); });
    return;
  }
  const d = derive();
  buildSkeleton(d);
  const want = new Set();

  if (d.grid) renderGrid(d, want); else if (d.tablet) renderTablet(d, want); else renderMonitor(d, want);

  // nicht mehr benötigte Streams schließen (wichtig für schwache Tablets)
  for (const key of [...videos.keys()]) if (!want.has(key)) dropVideo(key);
  renderChrome(d);
}

function layerFor(id) { return root.querySelector(`[data-layer="${CSS.escape(id)}"]`); }

function renderTablet(d, want) {
  const order = d.split ? d.motions : [d.main];
  const preloadAll = d.cams.length <= 3;
  for (const c of d.cams) {
    const layer = layerFor(c.id);
    const slot = layer.querySelector('.slot');
    const visible = order.includes(c.id);
    layer.classList.toggle('show', visible);
    layer.classList.toggle('left', d.split && order[0] === c.id);
    layer.classList.toggle('right', d.split && order[1] === c.id);
    const load = !c.off && (preloadAll || visible || c.id === d.nextCam || d.motions.includes(c.id));
    if (c.off) {
      slot.innerHTML = `<div class="offnote">${c.off_reason === 'privacy' ? 'Kamera aus' : 'pausiert'}</div>`;
    } else if (load) {
      if (slot.querySelector('.offnote')) slot.innerHTML = '';
      mount(slot, c.id, 'sub');
      want.add(c.id + '|sub');
    }
  }
}

function tileOverlay(c, d, suffix = '') {
  const s = sigInfo(c);
  return `<span class="tlabel">${esc(c.label)}${suffix}</span>` +
    (d.motions.includes(c.id) ? '<span class="tdot" aria-label="Bewegung"></span>' : '') +
    (c.door === 'open' ? '<span class="tdoor">Tor offen</span>' : '') +
    (s ? `<span class="tsig sig ${s.cls}" style="position:absolute;padding:0;background:none">${esc(s.text)}</span>` : '');
}

function renderGrid(d, want) {
  // bis vier Kameras volle Qualität, darüber Substream – schont Tablet und WLAN
  const quality = d.cams.length <= 4 ? 'main' : 'sub';
  for (const c of d.cams) {
    const tile = root.querySelector(`[data-open="${CSS.escape(c.id)}"]`);
    tile.classList.toggle('alert', d.motions.includes(c.id));
    const slot = tile.querySelector('.slot');
    if (c.off) {
      slot.innerHTML = `<div class="offnote">${esc(offWord(c))}</div>`;
    } else {
      if (slot.querySelector('.offnote')) slot.innerHTML = '';
      mount(slot, c.id, quality);
      want.add(c.id + '|' + quality);
    }
    tile.querySelector('.tov').innerHTML = tileOverlay(c, d);
  }
}

function renderMonitor(d, want) {
  const big = d.main;
  root.querySelector('#big').classList.toggle('alert', d.motions.includes(big));
  for (const c of d.cams) {
    const layer = layerFor(c.id);
    const slot = layer.querySelector('.slot');
    layer.classList.toggle('show', c.id === big);
    if (c.off) {
      slot.innerHTML = `<div class="offnote">${esc(offWord(c))}</div>`;
    } else if (c.id === big || videos.has(c.id + '|main')) {
      if (slot.querySelector('.offnote')) slot.innerHTML = '';
      mount(slot, c.id, 'main');
      want.add(c.id + '|main');
    }
    const tile = root.querySelector(`[data-tile="${CSS.escape(c.id)}"]`);
    // feste Reihenfolge: Kacheln wandern bei der Rotation nicht, der Tipp trifft immer
    tile.classList.toggle('current', c.id === big);
    tile.classList.toggle('alert', d.motions.includes(c.id));
    const tslot = tile.querySelector('.slot');
    if (c.off) {
      tslot.innerHTML = `<div class="offnote" style="font-size:11px">${c.off_reason === 'privacy' ? 'aus' : 'pausiert'}</div>`;
    } else {
      if (tslot.querySelector('.offnote')) tslot.innerHTML = '';
      mount(tslot, c.id, 'sub');
      want.add(c.id + '|sub');
    }
    tile.querySelector('.tov').innerHTML = tileOverlay(c, d, c.id === big ? ' · groß' : '');
  }
  const strip = root.querySelector('#strip');
  const n = Math.max(d.cams.length, 3);
  strip.style.gridTemplateRows = `repeat(${n}, minmax(0, 1fr))`;
}

function capHtml(c, d, pos) {
  const chips = [];
  if (d.motions.includes(c.id)) chips.push('<span class="chip motion"><span class="dot"></span>Bewegung</span>');
  if (c.door === 'open') chips.push('<span class="chip door">Tor offen</span>');
  if (c.off) chips.push(`<span class="chip off">${esc(offWord(c))}</span>`);
  return `<div class="cap ${pos}"><div class="chips">${chips.join('')}</div><span class="label">${esc(c.label)}</span></div>`;
}

function renderChrome(d) {
  const main = d.byId[d.main];
  const elapsed = (now() - rotStart) / 1000;
  const pct = Math.min(100, Math.round(elapsed / S.rotate_seconds * 100));
  let html = '';
  if (d.tablet) {
    if (d.split) {
      html += capHtml(d.byId[d.motions[0]], d, 'at-left') + capHtml(d.byId[d.motions[1]], d, 'at-right');
      html += '<div class="splitnote">Bewegung bei zwei Kameras · Antippen zum Anheften</div>';
    } else {
      html += capHtml(main, d, '');
      const s = sigInfo(main);
      if (s) html += `<span class="sig ${s.cls}">${esc(s.text)}</span>`;
    }
    if (d.rotating) html += `<div class="progress"><span style="width:${pct}%"></span></div>`;
  } else if (!d.grid) {
    const s = sigInfo(main);
    root.querySelector('#bigov').innerHTML = capHtml(main, d, '') +
      (s ? `<span class="sig ${s.cls}">${esc(s.text)}</span>` : '') +
      (d.rotating ? `<div class="progress"><span style="width:${pct}%"></span></div>` : '');
  }
  const tl = [];
  if (pin) {
    const left = Math.max(0, Math.round((pin.until - now()) / 1000));
    tl.push(`<button type="button" class="pill" data-act="unpin">Angeheftet · nur hier · ${Math.floor(left / 60)}:${String(left % 60).padStart(2, '0')} · lösen</button>`);
  }
  if (d.also.length) {
    tl.push(`<div class="also" role="status"><span class="dot"></span>auch:${d.also.map((id) =>
      `<button type="button" data-pin="${esc(id)}">${esc(d.byId[id].label)}</button>`).join('')}</div>`);
  }
  if (tl.length) html += `<div class="top-left">${tl.join('')}</div>`;
  const t = new Date();
  html += `<div class="top-right"><span class="clock">${String(t.getHours()).padStart(2, '0')}:${String(t.getMinutes()).padStart(2, '0')}</span>` +
    `<button type="button" class="menu-btn" data-act="menu" aria-label="Menü" aria-expanded="${menuOpen}">` +
    '<svg width="22" height="22" viewBox="0 0 24 24" fill="currentColor" aria-hidden="true"><circle cx="5" cy="12" r="2"/><circle cx="12" cy="12" r="2"/><circle cx="19" cy="12" r="2"/></svg></button></div>';
  if (!connected) html += '<div class="conn">Verbindung wird hergestellt …</div>';
  root.querySelector('#ov').innerHTML = html;
  const menu = menuOpen ? menuHtml(d) : '';
  if (menu !== shownMenu) {
    const host = root.querySelector('#menu');
    const top = host.querySelector('.panel')?.scrollTop || 0;
    host.innerHTML = menu;
    const panel = host.querySelector('.panel');
    if (panel) panel.scrollTop = top;
    shownMenu = menu;
  }
}

function menuHtml(d) {
  const auto = !gridOn && !pin;
  const icon = (p) => `<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" aria-hidden="true">${p}</svg>`;
  const pins = d.grid ? '' : `<div class="sec"><span class="cap2">Anheften · nur hier</span><div class="chipsel">${d.cams.map((c) => {
    const on = pin && pin.cam === c.id;
    return `<button type="button" ${on ? 'data-act="unpin"' : `data-pin="${esc(c.id)}"`} aria-pressed="${!!on}">${esc(c.label)}${on ? ' ×' : ''}</button>`;
  }).join('')}</div></div>`;
  const rows = d.cams.map((c) => {
    const s = sigInfo(c);
    const door = c.door === 'open' && !c.off;
    const info = c.off ? (c.off_reason === 'privacy' ? 'Privat · aus' : 'Pausiert') : door ? 'Tor offen' : d.motions.includes(c.id) ? 'Bewegung' : S.history ? 'Aufnahme' : 'Live';
    const sig = c.wired ? 'Kabel' : (s ? esc(s.text) : '');
    return `<div class="camrow${c.off ? ' is-off' : ''}"><span class="cname"><span>${esc(c.label)}</span>` +
      `<span class="info${door || d.motions.includes(c.id) ? ' alert' : ''}">${info}${sig ? ` · <span class="sig ${s && !c.wired ? s.cls : 'good'}">${sig}</span>` : ''}</span></span>` +
      `<button type="button" class="switch" role="switch" aria-checked="${!c.off}" data-off="${esc(c.id)}" aria-label="${esc(c.label)} ${c.off ? 'einschalten' : 'ausschalten'}"><span></span></button></div>`;
  });
  const offCount = d.cams.filter((c) => c.off).length;
  return `<button type="button" class="backdrop" data-act="close" aria-label="Menü schließen"></button>
    <aside class="panel" aria-label="Menü">
      <div class="head"><h2>${esc(S.title)}</h2><button type="button" class="x" data-act="close" aria-label="Schließen">×</button></div>
      <div class="seg" role="group" aria-label="Ansicht">
        <button type="button" data-act="rotate" aria-pressed="${auto}">${icon('<path d="M20 12a8 8 0 1 1-2.3-5.6M20 4v4h-4"/>')}Rotieren</button>
        <button type="button" data-act="${gridOn ? 'rotate' : 'grid'}" aria-pressed="${gridOn}">${icon('<rect x="4" y="4" width="7" height="7" rx="1.5"/><rect x="13" y="4" width="7" height="7" rx="1.5"/><rect x="4" y="13" width="7" height="7" rx="1.5"/><rect x="13" y="13" width="7" height="7" rx="1.5"/>')}Alle gleich groß</button>
      </div>
      <div class="quick">
        <a class="qbtn" href="#overview">${icon('<path d="M4 6h16M4 12h16M4 18h10"/>')}${S.history ? 'Übersicht &amp; Verlauf' : 'Übersicht'}</a>
        <button type="button" class="qbtn" data-act="fullscreen">${icon('<path d="M4 9V4h5M20 9V4h-5M4 15v5h5M20 15v5h-5"/>')}Vollbild</button>
      </div>
      ${pins}
      <div class="sec">
        <div class="sechead"><span class="cap2">Kameras</span><span><button type="button" class="textbtn" data-act="allon">Alle an</button><button type="button" class="textbtn" data-act="alloff">Alle aus</button></span></div>
        <div class="camlist">${rows.join('')}</div>
        <span class="hint">${offCount ? offCount + ' von ' + d.cams.length + ' aus' : (S.history ? 'Alle nehmen auf und melden Bewegung' : 'Alle an · nur Livebild, keine Aufnahme')}</span>
      </div>
    </aside>`;
}

// ---------------------------------------------------------------- Aktionen
async function post(path, body) {
  try {
    await fetch(path, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) });
  } catch (e) { /* Zustand kommt ohnehin über den WebSocket */ }
}

root.addEventListener('click', (e) => {
  if (onDetails()) return;
  const el = e.target.closest('[data-act],[data-pin],[data-off],[data-tile],[data-open]');
  if (!el || !S) return;
  const pinMs = S.pin_minutes * 60000;
  if (el.dataset.open) { location.hash = '#cam/' + encodeURIComponent(el.dataset.open); return; }
  if (el.dataset.tile) pin = { cam: el.dataset.tile, until: now() + pinMs };
  else if (el.dataset.pin) { gridOn = false; local.set('grid', '0'); pin = { cam: el.dataset.pin, until: now() + pinMs }; menuOpen = false; }
  else if (el.dataset.off) {
    const c = S.cams.find((x) => x.id === el.dataset.off);
    if (c) post(`api/cams/${encodeURIComponent(c.id)}/off`, { off: !c.off });
  } else {
    const act = el.dataset.act;
    if (act === 'menu') menuOpen = true;
    else if (act === 'close') menuOpen = false;
    else if (act === 'unpin') { pin = null; rotStart = now(); }
    else if (act === 'rotate') { rotateOn = true; pin = null; gridOn = false; local.set('grid', '0'); rotStart = now(); menuOpen = false; }
    else if (act === 'grid') { gridOn = !gridOn; local.set('grid', gridOn ? '1' : '0'); menuOpen = false; }
    else if (act === 'allon') post('api/all/off', { off: false });
    else if (act === 'alloff') post('api/all/off', { off: true });
    else if (act === 'fullscreen') { document.documentElement.requestFullscreen?.(); menuOpen = false; }
  }
  render();
});
addEventListener('keydown', (e) => { if (e.key === 'Escape' && menuOpen) { menuOpen = false; render(); } });
addEventListener('resize', () => { if (!viewParam) render(); });
addEventListener('hashchange', () => { if (!onDetails() && details) { details.leave(root); built = ''; } render(); });

// ---------------------------------------------------------------- Takt + Daten
setInterval(() => {
  if (!S) return;
  if (onDetails()) { if (details) details.tick(root, S); return; }
  const d = derive();
  if (d.rotating && (now() - rotStart) / 1000 >= S.rotate_seconds) { rotIdx += 1; rotStart = now(); }
  if (!d.rotating) rotStart = now();
  render();
}, 1000);

// Live-Zustand über WebSocket (SSE kommt durch den HA-Ingress-Proxy nicht durch).
// Fällt er aus, fragt die Seite bis zur Wiederverbindung regelmäßig ab.
let ws = null;
let backoff = 1000;
function connect() {
  const u = new URL('api/live', document.baseURI);
  u.protocol = u.protocol === 'https:' ? 'wss:' : 'ws:';
  ws = new WebSocket(u.href);
  ws.onmessage = (e) => { S = JSON.parse(e.data); connected = true; backoff = 1000; render(); };
  ws.onclose = () => {
    connected = false; ws = null; render();
    setTimeout(connect, backoff);
    backoff = Math.min(backoff * 2, 30000);
  };
}
async function poll() {
  if (connected) return;
  try {
    const r = await fetch('api/state', { cache: 'no-store' });
    if (r.ok) { S = await r.json(); render(); }
  } catch (e) { /* weiter versuchen */ }
}
setInterval(poll, 3000);
poll();
connect();
