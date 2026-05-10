"""
GoPro Controller - Main Application
Web-based interface for controlling one or two GoPro cameras.
"""

import os
import json
import time
import math
import threading
import serial
import serial.tools.list_ports
from flask import Flask, render_template, jsonify, request, send_from_directory, Response
from flask_socketio import SocketIO
from gopro import GoProConnection, GoProCamera, GoProMedia
from arduino import ArduinoController
from tracker import PersonTracker

app = Flask(__name__)
app.config['SECRET_KEY'] = 'gopro-controller-secret'
socketio = SocketIO(app, cors_allowed_origins="*", async_mode='threading')

# ---------------------------------------------------------------------------
# Camera setup
#
# Camera 1: first GoPro (WiFi SSID GP25102353, stream UDP 8554)
#   - Uses WiFi at 10.5.5.9 by default (dual-adapter setup)
#   - Will fall back to USB auto-discovery
#
# Camera 2: second GoPro (update SSID/IP below once you know them)
#   - Recommended: connect via USB-C so both cameras work simultaneously
#   - USB auto-discover will find it; OR set its IP explicitly below.
#   - Stream on UDP port 8555 to avoid conflict with cam1.
#
# To find your second camera's SSID: turn it on and look at the screen
# (Preferences → Connections → Camera Info) or check via Wi-Fi scan.
# ---------------------------------------------------------------------------

connection1 = GoProConnection(
    name="cam1",
    stream_udp_port=8554,
    gopro_ssid="GP25102353",    # First camera's WiFi SSID
    # gopro_ip left as None → auto-discovers (WiFi 10.5.5.9 or USB)
)

connection2 = GoProConnection(
    name="cam2",
    stream_udp_port=8555,
    gopro_ssid="Thirteen",      # HERO13 Black — WiFi SSID
    ble_name="GoPro 4477",      # HERO13 BLE advertisement name
)

camera1 = GoProCamera(connection1)
camera2 = GoProCamera(connection2)
media1 = GoProMedia(connection1, download_dir="downloads")
media2 = GoProMedia(connection2, download_dir="downloads")

# Registry: cam_id (1 or 2) → components
cameras = {
    1: {"conn": connection1, "cam": camera1, "media": media1},
    2: {"conn": connection2, "cam": camera2, "media": media2},
}

def get_cam(cam_id: int):
    """Return camera dict or None"""
    return cameras.get(cam_id)

# Arduino controller (Camera Rail + Gimbal)
arduino = ArduinoController()

# Person tracker (OpenCV HOG + gimbal auto-centering)
tracker = PersonTracker(arduino)

# ── RGB Arduino (dedicated serial for lights) ────────────────────────────────
# Separate Arduino running rgb_gui_controller.ino / unified_controller.ino
# Protocol: PINS:rPin,gPin,bPin  then  RGB:r,g,b  |  EFFECT:RAINBOW/FADE/STOP

_rgb_ser: serial.Serial = None
_rgb_port_name: str = None
_rgb_lock = threading.Lock()

def _rgb_connected() -> bool:
    return _rgb_ser is not None and _rgb_ser.is_open

def _rgb_send(cmd: str) -> bool:
    global _rgb_ser
    with _rgb_lock:
        if _rgb_connected():
            try:
                _rgb_ser.write((cmd + '\n').encode())
                _rgb_ser.flush()
                return True
            except Exception as e:
                print(f"[rgb] Send error: {e}")
    # Fallback: route through the main gimbal/rail Arduino when no separate connection
    return arduino.send(cmd)

def _rgb_connect(port: str) -> bool:
    global _rgb_ser, _rgb_port_name
    with _rgb_lock:
        try:
            if _rgb_ser and _rgb_ser.is_open:
                _rgb_ser.close()
            s = serial.Serial(port, 9600, timeout=1)
            time.sleep(2)
            s.flushInput()
            s.write(b"RGB:0,0,0\n")
            s.flush()
            time.sleep(0.4)
            confirmed = False
            if s.in_waiting:
                resp = s.readline().decode(errors='ignore').strip()
                confirmed = 'OK:RGB' in resp
            _rgb_ser = s
            _rgb_port_name = port
            print(f"[rgb] Connected on {port}" + (" (confirmed)" if confirmed else " (assumed)"))
            return True
        except Exception as e:
            print(f"[rgb] Connect error: {e}")
            return False

def _rgb_disconnect():
    global _rgb_ser, _rgb_port_name
    with _rgb_lock:
        if _rgb_ser and _rgb_ser.is_open:
            _rgb_ser.close()
        _rgb_ser = None
        _rgb_port_name = None

def _rgb_list_ports() -> list:
    return [p.device for p in serial.tools.list_ports.comports()]

# ── Light config ─────────────────────────────────────────────────────────────

_LIGHTS_CONFIG = os.path.join(os.path.dirname(__file__), 'lights.json')

def _load_lights() -> dict:
    try:
        with open(_LIGHTS_CONFIG) as f:
            items = json.load(f)
        return {int(it['id']): dict(it) for it in items}
    except (FileNotFoundError, json.JSONDecodeError, KeyError):
        return {
            1: {"id": 1, "name": "Light 1", "pin_r": 9,  "pin_g": 10, "pin_b": 11,
                "r": 255, "g": 255, "b": 255, "brightness": 100, "effect": None},
            2: {"id": 2, "name": "Light 2", "pin_r": 12, "pin_g": 13, "pin_b": 14,
                "r": 255, "g": 255, "b": 255, "brightness": 100, "effect": None},
        }

def _save_lights():
    with open(_LIGHTS_CONFIG, 'w') as f:
        json.dump(list(lights.values()), f, indent=2)

lights: dict = _load_lights()

def _next_light_id() -> int:
    return max(lights.keys(), default=0) + 1

def _light_send_color(ls: dict) -> bool:
    _rgb_send(f"PINS:{ls['pin_r']},{ls['pin_g']},{ls['pin_b']}")
    bri = ls['brightness'] / 100
    r = int(ls['r'] * bri)
    g = int(ls['g'] * bri)
    b = int(ls['b'] * bri)
    return _rgb_send(f"RGB:{r},{g},{b}")

# ── Timeline state (block-based) ───────────────────────────────────────────────
# Per-light: {"blocks": [...], "transitions": [...], "playing": bool, "_stop": bool, "loop": bool}
# blocks: [{color, brightness (0-100), duration (s)}]
# transitions: [{color_mode, brightness_mode, duration}]  — length == len(blocks)-1
_timelines: dict = {}

def _tl(light_id: int) -> dict:
    if light_id not in _timelines:
        _timelines[light_id] = {"blocks": [], "transitions": [], "playing": False, "_stop": False, "loop": False}
    return _timelines[light_id]

def _hex_to_rgb(hex_color: str):
    hex_color = hex_color.lstrip('#')
    return (int(hex_color[0:2], 16), int(hex_color[2:4], 16), int(hex_color[4:6], 16))

def _perform_transition(ls: dict, from_block: dict, to_block: dict, tr: dict, tl: dict):
    color_mode = tr.get('color_mode', 'instant_start')
    bri_mode   = tr.get('brightness_mode', 'instant_start')
    duration   = float(tr.get('duration', 1.0))

    # Both instant — nothing to do
    if color_mode == 'instant_start' and bri_mode == 'instant_start':
        return

    # Both instant_end — just wait
    if color_mode == 'instant_end' and bri_mode == 'instant_end':
        end = time.time() + duration
        while time.time() < end:
            if tl['_stop']: return
            time.sleep(0.05)
        return

    steps = max(1, int(duration * 20))
    step_time = duration / steps

    from_r, from_g, from_b = _hex_to_rgb(from_block['color'])
    to_r, to_g, to_b       = _hex_to_rgb(to_block['color'])
    from_bri = float(from_block['brightness'])
    to_bri   = float(to_block['brightness'])

    cur_r, cur_g, cur_b = (to_r, to_g, to_b) if color_mode == 'instant_start' else (from_r, from_g, from_b)
    cur_bri = to_bri if bri_mode == 'instant_start' else from_bri

    # Apply instant_start immediately
    if color_mode == 'instant_start' or bri_mode == 'instant_start':
        scale = cur_bri / 100.0
        _rgb_send(f"RGB:{int(cur_r*scale)},{int(cur_g*scale)},{int(cur_b*scale)}")

    for step in range(steps + 1):
        if tl['_stop']: return
        t = step / steps
        factor = (1 - math.cos(t * math.pi)) / 2

        if color_mode == 'gradual':
            cur_r = int(from_r + (to_r - from_r) * factor)
            cur_g = int(from_g + (to_g - from_g) * factor)
            cur_b = int(from_b + (to_b - from_b) * factor)
        elif color_mode == 'instant_end' and step == steps:
            cur_r, cur_g, cur_b = to_r, to_g, to_b

        if bri_mode == 'gradual':
            cur_bri = from_bri + (to_bri - from_bri) * factor
        elif bri_mode == 'instant_end' and step == steps:
            cur_bri = to_bri

        scale = cur_bri / 100.0
        _rgb_send(f"RGB:{int(cur_r*scale)},{int(cur_g*scale)},{int(cur_b*scale)}")
        time.sleep(step_time)

# ── Master Timeline ────────────────────────────────────────────────────────────
# tracks.rail:  [{start, duration, direction ("forward"|"backward"), speed}]
# tracks.cam1/cam2: [{start, duration, action ("photo"|"video_start"|"video_stop")}]
# light_tracks: {str(light_id): [{start, duration, color, brightness, transition:{mode,duration}}]}

_master_tl: dict = {
    "tracks":       {"rail": [], "cam1": [], "cam2": []},
    "light_tracks": {},
    "duration":     60.0,
    "loop":         False,
    "playing":      False,
    "_stop":        False,
}

def _tl_tr_to_server(mode: str, dur: float) -> dict:
    if mode == "fade":     return {"color_mode": "gradual",       "brightness_mode": "gradual",       "duration": dur}
    if mode == "dissolve": return {"color_mode": "gradual",       "brightness_mode": "instant_start", "duration": dur}
    return                        {"color_mode": "instant_start", "brightness_mode": "instant_start", "duration": 0.0}

def _compile_master_events() -> list:
    evs = []
    for blk in _master_tl["tracks"].get("rail", []):
        evs.append({"time": blk["start"], "type": "rail", "block": blk})
    for track_key, cam_id in [("cam1", 1), ("cam2", 2)]:
        for blk in _master_tl["tracks"].get(track_key, []):
            evs.append({"time": blk["start"], "type": "camera", "cam_id": cam_id, "block": blk})
    for lid_str, blocks in _master_tl["light_tracks"].items():
        lid = int(lid_str)
        ls  = lights.get(lid)
        if not ls: continue
        for i, blk in enumerate(blocks):
            evs.append({"time": blk["start"], "type": "light", "light_id": lid, "ls": ls,
                        "block": blk, "next_block": blocks[i+1] if i+1 < len(blocks) else None})
    evs.sort(key=lambda e: e["time"])
    return evs

def _fire_master_light(ev: dict):
    ls, block, next_block = ev["ls"], ev["block"], ev.get("next_block")
    r, g, b = _hex_to_rgb(block["color"])
    scale   = block["brightness"] / 100.0
    _rgb_send(f"PINS:{ls['pin_r']},{ls['pin_g']},{ls['pin_b']}")
    time.sleep(0.05)
    _rgb_send(f"RGB:{int(r*scale)},{int(g*scale)},{int(b*scale)}")
    if next_block:
        tr_ui  = block.get("transition", {"mode": "cut", "duration": 1.0})
        tr     = _tl_tr_to_server(tr_ui.get("mode", "cut"), float(tr_ui.get("duration", 1.0)))
        tr_dur = tr["duration"] if tr["color_mode"] == "gradual" or tr["brightness_mode"] == "gradual" else 0.0
        wait   = max(0.0, block["duration"] - tr_dur)
        if wait > 0: time.sleep(wait)
        if not _master_tl["_stop"]:
            _perform_transition(ls,
                {"color": block["color"],      "brightness": block["brightness"]},
                {"color": next_block["color"], "brightness": next_block["brightness"]},
                tr, _master_tl)

def _master_playback():
    _master_tl["playing"] = True
    _master_tl["_stop"]   = False
    while True:
        t0     = time.time()
        events = _compile_master_events()
        fired  = [False] * len(events)
        while not _master_tl["_stop"]:
            now = time.time() - t0
            for i, ev in enumerate(events):
                if not fired[i] and now >= ev["time"]:
                    fired[i] = True
                    etype = ev["type"]
                    if etype == "rail":
                        blk = ev["block"]
                        arduino.send(f"S{int(blk.get('speed',91))},{int(blk['duration']*1000)}")
                        time.sleep(0.05)
                        arduino.send("U" if blk.get("direction","forward")=="forward" else "D")
                    elif etype == "camera":
                        pass  # camera actions wired up next session
                    elif etype == "light":
                        threading.Thread(target=_fire_master_light, args=(ev,), daemon=True).start()
            if all(fired) and (time.time() - t0) >= _master_tl["duration"]:
                break
            time.sleep(0.02)
        if _master_tl["_stop"] or not _master_tl["loop"]: break
    # Turn off lights at end
    for lid_str, blocks in _master_tl["light_tracks"].items():
        ls = lights.get(int(lid_str))
        if ls:
            _rgb_send(f"PINS:{ls['pin_r']},{ls['pin_g']},{ls['pin_b']}")
            _rgb_send("RGB:0,0,0")
    arduino.send("X")
    _master_tl["playing"] = False

@app.route('/api/master-timeline', methods=['GET'])
def master_tl_get():
    return jsonify({k: _master_tl[k] for k in ("tracks","light_tracks","duration","loop","playing")})

@app.route('/api/master-timeline', methods=['POST'])
def master_tl_set():
    data = request.json or {}
    if "tracks"       in data: _master_tl["tracks"]       = data["tracks"]
    if "light_tracks" in data: _master_tl["light_tracks"] = data["light_tracks"]
    if "duration"     in data: _master_tl["duration"]     = float(data["duration"])
    if "loop"         in data: _master_tl["loop"]         = bool(data["loop"])
    return jsonify({"success": True})

@app.route('/api/master-timeline/play', methods=['POST'])
def master_tl_play():
    if _master_tl["playing"]:
        _master_tl["_stop"] = True
        time.sleep(0.15)
    threading.Thread(target=_master_playback, daemon=True).start()
    return jsonify({"success": True})

@app.route('/api/master-timeline/stop', methods=['POST'])
def master_tl_stop():
    _master_tl["_stop"]   = True
    _master_tl["playing"] = False
    arduino.send("X")
    return jsonify({"success": True})

# ── Arduino Routes ──────────────────────────────────────────────────────────

@app.route('/api/arduino/status')
def arduino_status():
    return jsonify(arduino.get_status())

@app.route('/api/arduino/connect', methods=['POST'])
def arduino_connect():
    ok = arduino.connect()
    return jsonify({"success": ok, **arduino.get_status()})

# Rail
@app.route('/api/arduino/rail/settings', methods=['POST'])
def arduino_rail_settings():
    data = request.json or {}
    if 'speed' in data:
        arduino.rail_set_speed(int(data['speed']))
    if 'duration' in data:
        arduino.rail_set_duration(int(data['duration']))
    return jsonify({"success": True, **arduino.get_status()})

@app.route('/api/arduino/rail/away', methods=['POST'])
def arduino_rail_away():
    return jsonify({"success": arduino.rail_away()})

@app.route('/api/arduino/rail/toward', methods=['POST'])
def arduino_rail_toward():
    return jsonify({"success": arduino.rail_toward()})

@app.route('/api/arduino/rail/stop', methods=['POST'])
def arduino_rail_stop():
    return jsonify({"success": arduino.rail_stop()})

# Gimbal
@app.route('/api/arduino/gimbal/base', methods=['POST'])
def arduino_gimbal_base():
    data = request.json or {}
    if 'us' in data:
        return jsonify({"success": arduino.gimbal_base_us(int(data['us']))})
    return jsonify({"success": arduino.gimbal_base_angle(int(data.get('angle', 90)))})

@app.route('/api/arduino/gimbal/cam', methods=['POST'])
def arduino_gimbal_cam():
    data = request.json or {}
    if 'us' in data:
        return jsonify({"success": arduino.gimbal_cam_us(int(data['us']))})
    return jsonify({"success": arduino.gimbal_cam_angle(int(data.get('angle', 90)))})

@app.route('/api/arduino/gimbal/center', methods=['POST'])
def arduino_gimbal_center():
    return jsonify({"success": arduino.gimbal_center()})

@app.route('/api/arduino/gimbal/sweep', methods=['POST'])
def arduino_gimbal_sweep():
    target = (request.json or {}).get('target', 'both')
    if target == 'base':
        ok = arduino.gimbal_sweep_base()
    elif target == 'cam':
        ok = arduino.gimbal_sweep_cam()
    else:
        ok = arduino.gimbal_sweep_both()
    return jsonify({"success": ok})

# ── Tracker Routes ──────────────────────────────────────────────────────────

@app.route('/api/tracker/status')
def tracker_status():
    return jsonify(tracker.get_status())

@app.route('/api/tracker/<int:cam_id>/start', methods=['POST'])
def tracker_start(cam_id):
    c = get_cam(cam_id)
    if not c:
        return jsonify({"error": f"Camera {cam_id} not found"}), 404
    if not c["conn"].stream_active:
        return jsonify({"error": "Start the camera preview first"}), 400
    flip = bool((request.json or {}).get('flip', False))
    ok = tracker.start(cam_id, c["conn"], flip=flip)
    return jsonify({"success": ok, **tracker.get_status()})

@app.route('/api/tracker/stop', methods=['POST'])
def tracker_stop():
    tracker.stop()
    return jsonify({"success": True})

@app.route('/api/tracker/limits', methods=['POST'])
def tracker_limits():
    data = request.json or {}
    tracker.set_limits(
        pan_min  = data.get('pan_min',  45),
        pan_max  = data.get('pan_max',  144),
        tilt_min = data.get('tilt_min', 1300),
        tilt_max = data.get('tilt_max', 2350),
    )
    return jsonify({"success": True})

# === Web Routes ===

@app.route('/')
def index():
    return render_template('index.html')

# ---------------------------------------------------------------------------
# Discovery
# ---------------------------------------------------------------------------

@app.route('/api/scan')
def scan_cameras():
    """Scan for all connected GoPros (USB and WiFi)"""
    found = GoProConnection.auto_detect_all()
    return jsonify({"cameras": found, "count": len(found)})

@app.route('/api/cameras')
def list_cameras():
    """Return status of all configured cameras"""
    result = []
    for cam_id, c in cameras.items():
        conn = c["conn"]
        connected = conn.check_connection()
        info = conn.get_connection_info()
        info["cam_id"] = cam_id
        info["connected"] = connected
        result.append(info)
    return jsonify(result)

# ---------------------------------------------------------------------------
# Single-camera routes (cam1 — backward compatible)
# ---------------------------------------------------------------------------

@app.route('/api/status')
def get_status():
    c = cameras[1]
    connected = c["conn"].check_connection()
    status = c["cam"].get_status()
    conn_info = c["conn"].get_connection_info()
    status.update(conn_info)
    return jsonify(status)

@app.route('/api/connect')
def connect():
    c = cameras[1]
    connected = c["conn"].check_connection()
    return jsonify(c["conn"].get_connection_info())

# ---------------------------------------------------------------------------
# Multi-camera routes  /api/<cam_id>/...
# ---------------------------------------------------------------------------

@app.route('/api/<int:cam_id>/status')
def cam_status(cam_id):
    c = get_cam(cam_id)
    if not c:
        return jsonify({"error": f"Camera {cam_id} not found"}), 404
    connected = c["conn"].check_connection()
    status = c["cam"].get_status()
    conn_info = c["conn"].get_connection_info()
    status.update(conn_info)
    status["cam_id"] = cam_id
    return jsonify(status)

@app.route('/api/<int:cam_id>/connect')
def cam_connect(cam_id):
    c = get_cam(cam_id)
    if not c:
        return jsonify({"error": f"Camera {cam_id} not found"}), 404
    c["conn"].check_connection()
    info = c["conn"].get_connection_info()
    info["cam_id"] = cam_id
    return jsonify(info)

@app.route('/api/<int:cam_id>/presets')
def cam_presets(cam_id):
    """Diagnostic: return raw preset list from camera"""
    c = get_cam(cam_id)
    if not c:
        return jsonify({"error": f"Camera {cam_id} not found"}), 404
    result = c["conn"].send_command("/gopro/camera/presets/get")
    return jsonify(result or {"error": "presets/get failed"})

@app.route('/api/<int:cam_id>/photo', methods=['POST'])
def cam_take_photo(cam_id):
    c = get_cam(cam_id)
    if not c:
        return jsonify({"error": f"Camera {cam_id} not found"}), 404
    result = c["cam"].take_photo()
    return jsonify({"success": result, "cam_id": cam_id})

@app.route('/api/<int:cam_id>/photo/timer', methods=['POST'])
def cam_photo_timer(cam_id):
    c = get_cam(cam_id)
    if not c:
        return jsonify({"error": f"Camera {cam_id} not found"}), 404
    delay = request.json.get('delay', 5)
    c["cam"].take_photo_with_delay(
        delay, lambda r: socketio.emit('photo_taken', {'success': r, 'cam_id': cam_id})
    )
    return jsonify({"success": True, "delay": delay, "cam_id": cam_id})

@app.route('/api/<int:cam_id>/photo/interval/start', methods=['POST'])
def cam_interval_start(cam_id):
    c = get_cam(cam_id)
    if not c:
        return jsonify({"error": f"Camera {cam_id} not found"}), 404
    interval = request.json.get('interval', 10)
    result = c["cam"].start_interval_photos(
        interval, lambda r: socketio.emit('photo_taken', {'success': r, 'cam_id': cam_id})
    )
    return jsonify({"success": result, "interval": interval, "cam_id": cam_id})

@app.route('/api/<int:cam_id>/photo/interval/stop', methods=['POST'])
def cam_interval_stop(cam_id):
    c = get_cam(cam_id)
    if not c:
        return jsonify({"error": f"Camera {cam_id} not found"}), 404
    result = c["cam"].stop_interval_photos()
    return jsonify({"success": result, "cam_id": cam_id})

@app.route('/api/<int:cam_id>/video/start', methods=['POST'])
def cam_video_start(cam_id):
    c = get_cam(cam_id)
    if not c:
        return jsonify({"error": f"Camera {cam_id} not found"}), 404
    result = c["cam"].start_video()
    return jsonify({"success": result, "cam_id": cam_id})

@app.route('/api/<int:cam_id>/video/stop', methods=['POST'])
def cam_video_stop(cam_id):
    c = get_cam(cam_id)
    if not c:
        return jsonify({"error": f"Camera {cam_id} not found"}), 404
    result = c["cam"].stop_video()
    return jsonify({"success": result, "cam_id": cam_id})

@app.route('/api/<int:cam_id>/stream/start', methods=['POST'])
def cam_stream_start(cam_id):
    c = get_cam(cam_id)
    if not c:
        return jsonify({"error": f"Camera {cam_id} not found"}), 404
    result = c["conn"].start_mjpeg_stream()
    return jsonify({"success": result, "cam_id": cam_id,
                    "mjpeg_url": f"/api/{cam_id}/mjpeg"})

@app.route('/api/<int:cam_id>/mjpeg')
def cam_mjpeg(cam_id):
    c = get_cam(cam_id)
    if not c:
        return jsonify({"error": f"Camera {cam_id} not found"}), 404
    conn = c["conn"]
    def generate():
        for frame in conn.mjpeg_frames():
            yield (b'--frame\r\nContent-Type: image/jpeg\r\n\r\n' + frame + b'\r\n')
    return Response(generate(), mimetype='multipart/x-mixed-replace; boundary=frame')

@app.route('/api/<int:cam_id>/mjpeg/annotated')
def cam_mjpeg_annotated(cam_id):
    """MJPEG stream with face-detection boxes drawn on each frame."""
    import cv2, numpy as np
    c = get_cam(cam_id)
    if not c:
        return jsonify({"error": f"Camera {cam_id} not found"}), 404
    conn = c["conn"]

    def generate():
        for frame_bytes in conn.mjpeg_frames():
            status = tracker.get_status()
            # Only annotate when this camera is being tracked
            if status.get('active') and status.get('cam_id') == cam_id:
                arr = np.frombuffer(frame_bytes, dtype=np.uint8)
                frame = cv2.imdecode(arr, cv2.IMREAD_COLOR)
                if frame is not None:
                    fh, fw = frame.shape[:2]

                    # Draw face bounding box — bbox is in flipped coords, convert back to raw
                    if status.get('detected') and status.get('bbox'):
                        bx, by, w, h = status['bbox']
                        if status.get('flip'):
                            x, y = fw - bx - w, fh - by - h
                        else:
                            x, y = bx, by
                        cv2.rectangle(frame, (x, y), (x+w, y+h), (0, 255, 0), 2)
                        ox = status['offset_x']
                        oy = status['offset_y']
                        cv2.putText(frame, f"x:{ox:+.2f} y:{oy:+.2f}",
                                    (x, max(y - 6, 12)),
                                    cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 255, 0), 1,
                                    cv2.LINE_AA)
                    else:
                        state_label = "LOCKED — no face" if status.get('state') == 'tracking' else "Scanning..."
                        cv2.putText(frame, state_label,
                                    (10, 24),
                                    cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 180, 255), 1,
                                    cv2.LINE_AA)

                    # Center crosshair
                    cx, cy = fw // 2, fh // 2
                    cv2.line(frame, (cx - 20, cy), (cx + 20, cy), (0, 200, 255), 1)
                    cv2.line(frame, (cx, cy - 20), (cx, cy + 20), (0, 200, 255), 1)

                    _, buf = cv2.imencode('.jpg', frame, [cv2.IMWRITE_JPEG_QUALITY, 80])
                    frame_bytes = buf.tobytes()

            yield (b'--frame\r\nContent-Type: image/jpeg\r\n\r\n' + frame_bytes + b'\r\n')

    return Response(generate(), mimetype='multipart/x-mixed-replace; boundary=frame')

@app.route('/api/<int:cam_id>/stream/stop', methods=['POST'])
def cam_stream_stop(cam_id):
    c = get_cam(cam_id)
    if not c:
        return jsonify({"error": f"Camera {cam_id} not found"}), 404
    result = c["conn"].stop_mjpeg_stream()
    return jsonify({"success": result, "cam_id": cam_id})

@app.route('/api/<int:cam_id>/ble/wake-wifi', methods=['POST'])
def cam_wake_wifi_ble(cam_id):
    c = get_cam(cam_id)
    if not c:
        return jsonify({"error": f"Camera {cam_id} not found"}), 404
    result = c["conn"].wake_gopro_wifi()
    return jsonify(result)

@app.route('/api/<int:cam_id>/wifi/check')
def cam_check_wifi(cam_id):
    c = get_cam(cam_id)
    if not c:
        return jsonify({"error": f"Camera {cam_id} not found"}), 404
    available = c["conn"].is_gopro_wifi_available()
    return jsonify({
        "available": available,
        "ssid": c["conn"].GOPRO_SSID,
        "cam_id": cam_id,
    })

@app.route('/api/<int:cam_id>/wifi/connect', methods=['POST'])
def cam_wifi_connect(cam_id):
    """Switch this PC's WiFi to the camera's network (single-adapter workaround)"""
    c = get_cam(cam_id)
    if not c:
        return jsonify({"error": f"Camera {cam_id} not found"}), 404
    result = c["conn"].switch_to_gopro_wifi()
    return jsonify({"success": result, "cam_id": cam_id, "ssid": c["conn"].GOPRO_SSID})

@app.route('/api/<int:cam_id>/wifi/home', methods=['POST'])
def cam_wifi_home(cam_id):
    """Switch back to home WiFi"""
    c = get_cam(cam_id)
    if not c:
        return jsonify({"error": f"Camera {cam_id} not found"}), 404
    result = c["conn"].switch_to_home_wifi()
    return jsonify({"success": result, "cam_id": cam_id})

@app.route('/api/<int:cam_id>/media/list')
def cam_media_list(cam_id):
    c = get_cam(cam_id)
    if not c:
        return jsonify({"error": f"Camera {cam_id} not found"}), 404
    files = c["media"].get_media_list()
    return jsonify({"files": files, "count": len(files), "cam_id": cam_id})

@app.route('/api/<int:cam_id>/settings/resolution', methods=['POST'])
def cam_set_resolution(cam_id):
    c = get_cam(cam_id)
    if not c:
        return jsonify({"error": f"Camera {cam_id} not found"}), 404
    resolution = request.json.get('resolution', '1080')
    result = c["cam"].set_resolution(resolution)
    return jsonify({"success": result, "cam_id": cam_id})

@app.route('/api/<int:cam_id>/settings/fps', methods=['POST'])
def cam_set_fps(cam_id):
    c = get_cam(cam_id)
    if not c:
        return jsonify({"error": f"Camera {cam_id} not found"}), 404
    fps = request.json.get('fps', 30)
    result = c["cam"].set_fps(fps)
    return jsonify({"success": result, "cam_id": cam_id})

@app.route('/api/<int:cam_id>/power/off', methods=['POST'])
def cam_power_off(cam_id):
    c = get_cam(cam_id)
    if not c:
        return jsonify({"error": f"Camera {cam_id} not found"}), 404
    result = c["cam"].power_off()
    return jsonify({"success": result, "cam_id": cam_id})

# ---------------------------------------------------------------------------
# Legacy single-camera routes (keep working for backward compat with existing UI)
# ---------------------------------------------------------------------------

@app.route('/api/photo', methods=['POST'])
def take_photo():
    result = camera1.take_photo()
    return jsonify({"success": result})

@app.route('/api/photo/timer', methods=['POST'])
def take_photo_timer():
    delay = request.json.get('delay', 5)
    camera1.take_photo_with_delay(delay, lambda r: socketio.emit('photo_taken', {'success': r}))
    return jsonify({"success": True, "delay": delay})

@app.route('/api/photo/interval/start', methods=['POST'])
def start_interval():
    interval = request.json.get('interval', 10)
    result = camera1.start_interval_photos(
        interval, lambda r: socketio.emit('photo_taken', {'success': r})
    )
    return jsonify({"success": result, "interval": interval})

@app.route('/api/photo/interval/stop', methods=['POST'])
def stop_interval():
    result = camera1.stop_interval_photos()
    return jsonify({"success": result})

@app.route('/api/video/start', methods=['POST'])
def start_video():
    result = camera1.start_video()
    return jsonify({"success": result})

@app.route('/api/video/stop', methods=['POST'])
def stop_video():
    result = camera1.stop_video()
    return jsonify({"success": result})

@app.route('/api/ble/wake-wifi', methods=['POST'])
def wake_wifi_ble():
    result = connection1.wake_gopro_wifi()
    return jsonify(result)

@app.route('/api/stream/start', methods=['POST'])
def start_stream():
    result = connection1.start_mjpeg_stream()
    return jsonify({"success": result})

@app.route('/api/stream/stop', methods=['POST'])
def stop_stream():
    result = connection1.stop_mjpeg_stream()
    return jsonify({"success": result})

@app.route('/api/stream/feed')
def stream_feed():
    """Serve MJPEG stream directly from FFmpeg (cam1 only)"""
    def generate():
        if not connection1.ffmpeg_process:
            return
        while connection1.stream_active and connection1.ffmpeg_process:
            frame_data = b''
            while True:
                byte = connection1.ffmpeg_process.stdout.read(1)
                if not byte:
                    return
                frame_data += byte
                if len(frame_data) >= 2 and frame_data[-2:] == b'\xff\xd9':
                    break
            yield (b'--frame\r\n'
                   b'Content-Type: image/jpeg\r\n\r\n' + frame_data + b'\r\n')
    return Response(generate(), mimetype='multipart/x-mixed-replace; boundary=frame')

@app.route('/api/wifi/check', methods=['GET'])
def check_gopro_wifi():
    available = connection1.is_gopro_wifi_available()
    return jsonify({
        "available": available,
        "ssid": connection1.GOPRO_SSID,
        "message": "" if available else f"GoPro WiFi '{connection1.GOPRO_SSID}' not found.",
    })

@app.route('/api/wifi/gopro', methods=['POST'])
def switch_to_gopro():
    result = connection1.switch_to_gopro_wifi()
    return jsonify({"success": result})

@app.route('/api/wifi/home', methods=['POST'])
def switch_to_home():
    result = connection1.switch_to_home_wifi()
    return jsonify({"success": result})

@app.route('/api/media/list')
def list_media():
    files = media1.get_media_list()
    return jsonify({"files": files, "count": len(files)})

@app.route('/api/media/download', methods=['POST'])
def download_media():
    directory = request.json.get('directory')
    filename = request.json.get('filename')
    if not directory or not filename:
        return jsonify({"error": "Missing directory or filename"}), 400
    def progress_update(progress):
        socketio.emit('download_progress', {'progress': progress, 'filename': filename})
    local_path = media1.download_file(directory, filename, progress_update)
    if local_path:
        return jsonify({"success": True, "path": local_path})
    return jsonify({"success": False, "error": "Download failed"}), 500

@app.route('/api/media/latest')
def get_latest():
    latest = media1.get_latest_media()
    return jsonify(latest if latest else {})

@app.route('/api/media/delete', methods=['POST'])
def delete_media():
    directory = request.json.get('directory')
    filename = request.json.get('filename')
    if not directory or not filename:
        return jsonify({"error": "Missing directory or filename"}), 400
    result = media1.delete_file(directory, filename)
    return jsonify({"success": result})

@app.route('/downloads/<filename>')
def serve_download(filename):
    return send_from_directory('downloads', filename)

@app.route('/api/downloads/list')
def list_downloads():
    files = media1.get_local_files()
    return jsonify({"files": files})

@app.route('/api/settings/resolution', methods=['POST'])
def set_resolution():
    resolution = request.json.get('resolution', '1080')
    result = camera1.set_resolution(resolution)
    return jsonify({"success": result})

@app.route('/api/settings/fps', methods=['POST'])
def set_fps():
    fps = request.json.get('fps', 30)
    result = camera1.set_fps(fps)
    return jsonify({"success": result})

@app.route('/api/power/off', methods=['POST'])
def power_off():
    result = camera1.power_off()
    return jsonify({"success": result})

# ── RGB Light Routes ────────────────────────────────────────────────────────

# Arduino connection management
@app.route('/api/lights/ports')
def lights_ports():
    return jsonify({"ports": _rgb_list_ports()})

@app.route('/api/lights/arduino')
def lights_arduino_status():
    return jsonify({
        "connected": _rgb_connected(),
        "port": _rgb_port_name,
        "fallback": (not _rgb_connected()) and arduino.connected,
        "fallback_port": arduino.port if arduino.connected else None,
    })

@app.route('/api/lights/arduino/connect', methods=['POST'])
def lights_arduino_connect():
    port = (request.json or {}).get('port')
    if not port:
        return jsonify({"error": "port required"}), 400
    ok = _rgb_connect(port)
    return jsonify({"success": ok, "connected": _rgb_connected(), "port": _rgb_port_name})

@app.route('/api/lights/arduino/disconnect', methods=['POST'])
def lights_arduino_disconnect():
    _rgb_disconnect()
    return jsonify({"success": True, "connected": False})

# Light CRUD
@app.route('/api/lights')
def lights_list():
    return jsonify(list(lights.values()))

@app.route('/api/lights', methods=['POST'])
def lights_add():
    lid = _next_light_id()
    name = (request.json or {}).get('name', f'Light {lid}')
    lights[lid] = {"id": lid, "name": name,
                   "pin_r": 9, "pin_g": 10, "pin_b": 11,
                   "r": 255, "g": 255, "b": 255, "brightness": 100, "effect": None}
    _save_lights()
    return jsonify(lights[lid]), 201

@app.route('/api/lights/<int:light_id>', methods=['DELETE'])
def lights_remove(light_id):
    if light_id not in lights:
        return jsonify({"error": "not found"}), 404
    del lights[light_id]
    _save_lights()
    return jsonify({"success": True})

@app.route('/api/lights/<int:light_id>/name', methods=['POST'])
def light_rename(light_id):
    ls = lights.get(light_id)
    if ls is None:
        return jsonify({"error": "not found"}), 404
    name = (request.json or {}).get('name', '').strip()
    if name:
        ls['name'] = name
        _save_lights()
    return jsonify({"success": True, "name": ls['name']})

@app.route('/api/lights/<int:light_id>/pins', methods=['POST'])
def light_pins(light_id):
    ls = lights.get(light_id)
    if ls is None:
        return jsonify({"error": "not found"}), 404
    ls['pin_r'] = int((request.json or {}).get('pin_r', ls['pin_r']))
    ls['pin_g'] = int((request.json or {}).get('pin_g', ls['pin_g']))
    ls['pin_b'] = int((request.json or {}).get('pin_b', ls['pin_b']))
    _save_lights()
    return jsonify({"success": True, **ls})

@app.route('/api/lights/<int:light_id>/color', methods=['POST'])
def light_color(light_id):
    ls = lights.get(light_id)
    if ls is None:
        return jsonify({"error": "not found"}), 404
    ls['r'] = int((request.json or {}).get('r', 255))
    ls['g'] = int((request.json or {}).get('g', 255))
    ls['b'] = int((request.json or {}).get('b', 255))
    ls['effect'] = None
    ok = _light_send_color(ls)
    return jsonify({"success": ok})

@app.route('/api/lights/<int:light_id>/brightness', methods=['POST'])
def light_brightness(light_id):
    ls = lights.get(light_id)
    if ls is None:
        return jsonify({"error": "not found"}), 404
    ls['brightness'] = max(0, min(100, int((request.json or {}).get('brightness', 100))))
    ok = _light_send_color(ls)
    return jsonify({"success": ok})

@app.route('/api/lights/<int:light_id>/effect', methods=['POST'])
def light_effect(light_id):
    ls = lights.get(light_id)
    if ls is None:
        return jsonify({"error": "not found"}), 404
    effect = (request.json or {}).get('effect', 'STOP').upper()
    ls['effect'] = None if effect == 'STOP' else effect
    _rgb_send(f"PINS:{ls['pin_r']},{ls['pin_g']},{ls['pin_b']}")
    ok = _rgb_send(f"EFFECT:{effect}")
    return jsonify({"success": ok})

@app.route('/api/lights/<int:light_id>/off', methods=['POST'])
def light_off(light_id):
    ls = lights.get(light_id)
    if ls is None:
        return jsonify({"error": "not found"}), 404
    _rgb_send(f"PINS:{ls['pin_r']},{ls['pin_g']},{ls['pin_b']}")
    ok = _rgb_send("RGB:0,0,0")
    return jsonify({"success": ok})

# Timeline routes
@app.route('/api/lights/<int:light_id>/timeline', methods=['GET'])
def light_timeline_get(light_id):
    tl = _tl(light_id)
    return jsonify({"blocks": tl['blocks'], "transitions": tl['transitions'], "loop": tl['loop']})

@app.route('/api/lights/<int:light_id>/timeline', methods=['POST'])
def light_timeline_set(light_id):
    data = request.json or {}
    tl = _tl(light_id)
    tl['blocks'] = data.get('blocks', [])
    tl['transitions'] = data.get('transitions', [])
    if 'loop' in data:
        tl['loop'] = bool(data['loop'])
    return jsonify({"success": True, "count": len(tl['blocks'])})

@app.route('/api/lights/<int:light_id>/timeline/play', methods=['POST'])
def light_timeline_play(light_id):
    ls = lights.get(light_id)
    if ls is None:
        return jsonify({"error": "not found"}), 404
    tl = _tl(light_id)
    if not tl['blocks']:
        return jsonify({"error": "no blocks"}), 400
    if tl['playing']:
        tl['_stop'] = True
        time.sleep(0.1)

    def _playback():
        if not arduino.connected and not _rgb_connected():
            tl['playing'] = False
            return
        tl['playing'] = True
        tl['_stop'] = False
        pins_cmd = f"PINS:{ls['pin_r']},{ls['pin_g']},{ls['pin_b']}"
        while True:
            # Sort blocks by start time so freely-dragged blocks play in order
            blocks = sorted(tl['blocks'], key=lambda b: float(b.get('start', 0)))
            transitions = list(tl['transitions'])
            play_start = time.time()
            for i, block in enumerate(blocks):
                if tl['_stop']: break
                # Wait until this block's scheduled start time
                target_t = play_start + float(block.get('start', 0))
                while time.time() < target_t:
                    if tl['_stop']: break
                    time.sleep(0.02)
                if tl['_stop']: break
                # Apply block color — delay 50ms between PINS and RGB so Arduino processes PINS first
                r, g, b = _hex_to_rgb(block['color'])
                scale = block['brightness'] / 100.0
                _rgb_send(pins_cmd)
                time.sleep(0.05)
                _rgb_send(f"RGB:{int(r*scale)},{int(g*scale)},{int(b*scale)}")
                # Wait until block ends (relative to play_start)
                block_end = target_t + float(block['duration'])
                while time.time() < block_end:
                    if tl['_stop']: break
                    time.sleep(0.02)
                # Transition to next block if adjacent
                if not tl['_stop'] and i < len(blocks) - 1:
                    tr = transitions[i] if i < len(transitions) else {'color_mode': 'instant_start', 'brightness_mode': 'instant_start', 'duration': 0}
                    _perform_transition(ls, block, blocks[i + 1], tr, tl)
            if tl['_stop'] or not tl['loop']:
                break
        if not tl['_stop']:
            _rgb_send(pins_cmd)
            time.sleep(0.05)
            _rgb_send("RGB:0,0,0")
        tl['playing'] = False

    threading.Thread(target=_playback, daemon=True).start()
    return jsonify({"success": True})

@app.route('/api/lights/<int:light_id>/timeline/stop', methods=['POST'])
def light_timeline_stop(light_id):
    tl = _tl(light_id)
    tl['_stop'] = True
    tl['playing'] = False
    return jsonify({"success": True})

# === SocketIO Events ===

@socketio.on('connect')
def handle_connect():
    print('Client connected')
    c1_connected = connection1.check_connection()
    c2_connected = connection2.check_connection()
    socketio.emit('connection_status', {
        'cam1': c1_connected,
        'cam2': c2_connected,
        'connected': c1_connected,  # backward compat
    })

@socketio.on('keep_alive')
def handle_keep_alive():
    connection1.keep_alive()

@socketio.on('keep_alive_cam')
def handle_keep_alive_cam(data):
    cam_id = data.get('cam_id', 1)
    c = get_cam(cam_id)
    if c:
        c["conn"].keep_alive()

if __name__ == '__main__':
    print("=" * 60)
    print("GoPro Controller — Dual Camera Support")
    print("=" * 60)
    print()
    print("To use both cameras simultaneously, connect BOTH via USB-C.")
    print("Each USB connection gets its own IP in the 172.2x.1xx.51 range.")
    print()
    print("Quick-check what cameras are visible:")
    print("  GET http://localhost:5000/api/scan")
    print()
    print("Control individual cameras:")
    print("  GET  http://localhost:5000/api/1/status")
    print("  GET  http://localhost:5000/api/2/status")
    print("  POST http://localhost:5000/api/1/stream/start")
    print("  POST http://localhost:5000/api/2/stream/start")
    print()
    print("HLS streams (after starting):")
    print("  /static/hls/cam1/stream.m3u8")
    print("  /static/hls/cam2/stream.m3u8")
    print()
    print("Then open http://localhost:5000 in your browser")
    print("=" * 60)

    socketio.run(app, host='0.0.0.0', port=5000, debug=True, allow_unsafe_werkzeug=True)
