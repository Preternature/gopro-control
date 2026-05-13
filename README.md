# LCA — Lights, Camera, Action

Web-based production controller for dual GoPro cameras, RGB LED lights, a motorised gimbal, and a camera rail — all orchestrated from a single timeline.

## Hardware

| Component | Details |
|---|---|
| Camera 1 | GoPro HERO12 Black |
| Camera 2 | GoPro HERO13 Black |
| Arduino | Mega 2560 (gimbal + rail + RGB lights) |
| Lights | RGB LED strips via common-anode PWM (pins 9/10/11 + second set) |
| Gimbal | Stepper motor on base + cam tilt axis |
| Rail | Stepper motor, forward/backward |

## Quick Start

```bash
pip install -r requirements.txt
python main.py
# Open http://localhost:5000
```

## Flash Arduino Firmware

Run from the `arduino/` folder:
```
upload_unified.bat
```
Requires `arduino-cli` on PATH. Auto-detects Mega COM port.

## Project Structure

```
LCA/
├── main.py                         # Flask + SocketIO server, all API routes
├── requirements.txt
├── lights.json                     # Saved light definitions (id, name, pins)
├── check-gopro.bat                 # Diagnose camera network connections
│
├── gopro/                          # GoPro WiFi/USB connection & control
│   ├── camera.py                   # start_video, stop_video, take_photo
│   ├── connection.py               # Auto-discover cameras over WiFi/USB
│   └── media.py                    # Media download
│
├── arduino/
│   ├── serial_controller.py        # ArduinoController: serial send/receive
│   ├── upload_unified.bat          # Compile + flash unified firmware
│   └── unified_controller/
│       └── unified_controller.ino  # Firmware: gimbal + rail + RGB lights
│
├── static/
│   ├── app.js                      # All frontend logic (timeline, lights, cameras)
│   ├── style.css
│   └── hls/                        # HLS stream segments (runtime, gitignored)
│
└── templates/
    └── index.html
```

## Arduino Serial Protocol

All commands sent at 9600 baud, newline-terminated:

| Command | Effect |
|---|---|
| `P` | Ping — responds `PONG` |
| `B<n>` | Base axis to angle n |
| `C<n>` | Cam axis to angle n |
| `BUS<n>` | Base speed (µs step delay) |
| `CUS<n>` | Cam speed |
| `S<speed>,<dur>` | Rail: move at speed for dur ms |
| `U` / `D` / `X` | Rail: up / down / stop |
| `PINS:<r>,<g>,<b>` | Set active RGB pin numbers |
| `RGB:<r>,<g>,<b>` | Set RGB PWM values (0–255) |
| `EFFECT:<X>` | LED effect: R=rainbow, B=breathe, O=off |

## Timeline Model

### Per-light timeline
Each light has its own timeline (`_tlBlocks[id]`). Blocks have absolute `start` + `duration` + `color` + `brightness` + optional `fadeIn`/`fadeOut`. Transitions (`_tlTrUI[id][i]`) apply only when two blocks are snapped together (gap < 0.12s).

### Master timeline
Combines rail, cam1, cam2, and all light tracks into a single playback. Light tracks mirror `_tlBlocks`. Duration is set globally. Supports looping.

### Playback
- Backend: `_master_playback()` in `main.py` fires each event in a daemon thread at its scheduled start time.
- Frontend: 600 ms polling detects stop/loop; RAF-based playhead animates position.

## Camera Connection

GoPros connect over WiFi (10.5.5.9) or USB (172.2x.x.51). Run `check-gopro.bat` to diagnose. BLE wake available via `gopro_ble.py`.
