// Übersicht (#overview), Kamera-Detail (#cam/<id>[/<tag>]) und Wiedergabe (#play/<event>).
import { createVideo, closeVideo } from './video.js';

const esc = (s) => String(s).replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
const pad = (n) => String(n).padStart(2, '0');
const hm = (t) => { const d = new Date(t * 1000); return pad(d.getHours()) + ':' + pad(d.getMinutes()); };
const hms = (t) => { const d = new Date(t * 1000); return hm(t) + ':' + pad(d.getSeconds()); };
const dur = (s) => { s = Math.max(0, Math.round(s)); return Math.floor(s / 60) + ':' + pad(s % 60); };
const gb = (b) => (b / 1024 ** 3).toFixed(b > 10 * 1024 ** 3 ? 0 : 1);

let H = null;          // geladener Verlauf
let loadedKey = '';
const live = new Map(); // Livebilder: key = cam|quality
let player = null;
let mounted = '';

export function route() {
  const [page, a, b] = location.hash.slice(1).split('/');
  if (page === 'play') return { page, id: decodeURIComponent(a || '') };
  if (page === 'cam') {
    const day = parseInt(b || '0', 10);
    return { page, cam: decodeURIComponent(a || ''), day: Number.isNaN(day) ? 0 : Math.min(Math.max(day, 0), 2) };
  }
  return { page: 'overview' };
}

function dayRange(offset) {
  const d = new Date();
  d.setHours(0, 0, 0, 0);
  d.setDate(d.getDate() - offset);
  const start = d.getTime() / 1000;
  return [start, start + 86400];
}

async function load(start, end, force = false) {
  const key = start + ':' + end;
  if (!force && key === loadedKey && H) return;
  loadedKey = key;
  try {
    const r = await fetch(`api/history?start=${start}&end=${end}`, { cache: 'no-store' });
    H = r.ok ? await r.json() : null;
  } catch (e) { H = null; }
  H = H || { events: [], coverage: {}, storage: {} };
}

function sigInfo(c) {
  if (c.lost) return { text: 'kein Empfang', cls: 'lost' };
  if (c.signal === null || c.signal === undefined) return c.wired ? { text: 'Kabel', cls: 'good' } : null;
  const d = Math.round(c.signal);
  return { text: '−' + Math.abs(d) + ' dBm', cls: d > -65 ? 'good' : d > -70 ? 'mid' : 'weak' };
}

// ---------------------------------------------------------------- Livebilder
function mountLive(box, stream, key) {
  let el = live.get(key);
  if (!el) { el = createVideo(stream); live.set(key, el); }
  if (el.parentElement !== box) box.prepend(el);
}
function pruneLive(keep) {
  for (const [k, el] of live) if (!keep.has(k)) { closeVideo(el); live.delete(k); }
}

export function leave(root) {
  pruneLive(new Set());
  stopPlayer();
  mounted = '';
  root.innerHTML = '';
}

export function tick(root, S) {
  const r = route();
  if (r.page === 'cam' && r.day === 0) {
    const [s] = dayRange(0);
    const nl = root.querySelector('.d-now');
    if (nl) nl.style.left = ((Date.now() / 1000 - s) / 864) + '%';
  } else if (r.page === 'play' && player) updatePlayer(root);
}

export async function render(root, S) {
  const r = route();
  const sig = JSON.stringify(r);
  if (sig === mounted) { refresh(root, S, r); return; }
  mounted = sig;
  stopPlayer();
  if (r.page === 'overview') return renderOverview(root, S);
  if (r.page === 'cam') return renderCam(root, S, r);
  return renderPlay(root, S, r.id);
}

function refresh(root, S, r) {
  if (r.page === 'overview') updateOverview(root, S);
  if (r.page === 'cam') updateCamLive(root, S, r.cam);
}

function head(S, back, title, extra = '') {
  return `<header class="d-head"><a class="d-back" href="${back}">‹ ${back === '#' ? 'Live' : 'Zurück'}</a><h1>${title}</h1>${extra}</header>`;
}

// ---------------------------------------------------------------- Übersicht: alle gleich groß + Infos
async function renderOverview(root, S) {
  root.innerHTML = `<div class="d-page">${head(S, '#', esc(S.title) + ' · Übersicht')}
    <section class="d-grid">${S.cams.map((c) => `<a class="d-tile" href="#cam/${encodeURIComponent(c.id)}" data-tile="${esc(c.id)}">
      <span class="d-tl2">${esc(c.label)}</span><span class="d-tinfo"></span></a>`).join('')}</section>
    <section class="d-card"><h2>Status</h2><div class="d-table" id="d-status"></div></section>
    <p class="d-muted" id="d-store"></p></div>`;
  updateOverview(root, S);
  if (S.history) {
    const [s, e] = dayRange(0);
    await load(s, e, true);
    const st = H.storage || {};
    const el = root.querySelector('#d-store');
    if (el && st.used_bytes != null) {
      el.textContent = `Speicher: ${st.store === 'primary' ? 'Netzlaufwerk' : 'Arbeitsspeicher'} ${gb(st.used_bytes)}${st.max_bytes ? ' / ' + gb(st.max_bytes) : ''} GB · Upload ${st.upload ? 'an' : 'aus'} · ${H.events.length} Ereignisse heute`;
    }
  }
}

function updateOverview(root, S) {
  const keep = new Set();
  for (const c of S.cams) {
    const t = root.querySelector(`[data-tile="${CSS.escape(c.id)}"]`);
    if (!t) continue;
    t.classList.toggle('alert', !!c.motion_since);
    t.classList.toggle('off', c.off);
    const s = sigInfo(c);
    t.querySelector('.d-tinfo').innerHTML =
      (c.motion_since ? '<span class="d-dot"></span>' : '') +
      (c.door === 'open' ? '<span class="d-door">Tor offen</span>' : '') +
      (s ? `<span class="sig ${s.cls} d-tsig">${esc(s.text)}</span>` : '');
    if (c.off) { t.querySelector('cam-video')?.remove(); continue; }
    mountLive(t, c.id + '_sub', c.id + '|sub');
    keep.add(c.id + '|sub');
  }
  pruneLive(keep);
  const rows = S.cams.map((c) => {
    const s = sigInfo(c);
    const state = c.off ? (c.off_reason === 'privacy' ? 'Privat · aus' : 'Pausiert')
      : c.motion_since ? 'Bewegung' : ({ recording: 'Aufnahme', live: 'Live', reconnecting: 'verbindet …', degraded: 'Aufnahme (RAM)', suppressed: 'aus' }[c.recording] || c.recording);
    return `<a class="d-tr" href="#cam/${encodeURIComponent(c.id)}"><span>${esc(c.label)}</span><span class="${c.motion_since ? 'd-alert' : ''}">${esc(state)}</span>` +
      `<span class="${c.door === 'open' ? 'd-alert' : 'd-muted'}">${c.door === 'open' ? 'Tor offen' : c.door === 'closed' ? 'Tor zu' : ''}</span>` +
      `<span class="sig ${s ? s.cls : ''} d-mono">${s ? esc(s.text) : ''}</span><span aria-hidden="true">›</span></a>`;
  }).join('');
  const tbl = root.querySelector('#d-status');
  if (tbl) tbl.innerHTML = rows;
}

// ---------------------------------------------------------------- Kamera-Detail
async function renderCam(root, S, r) {
  const c = S.cams.find((x) => x.id === r.cam);
  if (!c) { root.innerHTML = `<div class="d-page">${head(S, '#overview', 'Kamera nicht gefunden')}</div>`; return; }
  const [start, end] = dayRange(r.day);
  root.innerHTML = `<div class="d-page">${head(S, '#overview', esc(c.label))}
    <div class="d-big" data-big="${esc(c.id)}"><span class="d-binfo"></span></div>
    ${S.history ? '<section class="d-card" id="d-tl"><p class="d-muted">Lade Verlauf …</p></section><section id="d-evs"></section>' : '<p class="d-muted">An diesem Standort wird nicht aufgenommen – nur Livebild.</p>'}
  </div>`;
  updateCamLive(root, S, c.id);
  if (!S.history) return;
  await load(start, end, true);
  if (mounted !== JSON.stringify(route())) return;
  const now = Date.now() / 1000;
  const pct = (t) => ((Math.min(Math.max(t, start), end) - start) / 864).toFixed(3) + '%';
  const cov = (H.coverage[c.id] || []).map(([a, b]) => `<span class="d-cov" style="left:${pct(a)};width:calc(${pct(b)} - ${pct(a)})"></span>`).join('');
  const evs = H.events.filter((e) => e.cam === c.id);
  const marks = evs.map((e) => `<a class="d-mark" href="#play/${esc(e.id)}" style="left:${pct(e.start)}" aria-label="Ereignis ${hms(e.start)}"><span></span></a>`).join('');
  const days = ['Heute', 'Gestern', 'Vorgestern'].map((l, i) => `<a class="d-pill" href="#cam/${encodeURIComponent(c.id)}/${i}" aria-current="${i === r.day}">${l}</a>`).join('');
  root.querySelector('#d-tl').innerHTML = `<div class="d-row"><h2>Zeitleiste</h2><nav class="d-days">${days}</nav></div>
    <div class="d-axis"><div>${[0, 3, 6, 9, 12, 15, 18, 21, 24].map((h) => `<span>${pad(h)}</span>`).join('')}</div></div>
    <div class="d-bar d-bar-solo">${cov}${marks}${r.day === 0 ? `<span class="d-now" style="left:${pct(now)}"></span>` : ''}</div>
    <div class="d-legend"><span><i class="d-cov-i"></i>aufgenommen</span><span><i class="d-mark-i"></i>Bewegung</span></div>`;
  root.querySelector('#d-evs').innerHTML = `<div class="d-row"><h2>Ereignisse</h2><span class="d-muted">${evs.length}</span></div>` +
    (evs.length ? `<div class="d-list">${evs.map((e) => {
      const img = e.snapshot ? `<img src="api/snapshot/${esc(e.id)}.jpg" alt="" loading="lazy">` : '<span class="d-noimg">kein Bild</span>';
      return `<div class="d-ev">${img}<div class="d-evt"><span class="d-mono">${hms(e.start)}</span><span class="d-muted">${e.end ? 'Bewegung · ' + dur(e.end - e.start) : 'läuft gerade'}</span></div>` +
        `<a class="d-btn d-primary" href="#play/${esc(e.id)}">Ansehen</a><a class="d-btn" href="api/clip/${esc(e.id)}.mp4" download>Clip laden</a></div>`;
    }).join('')}</div>` : '<p class="d-muted">Keine Ereignisse an diesem Tag.</p>');
}

function updateCamLive(root, S, id) {
  const c = S.cams.find((x) => x.id === id);
  const box = root.querySelector('.d-big');
  if (!c || !box) return;
  box.classList.toggle('alert', !!c.motion_since);
  const s = sigInfo(c);
  box.querySelector('.d-binfo').innerHTML = (c.motion_since ? '<span class="chip motion"><span class="dot"></span>Bewegung</span>' : '') +
    (c.door === 'open' ? '<span class="chip door">Tor offen</span>' : '') +
    (c.off ? `<span class="chip off">${c.off_reason === 'privacy' ? 'Privat · Kamera aus' : 'Pausiert'}</span>` : '') +
    (s ? `<span class="chip sig ${s.cls}" style="position:static">${esc(s.text)}</span>` : '');
  if (c.off) { pruneLive(new Set()); return; }
  mountLive(box, c.id, c.id + '|main');
  pruneLive(new Set([c.id + '|main']));
}

// ---------------------------------------------------------------- Wiedergabe
function stopPlayer() {
  if (!player) return;
  player.hls?.destroy();
  player.video?.pause();
  player = null;
}

async function loadHls() {
  if (window.Hls) return window.Hls;
  await new Promise((ok, fail) => {
    const s = document.createElement('script');
    s.src = 'ui/hls.min.js';
    s.onload = ok; s.onerror = fail;
    document.head.appendChild(s);
  });
  return window.Hls;
}

async function renderPlay(root, S, id) {
  pruneLive(new Set());
  const now = Date.now() / 1000;
  const [ds] = dayRange(2);
  await load(ds, now + 60, true);
  const ev = H.events.find((e) => e.id === id);
  if (!ev) { root.innerHTML = `<div class="d-page">${head(S, '#overview', 'Ereignis nicht gefunden')}</div>`; return; }
  const cam = S.cams.find((c) => c.id === ev.cam) || { label: ev.cam, id: ev.cam };
  const t0 = ev.start - 600;
  const t1 = Math.min((ev.end ?? now) + 600, now);
  root.innerHTML = `<div class="d-page">${head(S, '#cam/' + encodeURIComponent(cam.id), esc(cam.label) + ` <span class="d-mono d-muted">${hms(ev.start)}</span>`)}
    <div class="d-playwrap">
      <div class="d-video"><video playsinline muted></video><span class="d-clock d-mono">–</span><span class="d-speed"></span></div>
      <aside class="d-card d-side"><h2>Ereignis</h2>
        <dl><dt>Kamera</dt><dd>${esc(cam.label)}</dd><dt>Beginn</dt><dd class="d-mono">${hms(ev.start)}</dd>
        <dt>Ende</dt><dd class="d-mono">${ev.end ? hms(ev.end) : 'läuft'}</dd><dt>Dauer</dt><dd>${dur((ev.end ?? now) - ev.start)}</dd></dl>
        ${ev.snapshot ? `<img src="api/snapshot/${esc(ev.id)}.jpg" alt="Snapshot beim Auslösen">` : ''}
        <a class="d-btn d-primary" href="api/clip/${esc(ev.id)}.mp4" download>Clip herunterladen (.mp4)</a></aside>
    </div>
    <div class="d-card"><div class="d-scrub"><span class="d-span"></span><input type="range" min="0" max="1000" value="0" aria-label="Position"></div>
      <div class="d-axis2 d-mono"><span>${hm(t0)}</span><span>${hm((t0 + t1) / 2)}</span><span>${hm(t1)}</span></div></div>
    <div class="d-ctrl"><button type="button" data-p="-10">−10 s</button><button type="button" class="d-primary" data-p="play">Abspielen</button><button type="button" data-p="10">+10 s</button>
      <span class="d-grow"></span>${[1, 2, 4].map((v) => `<button type="button" data-rate="${v}" aria-pressed="${v === 1}">${v}×</button>`).join('')}</div>
  </div>`;
  const video = root.querySelector('video');
  const src = `api/vod/${encodeURIComponent(ev.cam)}.m3u8?start=${t0}&end=${t1}`;
  player = { video, ev, t0, t1, hls: null };
  if (video.canPlayType('application/vnd.apple.mpegurl')) video.src = src;
  else {
    const Hls = await loadHls();
    player.hls = new Hls({ maxBufferLength: 30 });
    player.hls.loadSource(src);
    player.hls.attachMedia(video);
  }
  video.addEventListener('loadedmetadata', () => { video.currentTime = Math.max(0, ev.start - 30 - t0); }, { once: true });
  video.addEventListener('timeupdate', () => updatePlayer(root));
  const total = t1 - t0;
  const span = root.querySelector('.d-span');
  span.style.left = (Math.max(0, ev.start - 30 - t0) / total * 100) + '%';
  span.style.width = (((ev.end ?? now) - ev.start + 30) / total * 100) + '%';
  root.querySelector('.d-scrub input').addEventListener('input', (e) => {
    if (video.duration) video.currentTime = e.target.value / 1000 * video.duration;
  });
  root.querySelector('.d-ctrl').addEventListener('click', (e) => {
    const b = e.target.closest('button');
    if (!b) return;
    if (b.dataset.p === 'play') { video.paused ? video.play() : video.pause(); }
    else if (b.dataset.p) video.currentTime = Math.max(0, video.currentTime + Number(b.dataset.p));
    else if (b.dataset.rate) {
      video.playbackRate = Number(b.dataset.rate);
      root.querySelectorAll('[data-rate]').forEach((x) => x.setAttribute('aria-pressed', x === b));
    }
    updatePlayer(root);
  });
}

function updatePlayer(root) {
  if (!player) return;
  const { video, t0 } = player;
  const clock = root.querySelector('.d-clock');
  if (clock) clock.textContent = hms(t0 + video.currentTime);
  const sp = root.querySelector('.d-speed');
  if (sp) sp.textContent = video.playbackRate !== 1 ? video.playbackRate + '×' : '';
  const btn = root.querySelector('[data-p="play"]');
  if (btn) btn.textContent = video.paused ? 'Abspielen' : 'Pause';
  const range = root.querySelector('.d-scrub input');
  if (range && video.duration && document.activeElement !== range) range.value = Math.round(video.currentTime / video.duration * 1000);
}
