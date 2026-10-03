# ha-cam-recorder

Home-Assistant-Add-on: Ringpuffer-Aufnahme für RTSP-Kameras, Ereignis-Clips
mit Vor- und Nachlauf nach Backblaze B2. Details in
[cam-recorder/DOCS.md](cam-recorder/DOCS.md).

## Installation

Einstellungen → Add-ons → Add-on-Store → ⋮ → Repositories →
`https://github.com/masteph92/ha-cam-recorder`

## Entwicklung

```sh
uv run --no-project --with pytest --with aiohttp python -m pytest
```

Die Logik (Ereignisfenster, Aufräumen, Warteschlange, Speicherwahl) ist ohne
Home Assistant, ffmpeg und rclone testbar.
