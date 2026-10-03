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
hinzufügen, Verwendung **Share**, Name z. B. `cam` → im Add-on
`storage.primary: /share/cam`. Den Marker legt das Add-on mit
`storage.create_marker: true` selbst an – nur wenn der Pfad wirklich auf
einem CIFS/NFS-Mount liegt. Danach kann die Option wieder aus.

Aufgeräumt wird über `primary_max_gb`: erst die ältesten ruhigen Segmente,
Ereignis-Segmente zuletzt, nichts, was noch hochgeladen werden muss.

## Upload

`upload.remote` ist ein rclone-Ziel mit Prefix, z. B. `b2:cam-clips/mg`.
Application Key in B2 auf Bucket und Prefix beschränken, **ohne
deleteFiles**. Aufbewahrung regelt die Lifecycle-Regel des Buckets.
`daily_budget_gb` begrenzt den Upload pro Kamera und Tag.

## Startansicht

Seitenleiste **Kameras** (über HA, mit HA-Login) oder für Wandtablets direkt
im LAN: `http://<HA-IP>:8580/?key=<ui.kiosk_key>` – der Schlüssel wird danach
als Cookie gemerkt. Ohne `ui.kiosk_key` ist der LAN-Port gesperrt.

- **Tablet** (schmale Bildschirme, oder `?view=tablet`): eine Kamera im
  Vollbild, rotiert alle `ui.rotate_seconds`. Bewegung holt die neueste Kamera
  nach vorn; genau zwei gleichzeitig stehen nebeneinander, ab drei steht die
  neueste groß und oben „auch: …“. Nutzt den Substream (`rtsp_sub`).
- **Monitor** (ab 1400 px Breite, oder `?view=monitor`): eine groß, alle
  anderen in einer Leiste. Antippen heftet eine Kamera für `ui.pin_minutes`
  nur auf diesem Bildschirm an.
- **⋯-Menü**: Anzeige (nur dieser Bildschirm) und Kameras an/aus (für alle).
  Hat eine Kamera `suppress` (Privacy-Schalter in HA), schaltet das Menü
  diesen; sonst pausiert das Add-on die Aufnahme selbst.
- Rot heißt Handlungsbedarf: Bewegung, Tor offen (`door_entity`), kein
  Empfang (`signal_entity` nicht verfügbar). Empfang sonst als leiser Text:
  grau gut, gelb mittel, orange schwach.

### Fire-Tablet

Fully Kiosk Browser (APK von fully-kiosk.com) → Start-URL wie oben, „Keep
Screen On“, „Autostart on Boot“. Die Seite lädt pro Kamera nur den Substream
und schließt Streams, die gerade nicht gebraucht werden.

### Verbindungen zur Kamera

go2rtc hält pro Kamera eine Verbindung zum Hauptstream (Aufnahme) und, wenn
ein Tablet schaut, eine zum Substream. Tapo-Kameras erlauben höchstens zwei –
die HA-Kamera-Entity daher auf den Restream des Add-ons umstellen statt
direkt auf die Kamera.

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
    rtsp_sub: rtsp://cam:PASSWORT@192.168.1.201:554/stream2
    label: Vorzimmer
    triggers:
      - binary_sensor.kamera_vorzimmer_motion_alarm
    suppress: switch.kamera_vorzimmer_privacy
ui:
  title: Wohnung
  kiosk_key: ein-langer-zufaelliger-schluessel
```
