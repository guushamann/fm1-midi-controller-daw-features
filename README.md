# FM-1 MIDI Bridge

Headless Raspberry Pi MIDI bridge between a **Novation Launchkey Mini MK3** and an **M-VAVE FM-1** running **Melodee** firmware.

## Features

- DAW-mode handshake for the Launchkey Mini MK3
- Hotplug-aware ALSA MIDI port manager
- Encoder bank tracking (Device / Mixer / Sends)
- Pad feedback for track mutes, active track, and pattern triggers
- Live web dashboard with MIDI monitor, mapping editor, and troubleshooting actions
- FastAPI + WebSocket backend
- Systemd service for auto-start

## Project Layout

```
├── bridge/                 # MIDI engine, port manager, controller drivers
├── web/                    # FastAPI app + static dashboard
├── config/                 # YAML device / controller / synth profiles
├── systemd/                # systemd unit file
├── main.py                 # Entry point
└── pyproject.toml          # uv project config
```

## Local Development

```bash
uv sync
uv run python main.py --port 8000
```

Open http://127.0.0.1:8000 for the dashboard.

## Raspberry Pi Deployment

1. Clone/push this repo to `/home/guus/fm1-midi-bridge` on the Pi.
2. Install uv on the Pi.
3. Copy the systemd unit:
   ```bash
   sudo cp systemd/fm1-midi-bridge.service /etc/systemd/system/
   sudo systemctl daemon-reload
   sudo systemctl enable --now fm1-midi-bridge
   ```
4. Open http://midibox.local:8000 on your local network.

See `plan.md` for full architecture and mapping details.
