# GoPro Controller

Control your GoPro Hero 12 Black over WiFi with a web-based interface.

## Features

- Live preview streaming
- Photo capture (single, timer, interval)
- Video recording (start/stop)
- Media browser and replay
- Camera settings control

## Hardware Requirements

- GoPro Hero 12 Black
- USB WiFi adapter (for controlling GoPro while on Ethernet)
- USB-C cable (optional, for charging while shooting)

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

## GoPro WiFi Connection

Since you want to stay on Ethernet for internet while controlling the GoPro:

1. **On GoPro**: Swipe down → Preferences → Wireless Connections → Connect Device → GoPro Quik App
2. Note the WiFi name and password displayed
3. **On PC**: Connect your USB WiFi adapter to the GoPro's network
4. Windows will route: Ethernet → Internet, WiFi → GoPro (10.5.5.9)

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
