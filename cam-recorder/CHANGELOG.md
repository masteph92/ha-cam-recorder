# Changelog

## 0.3.2

- Oberfläche wird nach einem Update sofort neu geladen: `/static/` mit
  `Cache-Control: no-cache`, vorher hielt der Browser die alte `app.js`.

## 0.3.1

- Menülink „Übersicht“ in Textfarbe statt Browser-Blau.

## 0.3.0

- Übersicht (⋯ → Übersicht): alle Kameras gleich groß, darunter Zustand,
  Tor, Empfang und Speicher.
- Kamera-Detail (`#cam/<name>`): großes Livebild, Zeitleiste für
  heute/gestern/vorgestern mit Aufnahme-Abdeckung und Ereignissen,
  Ereignisliste mit Snapshot.
- Anzeige „Alle gleich groß“ (oder `?view=grid`), pro Bildschirm gemerkt.
- Wiedergabe ±10 min um ein Ereignis direkt aus den Segmenten (HLS, kein
  Transkodieren), −10/+10 s, 1×/2×/4×; Clip-Download als .mp4.
- hls.js wird ins Image gepackt (kein CDN zur Laufzeit).
- Snapshot aus dem ersten Bild des Segments (`-sseof` schrieb bei MPEG-TS
  nichts); Clip-Liste als Datei in /tmp statt über eine Pipe.

## 0.2.2

- `recording: false`: nur Livebild, keine Aufnahme. go2rtc holt einen Stream
  nur, solange jemand zuschaut – für Standorte mit Funkstrecke oder 5G, an
  denen (noch) nichts gespeichert werden soll.

## 0.2.1

- Live-Zustand über WebSocket statt Server-Sent Events: der HA-Ingress-Proxy
  hielt den Event-Stream zurück, die Startansicht blieb in der Seitenleiste
  schwarz. Fällt der WebSocket aus, fragt die Seite alle 3 s ab.
- Monitor-Leiste in fester Reihenfolge, die große Kamera ist markiert.

## 0.2.0

- Eigene Startansicht statt go2rtc-Oberfläche: Tablet (eine Kamera im
  Vollbild, rotierend, neueste Bewegung vorn, zwei gleichzeitig nebeneinander)
  und Monitor (eine groß, Rest in einer Leiste, lokal anheften).
- Live-Zustand per Server-Sent Events; Kameras an/aus (Privacy-Schalter in HA
  oder interne Pause, bleibt über Neustarts).
- LAN-Port 8580 für Wandtablets ohne HA-Login, nur mit `ui.kiosk_key`.
- Pro Kamera optional `rtsp_sub` (Substream fürs Tablet), `door_entity`,
  `signal_entity`, `label`, `wired`.
- go2rtc-API nur noch auf localhost; die Oberfläche reicht ausschließlich den
  Videostream durch.

## 0.1.3

- create_marker erkennt HA-Netzwerkspeicher: systemd-Automount (autofs) wird
  vor der Prüfung ausgelöst, gestapelte Mounts zählen mit dem letzten Eintrag.

## 0.1.2

- `storage.create_marker`: legt `.cam-recorder-root` selbst an, aber nur wenn
  `storage.primary` auf einem CIFS/NFS-Mount liegt (laut /proc/mounts).

## 0.1.1

- go2rtc-Oberfläche als Seitenleisten-Eintrag „Kameras“ (Ingress): Livebild
  aller Kameras über die bestehende go2rtc-Verbindung.
- Recorder wartet beim Start kurz auf go2rtc statt mit Fehler zu beginnen.

## 0.1.0

- Erste Version: go2rtc als einziger Kamera-Abnehmer, ffmpeg-Segmente,
  Ereignisfenster mit Deckel, Upload-Warteschlange nach B2, Aufräumen,
  tmpfs-Ausweichspeicher, Health-Sensoren.
