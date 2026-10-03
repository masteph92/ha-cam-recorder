# Changelog

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
