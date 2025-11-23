# GoPro Controller

Control your GoPro Hero 12 Black over WiFi with a web-based interface.

## Features

- Live preview streaming (HLS video)
- Bluetooth WiFi wake (no phone app needed!)
- Photo capture (single, timer, interval, click-to-shoot)
- Video recording (start/stop)
- Dual WiFi adapter support (stay on home internet while controlling GoPro)

## Hardware Requirements

- GoPro Hero 12 Black
- USB WiFi adapter (TP-Link TL-WN725N or similar 2.4GHz adapter)
- PC with Bluetooth (for WiFi wake feature)
- FFmpeg installed at `C:\ffmpeg\bin\`

## Setup

1. Install dependencies:
   ```bash
   pip install -r requirements.txt
   ```

2. Connect USB WiFi adapter to GoPro's network (see below)

3. Run the application:
   ```bash
   python main.py
   ```

4. Open browser to `http://localhost:5000`

## GoPro Connection Setup

### One-Time Bluetooth Pairing

This lets you wake the GoPro's WiFi without using the phone app:

1. **On GoPro**: Preferences → Connections → Connect Device → GoPro App
2. **On Windows**: Settings → Bluetooth & devices → Add device → Bluetooth
3. Select "GoPro XXXX" and pair it

### Dual WiFi Adapter Setup

This keeps you on home internet while controlling the GoPro:

1. **Primary adapter** (Intel/built-in): Stays on home WiFi (e.g., "Bitterroot")
2. **USB adapter** (TP-Link): Connects to GoPro WiFi (e.g., "GP25102353")

Configure the USB adapter to NOT auto-connect to your home network:
```cmd
netsh wlan set profileparameter name="YourHomeSSID" interface="Wi-Fi 2" connectionmode=manual
```

### GoPro WiFi Credentials

- **SSID**: GP25102353 (shown on GoPro screen)
- **Password**: XHM-r5z-rvP

### Usage Flow

1. Turn on GoPro
2. Click "Wake WiFi (Bluetooth)" button - this enables GoPro's WiFi via Bluetooth
3. App auto-connects and you're ready to go!

Or manually: Use phone app once to enable WiFi, then the web interface takes over.

## Project Structure

```
gopro-control/
├── main.py              # Application entry point
├── gopro/
│   ├── __init__.py
│   ├── connection.py    # GoPro WiFi connection & auto-discovery
│   ├── camera.py        # Camera control commands
│   └── media.py         # Media download/management
├── static/              # CSS, JS assets
├── templates/           # HTML templates
└── downloads/           # Downloaded media
```

## TODO

### Core Features
- [ ] Test full connection flow with USB WiFi adapter
- [ ] Verify photo capture works over WiFi
- [ ] Verify video start/stop works
- [ ] Test interval photo capture
- [ ] Test media browser and thumbnails
- [ ] Test file downloads

### Live Preview
- [ ] Implement actual video preview display (currently just starts stream)
- [ ] Add VLC/ffplay integration or browser-based RTSP player
- [ ] Display preview in the web UI

### UI Improvements
- [ ] Add battery level indicator
- [ ] Add storage space indicator
- [ ] Show current camera mode/settings
- [ ] Add photo countdown timer display
- [ ] Improve mobile responsiveness

### Additional Features
- [ ] Burst photo mode
- [ ] Timelapse mode controls
- [ ] HiLight tagging
- [ ] Voice control toggle
- [ ] Orientation lock
- [ ] GPS toggle
- [ ] Custom presets

### Media Management
- [ ] Batch download multiple files
- [ ] Filter by date/type
- [ ] Delete confirmation dialog
- [ ] Show file size in human-readable format
- [ ] Progress bar for downloads

### Reliability
- [ ] Better error handling and user feedback
- [ ] Auto-reconnect on connection loss
- [ ] Connection timeout adjustments
- [ ] Logging for debugging

### Future Ideas
- [ ] Scheduled recording (start/stop at specific times)
- [ ] Motion detection trigger
- [ ] Multi-camera support
- [ ] Export to cloud storage
- [ ] Keyboard shortcuts
