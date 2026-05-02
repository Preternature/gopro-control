"""
GoPro Controller - Main Application
Web-based interface for controlling one or two GoPro cameras.
"""

import os
from flask import Flask, render_template, jsonify, request, send_from_directory, Response
from flask_socketio import SocketIO
from gopro import GoProConnection, GoProCamera, GoProMedia

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
