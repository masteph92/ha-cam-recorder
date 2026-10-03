# Cam Recorder

Schreibt RTSP-Kameras laufend in einen Ringpuffer und lädt bei Bewegung
Clips mit Vor- und Nachlauf nach Backblaze B2. Kein NVR, kein Transkodieren:
ffmpeg kopiert nur, go2rtc hält pro Kamera genau eine Verbindung.

## Ablauf

```
Kamera ──RTSP──> go2rtc (1 Sitzung je Kamera) ──> ffmpeg -c copy ──> Segmente
                                                                     │
HA-Entity (Bewegung, Tür) ──> Ereignisfenster ──> Upload-Warteschlange ──> rclone ──> B2
HA-Entity (Privacy)       ──> Aufnahme und Auslöser aus
```

- Segmente schneiden an Keyframes. `segment_seconds` kleiner als der
  Keyframe-Abstand der Kamera (Tapo ~8 s) ergibt ein Segment pro Keyframe.
- Ereignis: Vorlauf `pre_seconds`, Nachlauf `post_seconds` nach dem letzten
  Auslöser, höchstens `max_seconds`. Danach erst wieder ein neues Ereignis,
  wenn `post_seconds` lang Ruhe war.
- Auslöser dürfen Pulse (Tapo ONVIF) oder Zustände (Torkontakt) sein.

## Speicher

`storage.primary` muss die Datei `.cam-recorder-root` enthalten. Fehlt sie
(Share nicht eingehängt), schreibt das Add-on nur in den tmpfs-Ausweich-
speicher und hält dort `fallback_keep_seconds`. So wird nie die HA-Platte
vollgeschrieben.

Netzwerk-Share in HA: Einstellungen → System → Speicher → Netzwerkspeicher
hinzufügen, Verwendung **Share**. Dann im Share den Ordner `cam-recorder`
anlegen und darin die leere Datei `.cam-recorder-root`.

Aufgeräumt wird über `primary_max_gb`: erst die ältesten ruhigen Segmente,
Ereignis-Segmente zuletzt, nichts, was noch hochgeladen werden muss.

## Upload

`upload.remote` ist ein rclone-Ziel mit Prefix, z. B. `b2:cam-clips/mg`.
Application Key in B2 auf Bucket und Prefix beschränken, **ohne
deleteFiles**. Aufbewahrung regelt die Lifecycle-Regel des Buckets.
`daily_budget_gb` begrenzt den Upload pro Kamera und Tag.

## In Home Assistant

- `sensor.cam_recorder_<kamera>`: `recording`, `suppressed`, `reconnecting`,
  `degraded` (Ausweichspeicher), Attribute mit Segmentalter, Warteschlange,
  Upload heute.
- Events `cam_recorder_event_start`, `cam_recorder_event_end`,
  `cam_recorder_snapshot` für Benachrichtigungen per Automation.

## Ton

`audio: false` ist Absicht. Ton in Wohnräumen mitzuschneiden ist rechtlich
heikler als Bild.

## Beispiel

```yaml
site: mg
cameras:
  - name: vorzimmer
    rtsp: rtsp://cam:PASSWORT@192.168.1.201:554/stream1
    triggers:
      - binary_sensor.kamera_vorzimmer_motion_alarm
    suppress: switch.kamera_vorzimmer_privacy
```
