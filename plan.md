# Raspberry Pi 3 B+ MIDI Bridge: Launchkey Mini MK3 ↔ M-VAVE FM-1 (Melodee)

A modular, headless Python MIDI mediator running on a **Raspberry Pi 3 Model B+** that unlocks DAW mode on the **Novation Launchkey Mini MK3**, translates its controls for the **M-VAVE FM-1** (running **Melodee** firmware), and provides a local web dashboard for configuration, live MIDI monitoring, and troubleshooting.

---

## 1. System Architecture & Directory Structure

To ensure the Launchkey Mini MK3 can be swapped for any other MIDI controller later without rewriting routing logic, the codebase is split into three decoupled layers:

1. **Configuration Profiles (`config/`)**: YAML files defining USB port patterns, controller note/CC layouts, and synth target mappings.
2. **Core MIDI Engine & Driver Abstraction (`bridge/`)**: Hardware-agnostic event router paired with controller-specific drivers (e.g., handling Novation's DAW handshake and RGB LED feedback).
3. **Web Dashboard & Monitor (`web/`)**: A FastAPI + WebSocket server running alongside the MIDI engine for zero-SSH port management, live MIDI logging, and mapping edits.

```text
fm1-midi-bridge/
├── config/
│   ├── devices.yaml               # Active USB port regexes & selected profiles
│   ├── controllers/
│   │   ├── launchkey_mk3.yaml     # Launchkey DAW notes/CCs & LED color palette
│   │   └── generic_cc.yaml        # Fallback profile for standard MIDI controllers
│   └── synths/
│       └── fm1_melodee.yaml       # Track channels, CC targets, MIDI-learn slots
├── bridge/
│   ├── __init__.py
│   ├── events.py                  # Standardized internal events (PadPress, EncoderTurn, etc.)
│   ├── port_manager.py            # Hotplug ALSA/USB watcher & auto-reconnect loop
│   ├── engine.py                  # Core translator (maps internal events to synth CCs)
│   └── drivers/
│       ├── __init__.py
│       ├── base.py                # Abstract ControllerDriver base class
│       └── novation_lk.py         # Launchkey DAW handshake (9F 0C 7F) & RGB LED control
├── web/
│   ├── __init__.py
│   ├── app.py                     # FastAPI REST endpoints & WebSocket live monitor
│   └── static/
│       └── index.html             # Single-file Tailwind/Vanilla JS dashboard
├── systemd/
│   └── fm1-midi-bridge.service    # Systemd unit for boot startup & crash recovery
├── main.py                        # Entry point tying asyncio web server + MIDI threads
└── requirements.txt               # mido, python-rtmidi, fastapi, uvicorn, pyyaml, websockets
```

---

## 2. Controller Abstraction Layer (`bridge/drivers/`)

Instead of hardcoding Launchkey-specific note numbers inside the routing engine, incoming hardware messages are normalized into internal events (`bridge/events.py`):

* `NoteEvent(channel, note, velocity, is_on)`
* `PadEvent(pad_index: 0..15, velocity: 0..127, pressed: bool)`
* `EncoderEvent(bank: str, encoder_index: 0..7, value: 0..127)`
* `TransportEvent(action: "play" | "stop" | "record")`

### Driver Responsibilities
* **`drivers/base.py` (`BaseControllerDriver`)**:
  * `on_connect(daw_out_port)`: Sends device initialization messages.
  * `on_disconnect(daw_out_port)`: Sends clean exit/reset messages.
  * `parse_daw_message(msg) -> InternalEvent | None`: Translates raw controller MIDI into internal events.
  * `update_feedback(daw_out_port, state)`: Updates pad LEDs, button states, or OLED screen text.
* **`drivers/novation_lk.py` (`LaunchkeyMK3Driver`)**:
  * **Handshake**: Sends `0x9F 0x0C 0x7F` (Channel 16, Note 12, Velocity 127) to `Launchkey Mini MK3 DAW` on connection, and `0x9F 0x0C 0x00` on disconnect.
  * **Encoder Bank Tracking**: Listens on Channel 16 (`CC 9`) to detect when the user switches the 8 encoders between **Device/Plugin**, **Mixer**, and **Sends** modes.
  * **RGB Pad Feedback**: Sends Note-On messages on Channel 1 (static color) or Channel 3 (pulsing) back to the DAW port using velocity values (`0`–`127`) to indicate mute, active track, and pattern states.

> **Swapping Controllers Later**: To use a different controller (e.g., Arturia MiniLab or Akai MPK), create a new YAML file in `config/controllers/` and select it in the web UI. Only controllers requiring proprietary handshakes or LED feedback need a custom subclass in `drivers/`.

---

## 3. DAW Mode ↔ Melodee Firmware Mapping

**Melodee (v1.5–1.6)** provides 4 internal synth tracks (Prophet, CZ-1, SID, FM6) + 4 external MIDI tracks, track/master FX, motion recording, and built-in **MIDI Learn**.

| Launchkey Mini MK3 Control | Melodee Firmware Target | Behavior & LED Visual Feedback |
| :--- | :--- | :--- |
| **Keys + Pitch/Mod + Arp** (`MIDI` port) | Active Track (`Ch 1`–`Ch 4`) | Routed directly to whichever synth track is currently selected. |
| **Top Pads (Pads 1–8)** | **Track Mutes (Tracks 1–8)** | Toggles mute CC per track. LEDs: **Bright Green** (active), **Dim Red** (muted). |
| **Bottom Pads (Pads 9–12)** | **Active Track Select (Tracks 1–4)** | Sets the active keyboard MIDI channel (`Ch 1`–`Ch 4`). LEDs: **Bright Cyan** (selected), **Dim Blue** (unselected). |
| **Bottom Pads (Pads 13–16)** | **Pattern Triggers (Patterns 1–4)** | Sends Program Change `0`–`3` on the global control channel to switch patterns. LEDs: **Amber** (queued/active). |
| **Encoders — Device/Plugin Mode** | **Synth Engine Macros** (Active Track) | Sends 8 configurable CCs (e.g., `CC 74` Cutoff, `CC 71` Resonance, `CC 73` Attack, `CC 72` Decay, `CC 20`–`23` FM/Shape/LFO) mapped via Melodee's MIDI Learn. |
| **Encoders — Mixer Mode** | **Track Volumes (Tracks 1–8)** | Sends `CC 7` (Channel Volume) across MIDI Channels 1–8. |
| **Encoders — Sends Mode** | **FX Sends & Master FX** | Sends Reverb (`CC 91`) and Delay (`CC 93`) send levels per track + Master FX parameters. |
| **Play / Stop / Record Buttons** | **Transport & Motion Recording** | Sends MIDI Real-Time Start (`0xFA`), Stop (`0xFC`), and Record Arm CC for Melodee's motion/one-pass recording. |

*All CC numbers and channel assignments live in `config/synths/fm1_melodee.yaml` so they can be modified live in the web interface.*

---

## 4. Web Interface & Troubleshooting Dashboard (`web/`)

The web server runs via **FastAPI** and **Uvicorn** inside the same Python `asyncio` loop as the MIDI bridge, using thread-safe `asyncio.Queue` bridges so browser activity never introduces MIDI jitter.

### Dashboard Features (`http://midibox.local:8000`)
1. **Device Connection Matrix**:
   * Live status badges (Connected / Disconnected) for `Launchkey MIDI`, `Launchkey DAW`, and `FM-1`.
   * Dropdown menus listing all detected ALSA/USB MIDI ports (`mido.get_ioport_names()`) to manually override port matching or switch controller/synth profiles.
2. **Live Bi-Directional MIDI Monitor (WebSocket)**:
   * Real-time scrolling log showing:
     * `[IN - LK DAW]` Raw messages from the Launchkey.
     * `[OUT - FM-1]` Translated messages sent to the FM-1.
   * Filter checkboxes to hide noisy messages (such as MIDI Clock `0xF8` or Active Sensing) and a **Pause/Clear** button.
3. **One-Click Troubleshooting Actions**:
   * **Re-Send DAW Handshake**: Manually fires `9F 0C 7F` if the Launchkey exits DAW mode.
   * **MIDI Panic (All Notes Off)**: Broadcasts `CC 123` (All Notes Off) and `CC 64 = 0` (Sustain Off) across all 16 channels to clear stuck notes.
   * **LED Diagnostic Sweep**: Cycles colors across all 16 Launchkey pads to verify bi-directional USB communication.
4. **Live Mapping Editor**:
   * Form-based editor for `fm1_melodee.yaml` allowing you to change encoder CC numbers, track channels, and mute CCs, saving to disk and hot-reloading the engine immediately.

---

## 5. Raspberry Pi 3 B+ Implementation Phases

### Phase 1: Base OS & Hardware Prep
* Flash **Raspberry Pi OS Lite (64-bit)** onto a microSD card using Raspberry Pi Imager (pre-configuring Wi-Fi, SSH, and hostname `midibox`).
* Power the Pi 3 B+ with a **5V / 2.5A** micro-USB power supply so its four USB-A ports can comfortably power both the Launchkey Mini MK3 and the FM-1.
* Install dependencies:
  ```bash
  sudo apt update && sudo apt install -y python3-pip python3-rtmidi libasound2-dev avahi-daemon
  pip3 install mido fastapi uvicorn pyyaml websockets
  ```

### Phase 2: Core MIDI Engine & Hotplug Manager
* Implement `bridge/port_manager.py` with a background polling loop (every `1.5s`) that inspects `mido.get_input_names()` and `mido.get_output_names()`.
* Automatically open matching ports when plugged in, trigger `driver.on_connect()`, and gracefully close ports on USB disconnect without crashing the daemon.
* Wire up `bridge/drivers/novation_lk.py` and `bridge/engine.py`, verifying DAW mode unlocks and pad LEDs reflect track mute/select states.

### Phase 3: FastAPI Backend & Web Dashboard
* Build `web/app.py` with REST endpoints:
  * `GET /api/status` — Port states, active profiles, CPU/memory stats.
  * `POST /api/ports` — Reassign USB ports or switch YAML profiles.
  * `GET /api/mappings` & `POST /api/mappings` — Read/write `fm1_melodee.yaml` and hot-reload.
  * `POST /api/actions/handshake`, `/api/actions/panic`, `/api/actions/led-test`.
  * `WS /ws/monitor` — Streams live MIDI traffic to connected browsers.
* Build `web/static/index.html` using lightweight Vanilla JS and Tailwind CSS (bundled locally so it works completely offline without internet access).

### Phase 4: Appliance Hardening (`systemd` & Read-Only FS)
* Create `/etc/systemd/system/fm1-midi-bridge.service` with `Restart=always` and `RestartSec=2` so the bridge starts automatically on boot.
* Once configuration is finalized, enable the **Overlay File System** via `sudo raspi-config` (`Performance Options → Overlay File System`) so you can safely switch off power to the Pi 3 B+ anytime without risking SD card corruption.