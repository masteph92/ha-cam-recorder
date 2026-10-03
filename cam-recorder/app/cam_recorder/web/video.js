// Gemeinsamer Live-Player für Start- und Detailansicht (go2rtc, MSE).
import { VideoRTC } from '../go2rtc/video-rtc.js';

class CamVideo extends VideoRTC {
  oninit() {
    super.oninit();
    this.video.controls = false;
    this.video.muted = true;
    this.video.playsInline = true;
  }
}
if (!customElements.get('cam-video')) customElements.define('cam-video', CamVideo);

export function wsUrl(stream) {
  const u = new URL('go2rtc/api/ws', document.baseURI);
  u.searchParams.set('src', stream);
  return u.href;
}

// background=true: läuft weiter, auch wenn gerade unsichtbar – Wechsel ohne Puffern
export function createVideo(stream, background = true) {
  const el = document.createElement('cam-video');
  el.mode = 'mse';
  el.background = background;
  el.visibilityCheck = false;
  el.src = wsUrl(stream);
  return el;
}

export function closeVideo(el) {
  el.background = false; // jetzt darf VideoRTC die Verbindung schließen
  el.remove();
}
