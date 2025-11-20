"""
GoPro Controller - Main Application
Web-based interface for controlling GoPro Hero 12
"""

import os
from flask import Flask, render_template, jsonify, request, send_from_directory
from flask_socketio import SocketIO
from gopro import GoProConnection, GoProCamera, GoProMedia

app = Flask(__name__)
app.config['SECRET_KEY'] = 'gopro-controller-secret'
socketio = SocketIO(app, cors_allowed_origins="*")

# Initialize GoPro modules
connection = GoProConnection()
camera = GoProCamera(connection)
media = GoProMedia(connection, download_dir="downloads")

# === Web Routes ===

@app.route('/')
def index():
    """Main control interface"""
    return render_template('index.html')

@app.route('/api/status')
def get_status():
    """Get camera connection status"""
    connected = connection.check_connection()
    status = camera.get_status()
    conn_info = connection.get_connection_info()
    status.update(conn_info)
    return jsonify(status)

@app.route('/api/connect')
def connect():
    """Check/establish connection to GoPro"""
    connected = connection.check_connection()
    conn_info = connection.get_connection_info()
    return jsonify(conn_info)

# === Camera Control Routes ===

@app.route('/api/photo', methods=['POST'])
def take_photo():
    """Take a single photo"""
    result = camera.take_photo()
    return jsonify({"success": result})

@app.route('/api/photo/timer', methods=['POST'])
def take_photo_timer():
    """Take a photo with delay"""
    delay = request.json.get('delay', 5)
    camera.take_photo_with_delay(delay, lambda r: socketio.emit('photo_taken', {'success': r}))
    return jsonify({"success": True, "delay": delay})

@app.route('/api/photo/interval/start', methods=['POST'])
def start_interval():
    """Start interval photo capture"""
    interval = request.json.get('interval', 10)
    result = camera.start_interval_photos(
        interval,
        lambda r: socketio.emit('photo_taken', {'success': r})
    )
    return jsonify({"success": result, "interval": interval})

@app.route('/api/photo/interval/stop', methods=['POST'])
def stop_interval():
    """Stop interval photo capture"""
    result = camera.stop_interval_photos()
    return jsonify({"success": result})

@app.route('/api/video/start', methods=['POST'])
def start_video():
    """Start video recording"""
    result = camera.start_video()
    return jsonify({"success": result})

@app.route('/api/video/stop', methods=['POST'])
def stop_video():
    """Stop video recording"""
    result = camera.stop_video()
    return jsonify({"success": result})

# === Preview Stream Routes ===

@app.route('/api/stream/start', methods=['POST'])
def start_stream():
    """Start preview stream"""
    result = connection.start_preview_stream()
    return jsonify({"success": result})

@app.route('/api/stream/stop', methods=['POST'])
def stop_stream():
    """Stop preview stream"""
    result = connection.stop_preview_stream()
    return jsonify({"success": result})

# === Media Routes ===

@app.route('/api/media/list')
def list_media():
    """Get list of media files on camera"""
    files = media.get_media_list()
    return jsonify({"files": files, "count": len(files)})

@app.route('/api/media/download', methods=['POST'])
def download_media():
    """Download a media file"""
    directory = request.json.get('directory')
    filename = request.json.get('filename')

    if not directory or not filename:
        return jsonify({"error": "Missing directory or filename"}), 400

    def progress_update(progress):
        socketio.emit('download_progress', {'progress': progress, 'filename': filename})

    local_path = media.download_file(directory, filename, progress_update)

    if local_path:
        return jsonify({"success": True, "path": local_path})
    return jsonify({"success": False, "error": "Download failed"}), 500

@app.route('/api/media/latest')
def get_latest():
    """Get the most recent media file info"""
    latest = media.get_latest_media()
    return jsonify(latest if latest else {})

@app.route('/api/media/delete', methods=['POST'])
def delete_media():
    """Delete a media file from camera"""
    directory = request.json.get('directory')
    filename = request.json.get('filename')

    if not directory or not filename:
        return jsonify({"error": "Missing directory or filename"}), 400

    result = media.delete_file(directory, filename)
    return jsonify({"success": result})

@app.route('/downloads/<filename>')
def serve_download(filename):
    """Serve downloaded media files"""
    return send_from_directory('downloads', filename)

@app.route('/api/downloads/list')
def list_downloads():
    """List locally downloaded files"""
    files = media.get_local_files()
    return jsonify({"files": files})

# === Settings Routes ===

@app.route('/api/settings/resolution', methods=['POST'])
def set_resolution():
    """Set video resolution"""
    resolution = request.json.get('resolution', '1080')
    result = camera.set_resolution(resolution)
    return jsonify({"success": result})

@app.route('/api/settings/fps', methods=['POST'])
def set_fps():
    """Set video FPS"""
    fps = request.json.get('fps', 30)
    result = camera.set_fps(fps)
    return jsonify({"success": result})

@app.route('/api/power/off', methods=['POST'])
def power_off():
    """Turn off the camera"""
    result = camera.power_off()
    return jsonify({"success": result})

# === SocketIO Events ===

@socketio.on('connect')
def handle_connect():
    """Handle client connection"""
    print('Client connected')
    connected = connection.check_connection()
    socketio.emit('connection_status', {'connected': connected})

@socketio.on('keep_alive')
def handle_keep_alive():
    """Keep camera connection alive"""
    connection.keep_alive()

if __name__ == '__main__':
    print("=" * 50)
    print("GoPro Controller")
    print("=" * 50)
    print("\nFor USB connection:")
    print("  1. On GoPro: Preferences > Connections > USB Connection > GoPro Connect")
    print("  2. Connect USB-C cable to PC")
    print("\nFor WiFi connection:")
    print("  1. Connect PC to GoPro's WiFi network")
    print("\nThen open http://localhost:5000 in your browser")
    print("\n" + "=" * 50)

    socketio.run(app, host='0.0.0.0', port=5000, debug=True)
