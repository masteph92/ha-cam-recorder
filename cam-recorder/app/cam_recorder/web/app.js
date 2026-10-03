// Startansicht: Tablet (eine Kamera im Vollbild) oder Monitor (eine groß, Rest
// in einer Leiste). Live-Bild über go2rtc (MSE), Zustand über Server-Sent Events.
import { VideoRTC } from '../go2rtc/video-rtc.js';

class CamVideo extends VideoRTC {
  oninit() {
    super.oninit();
    this.video.controls = false;
    this.video.muted = true;
    this.video.playsInline = true;
  }
}
customElements.define('cam-video', CamVideo);

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

const esc = (s) => String(s).replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
const view = () => viewParam || (innerWidth >= 1400 ? 'monitor' : 'tablet');
const now = () => Date.now();

// ---------------------------------------------------------------- Video-Pool
const videos = new Map();
function wsUrl(stream) {
  const u = new URL('go2rtc/api/ws', document.baseURI);
  u.searchParams.set('src', stream);
  return u.href;
}
function video(cam, quality) {
  const key = cam + '|' + quality;
  let el = videos.get(key);
  if (!el) {
    el = document.createElement('cam-video');
    el.mode = 'mse';
    el.background = true;        // im Hintergrund weiterlaufen: Wechsel ohne Puffern
    el.visibilityCheck = false;
    el.src = wsUrl(quality === 'main' ? cam : cam + '_sub');
    videos.set(key, el);
  }
  return el;
}
function dropVideo(key) {
  const el = videos.get(key);
  if (!el) return;
  el.background = false;         // jetzt darf VideoRTC die Verbindung schließen
  el.remove();
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
  const split = tablet && !pin && motions.length === 2;
  const main = pin ? pin.cam : (newest || (rotateOn ? rotCam : ring[0]));
  const showAlso = tablet && !split && (motions.length >= 3 || (pin && motions.length >= 1));
  const also = showAlso ? motions.filter((id) => id !== main).reverse() : [];
  const rotating = !split && !pin && !newest && rotateOn && ring.length > 1;
  return { cams, byId, motions, newest, main, nextCam, split, also, rotating, tablet };
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
  for (const key of [...videos.keys()]) dropVideo(key);
  if (d.tablet) {
    root.innerHTML = d.cams.map((c) => `<div class="layer" data-layer="${esc(c.id)}"><div class="slot"></div></div>`).join('') +
      '<div id="ov"></div>';
  } else {
    root.innerHTML = `<div class="monitor"><div class="big" id="big">${d.cams.map((c) =>
      `<div class="layer" data-layer="${esc(c.id)}"><div class="slot"></div></div>`).join('')}<div id="bigov"></div></div>
      <div class="strip" id="strip">${d.cams.map((c) =>
      `<button type="button" class="tile" data-tile="${esc(c.id)}" aria-label="${esc(c.label)} hier anheften"><div class="slot"></div><span class="tov"></span></button>`).join('')}</div></div>
      <div id="ov"></div>`;
  }
}

// ---------------------------------------------------------------- Rendern
function render() {
  if (!S) return;
  const d = derive();
  buildSkeleton(d);
  const want = new Set();

  if (d.tablet) renderTablet(d, want); else renderMonitor(d, want);

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
    tile.hidden = c.id === big;
    tile.classList.toggle('alert', d.motions.includes(c.id));
    const tslot = tile.querySelector('.slot');
    if (c.off) {
      tslot.innerHTML = `<div class="offnote" style="font-size:11px">${c.off_reason === 'privacy' ? 'aus' : 'pausiert'}</div>`;
    } else {
      if (tslot.querySelector('.offnote')) tslot.innerHTML = '';
      mount(tslot, c.id, 'sub');
      want.add(c.id + '|sub');
    }
    const s = sigInfo(c);
    tile.querySelector('.tov').innerHTML =
      `<span class="tlabel">${esc(c.label)}</span>` +
      (d.motions.includes(c.id) ? '<span class="tdot" aria-label="Bewegung"></span>' : '') +
      (c.door === 'open' ? '<span class="tdoor">Tor offen</span>' : '') +
      (s ? `<span class="tsig sig ${s.cls}" style="position:absolute;padding:0;background:none">${esc(s.text)}</span>` : '');
  }
  const strip = root.querySelector('#strip');
  const n = Math.max(d.cams.length - 1, 3);
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
  } else {
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
  if (menuOpen) html += menuHtml(d);
  root.querySelector('#ov').innerHTML = html;
}

function menuHtml(d) {
  const showRows = [`<button type="button" class="opt" data-act="rotate" aria-pressed="${rotateOn && !pin}">Automatisch rotieren<span>${rotateOn && !pin ? '●' : ''}</span></button>`]
    .concat(d.cams.map((c) => `<button type="button" class="opt" data-pin="${esc(c.id)}" aria-pressed="${pin && pin.cam === c.id}">${esc(c.label)} anheften<span>${pin && pin.cam === c.id ? '●' : ''}</span></button>`));
  const rows = d.cams.map((c) => {
    const s = sigInfo(c);
    const info = c.off ? (c.off_reason === 'privacy' ? 'Privatsphäre – Kamera aus' : 'pausiert – keine Aufnahme')
      : (c.door === 'open' ? 'Tor offen' : c.door === 'closed' ? 'Tor zu' : 'Aufnahme');
    return `<div class="camrow"><span><div>${esc(c.label)}</div><div class="info ${c.door === 'open' && !c.off ? 'door' : ''}">${info}</div></span>` +
      `<span class="rsig sig ${s ? s.cls : ''}" style="position:static;padding:0;background:none">${c.wired ? 'Kabel' : (s ? esc(s.text) : '')}</span>` +
      `<button type="button" class="switch" role="switch" aria-checked="${!c.off}" data-off="${esc(c.id)}" aria-label="${esc(c.label)} ${c.off ? 'einschalten' : 'ausschalten'}"><span></span></button></div>`;
  });
  const offCount = d.cams.filter((c) => c.off).length;
  return `<button type="button" class="backdrop" data-act="close" aria-label="Menü schließen"></button>
    <aside class="panel" aria-label="Menü">
      <div class="head"><h2>${esc(S.title)}</h2><button type="button" class="x" data-act="close" aria-label="Schließen">×</button></div>
      <div class="sec"><span class="cap2">Anzeige · nur dieser Bildschirm</span>${showRows.join('')}</div>
      <div class="sec"><span class="cap2">Kameras · für alle</span>
        <div class="row2"><button type="button" class="opt" data-act="allon">Alle an</button><button type="button" class="opt" data-act="alloff">Alle aus</button></div>
        ${rows.join('')}
        <span class="hint">${offCount ? offCount + ' von ' + d.cams.length + ' aus' : 'Alle nehmen auf und melden Bewegung'}</span></div>
      <div class="foot"><button type="button" class="opt" data-act="fullscreen">Vollbild</button></div>
    </aside>`;
}

// ---------------------------------------------------------------- Aktionen
async function post(path, body) {
  try {
    await fetch(path, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) });
  } catch (e) { /* Zustand kommt ohnehin über SSE */ }
}

root.addEventListener('click', (e) => {
  const el = e.target.closest('[data-act],[data-pin],[data-off],[data-tile]');
  if (!el || !S) return;
  const pinMs = S.pin_minutes * 60000;
  if (el.dataset.tile) pin = { cam: el.dataset.tile, until: now() + pinMs };
  else if (el.dataset.pin) { pin = { cam: el.dataset.pin, until: now() + pinMs }; menuOpen = false; }
  else if (el.dataset.off) {
    const c = S.cams.find((x) => x.id === el.dataset.off);
    if (c) post(`api/cams/${encodeURIComponent(c.id)}/off`, { off: !c.off });
  } else {
    const act = el.dataset.act;
    if (act === 'menu') menuOpen = true;
    else if (act === 'close') menuOpen = false;
    else if (act === 'unpin') { pin = null; rotStart = now(); }
    else if (act === 'rotate') { rotateOn = true; pin = null; rotStart = now(); menuOpen = false; }
    else if (act === 'allon') post('api/all/off', { off: false });
    else if (act === 'alloff') post('api/all/off', { off: true });
    else if (act === 'fullscreen') { document.documentElement.requestFullscreen?.(); menuOpen = false; }
  }
  render();
});
addEventListener('keydown', (e) => { if (e.key === 'Escape' && menuOpen) { menuOpen = false; render(); } });
addEventListener('resize', () => { if (!viewParam) render(); });

// ---------------------------------------------------------------- Takt + Daten
setInterval(() => {
  if (!S) return;
  const d = derive();
  if (d.rotating && (now() - rotStart) / 1000 >= S.rotate_seconds) { rotIdx += 1; rotStart = now(); }
  if (!d.rotating) rotStart = now();
  render();
}, 1000);

function connect() {
  const es = new EventSource('api/events');
  es.onmessage = (e) => { S = JSON.parse(e.data); connected = true; render(); };
  es.onerror = () => { connected = false; render(); };
}
connect();
