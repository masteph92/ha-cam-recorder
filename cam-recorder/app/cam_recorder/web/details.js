// Detailansicht: Zeitleiste pro Tag, Ereignisliste, Wiedergabe aus den Segmenten.
// Route: #details[/<tag-offset>]  und  #play/<event-id>
import { createVideo, closeVideo } from './video.js';

const esc = (s) => String(s).replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
const pad = (n) => String(n).padStart(2, '0');
const hm = (t) => { const d = new Date(t * 1000); return pad(d.getHours()) + ':' + pad(d.getMinutes()); };
const hms = (t) => { const d = new Date(t * 1000); return hm(t) + ':' + pad(d.getSeconds()); };
const dur = (s) => { s = Math.max(0, Math.round(s)); return Math.floor(s / 60) + ':' + pad(s % 60); };
const gb = (b) => (b / 1024 ** 3).toFixed(b > 10 * 1024 ** 3 ? 0 : 1);

let H = null;          // geladener Verlauf
let loadedKey = '';
let live = new Map();  // kleine Livebilder oben
let player = null;     // { hls, video, ev, t0, t1 }
let mounted = '';

function route() {
  const h = location.hash;
  if (h.startsWith('#play/')) return { page: 'play', id: decodeURIComponent(h.slice(6)) };
  const off = parseInt(h.split('/')[1] || '0', 10);
  return { page: 'list', day: Number.isNaN(off) ? 0 : Math.min(Math.max(off, 0), 2) };
}

function dayRange(offset) {
  const d = new Date();
  d.setHours(0, 0, 0, 0);
  d.setDate(d.getDate() - offset);
  const start = d.getTime() / 1000;
  return [start, start + 86400];
}

async function load(start, end) {
  const key = start + ':' + end;
  if (key === loadedKey && H) return;
  loadedKey = key;
  const r = await fetch(`api/history?start=${start}&end=${end}`, { cache: 'no-store' });
  H = r.ok ? await r.json() : { events: [], coverage: {}, storage: {} };
}

export function leave(root) {
  for (const el of live.values()) closeVideo(el);
  live.clear();
  stopPlayer();
  mounted = '';
  root.innerHTML = '';
}

export function tick(root, S) {
  const r = route();
  if (r.page === 'list') {
    const now = Date.now() / 1000;
    const [s, e] = dayRange(r.day);
    const nl = root.querySelector('.d-now');
    if (nl && now < e) nl.style.left = ((now - s) / 864) + '%';
  } else if (player) {
    updatePlayer(root);
  }
}

export async function render(root, S) {
  const r = route();
  const sig = r.page + ':' + (r.page === 'play' ? r.id : r.day);
  if (sig === mounted) { updateLive(root, S); return; }
  mounted = sig;
  stopPlayer();
  if (r.page === 'play') return renderPlay(root, S, r.id);
  const [start, end] = dayRange(r.day);
  root.innerHTML = '<div class="d-page"><p class="d-muted">Lade Verlauf …</p></div>';
  await load(start, end);
  if (mounted !== sig) return;
  renderList(root, S, r.day, start, end);
}

// ---------------------------------------------------------------- Liste
function renderList(root, S, day, start, end) {
  const cams = S.cams;
  const st = H.storage || {};
  const recCount = cams.filter((c) => !c.off).length;
  const now = Date.now() / 1000;
  const pct = (t) => ((Math.min(Math.max(t, start), end) - start) / 864).toFixed(3) + '%';
  const days = ['Heute', 'Gestern', 'Vorgestern'].map((label, i) =>
    `<a class="d-pill" href="#details/${i}" aria-current="${i === day}">${label}</a>`).join('');
  const tracks = cams.map((c) => {
    const cov = (H.coverage[c.id] || []).map(([a, b]) =>
      `<span class="d-cov" style="left:${pct(a)};width:calc(${pct(b)} - ${pct(a)})"></span>`).join('');
    const evs = H.events.filter((e) => e.cam === c.id).map((e) =>
      `<a class="d-mark" href="#play/${esc(e.id)}" style="left:${pct(e.start)}" aria-label="Ereignis ${hms(e.start)}"><span></span></a>`).join('');
    return `<div class="d-track"><span class="d-tl">${esc(c.label)}</span><div class="d-bar">${cov}${evs}${day === 0 ? `<span class="d-now" style="left:${pct(now)}"></span>` : ''}</div></div>`;
  }).join('');
  const label = Object.fromEntries(cams.map((c) => [c.id, c.label]));
  const events = H.events.length ? H.events.map((e) => {
    const len = (e.end ?? now) - e.start;
    const img = e.snapshot ? `<img src="api/snapshot/${esc(e.id)}.jpg" alt="" loading="lazy">` : '<span class="d-noimg">kein Bild</span>';
    return `<div class="d-ev">${img}<div class="d-evt"><b>${esc(label[e.cam] || e.cam)}</b> <span class="d-mono">${hms(e.start)}</span>` +
      `<span class="d-muted">${e.end ? 'Bewegung · ' + dur(len) : 'läuft gerade'}</span></div>` +
      `<a class="d-btn d-primary" href="#play/${esc(e.id)}">Ansehen</a><a class="d-btn" href="api/clip/${esc(e.id)}.mp4" download>Clip laden</a></div>`;
  }).join('') : '<p class="d-muted">Keine Ereignisse an diesem Tag.</p>';
  const used = st.used_bytes != null ? `${gb(st.used_bytes)}${st.max_bytes ? ' / ' + gb(st.max_bytes) : ''} GB` : '';
  root.innerHTML = `<div class="d-page">
    <header class="d-head"><a class="d-back" href="#">‹ Live</a><h1>${esc(S.title)} · Verlauf</h1>
      <span class="d-chip">${recCount} / ${cams.length} nehmen auf</span>
      ${used ? `<span class="d-chip d-mono">${st.store === 'primary' ? 'DS' : 'RAM'} ${used}</span>` : ''}
      <span class="d-chip">${st.upload ? 'Upload an' : 'Upload aus'}</span></header>
    <section class="d-live">${cams.map((c) => `<div class="d-lt" data-live="${esc(c.id)}"><span class="d-ltl">${esc(c.label)}</span></div>`).join('')}</section>
    <section class="d-card"><div class="d-row"><h2>Zeitleiste</h2><nav class="d-days">${days}</nav></div>
      <div class="d-scroll"><div class="d-tracks"><div class="d-track d-axis"><span></span><div>${[0, 3, 6, 9, 12, 15, 18, 21, 24].map((h) => `<span>${pad(h)}</span>`).join('')}</div></div>${tracks}
      <div class="d-legend"><span><i class="d-cov-i"></i>aufgenommen</span><span><i class="d-mark-i"></i>Bewegung</span>${day === 0 ? '<span><i class="d-now-i"></i>jetzt</span>' : ''}</div></div></div></section>
    <section><div class="d-row"><h2>Ereignisse</h2><span class="d-muted">${H.events.length}</span></div><div class="d-list">${events}</div></section>
  </div>`;
  updateLive(root, S);
}

function updateLive(root, S) {
  for (const c of S.cams) {
    const box = root.querySelector(`[data-live="${CSS.escape(c.id)}"]`);
    if (!box) continue;
    box.classList.toggle('alert', !!c.motion_since);
    if (c.off) {
      if (live.has(c.id)) { closeVideo(live.get(c.id)); live.delete(c.id); }
      if (!box.querySelector('.d-off')) box.insertAdjacentHTML('afterbegin', '<span class="d-off">aus</span>');
      continue;
    }
    box.querySelector('.d-off')?.remove();
    if (!live.has(c.id)) live.set(c.id, createVideo(c.id + '_sub'));
    const el = live.get(c.id);
    if (el.parentElement !== box) box.prepend(el);
  }
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
    s.src = 'static/hls.min.js';
    s.onload = ok; s.onerror = fail;
    document.head.appendChild(s);
  });
  return window.Hls;
}

async function renderPlay(root, S, id) {
  for (const el of live.values()) closeVideo(el);
  live.clear();
  const now = Date.now() / 1000;
  const [ds] = dayRange(2);
  await load(ds, now + 60);
  const ev = H.events.find((e) => e.id === id);
  if (!ev) { root.innerHTML = '<div class="d-page"><a class="d-back" href="#details">‹ Verlauf</a><p class="d-muted">Ereignis nicht gefunden.</p></div>'; return; }
  const cam = S.cams.find((c) => c.id === ev.cam) || { label: ev.cam };
  const t0 = ev.start - 600;
  const t1 = Math.min((ev.end ?? now) + 600, now);
  root.innerHTML = `<div class="d-page">
    <header class="d-head"><a class="d-back" href="#details">‹ Verlauf</a><h1>${esc(cam.label)} <span class="d-mono d-muted">${hms(ev.start)}</span></h1></header>
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
  if (video.canPlayType('application/vnd.apple.mpegurl')) {
    video.src = src;
  } else {
    const Hls = await loadHls();
    player.hls = new Hls({ maxBufferLength: 30 });
    player.hls.loadSource(src);
    player.hls.attachMedia(video);
  }
  video.addEventListener('loadedmetadata', () => {
    // Start 30 s vor dem Ereignis (die Playlist beginnt 10 min davor)
    video.currentTime = Math.max(0, ev.start - 30 - t0);
  }, { once: true });
  const span = root.querySelector('.d-span');
  const total = t1 - t0;
  span.style.left = ((ev.start - 30 - t0) / total * 100) + '%';
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
  const { video, t0, t1 } = player;
  const clock = root.querySelector('.d-clock');
  if (clock) clock.textContent = hms(t0 + video.currentTime);
  const sp = root.querySelector('.d-speed');
  if (sp) sp.textContent = video.playbackRate !== 1 ? video.playbackRate + '×' : '';
  const btn = root.querySelector('[data-p="play"]');
  if (btn) btn.textContent = video.paused ? 'Abspielen' : 'Pause';
  const range = root.querySelector('.d-scrub input');
  if (range && video.duration && document.activeElement !== range) range.value = Math.round(video.currentTime / video.duration * 1000);
}
