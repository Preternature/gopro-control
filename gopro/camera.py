"""
GoPro Camera Control Module
Commands for photo, video, and camera settings
"""

import time
import threading
from typing import Optional, Callable
from .connection import GoProConnection

class GoProCamera:
    """Camera control operations for GoPro Hero 12"""

    # GoPro preset group IDs (strings as returned by /gopro/camera/presets/get)
    _GROUP_VIDEO      = 'PRESET_GROUP_ID_VIDEO'
    _GROUP_PHOTO      = 'PRESET_GROUP_ID_PHOTO'
    _GROUP_TIMELAPSE  = 'PRESET_GROUP_ID_TIMELAPSE'

    def __init__(self, connection: GoProConnection):
        self.conn = connection
        self.is_recording = False
        self.timer_thread = None
        self.timer_running = False
        self._preset_cache: dict = {}  # group_id -> first preset id

    # === Preset discovery ===

    def _first_preset_id(self, group_id: int) -> Optional[int]:
        """Return the first preset ID in the given group, cached after first query."""
        if group_id in self._preset_cache:
            return self._preset_cache[group_id]
        print(f"[{self.conn.name}] Fetching presets to find group {group_id}...")
        result = self.conn.send_command("/gopro/camera/presets/get")
        if not result:
            print(f"[{self.conn.name}] presets/get returned nothing — camera not connected?")
            return None
        groups = result.get("presetGroupArray", [])
        print(f"[{self.conn.name}] Got {len(groups)} preset groups: ids={[g.get('id') for g in groups]}")
        for group in groups:
            gid = group.get("id")
            presets = group.get("presetArray", [])
            if presets:
                self._preset_cache[gid] = presets[0].get("id")
                print(f"[{self.conn.name}]   group {gid} -> first preset id {self._preset_cache[gid]}")
        found = self._preset_cache.get(group_id)
        if found is None:
            print(f"[{self.conn.name}] Group {group_id} NOT found. Available groups: {list(self._preset_cache.keys())}")
        return found

    def _load_preset_group(self, group_id: int) -> bool:
        pid = self._first_preset_id(group_id)
        if pid is None:
            print(f"[{self.conn.name}] Could not find preset for group {group_id}")
            return False
        print(f"[{self.conn.name}] Loading preset id={pid} (group {group_id})...")
        result = self.conn.send_command("/gopro/camera/presets/load", {"id": pid})
        if result is None:
            print(f"[{self.conn.name}] presets/load FAILED (camera returned error or timed out)")
            return False
        print(f"[{self.conn.name}] presets/load OK: {result}")
        return True

    # === Mode Control ===

    def set_mode_video(self) -> bool:
        return self._load_preset_group(self._GROUP_VIDEO)

    def set_mode_photo(self) -> bool:
        return self._load_preset_group(self._GROUP_PHOTO)

    def set_mode_timelapse(self) -> bool:
        return self._load_preset_group(self._GROUP_TIMELAPSE)

    # === Shutter Control ===

    def shutter_on(self) -> bool:
        """Start recording or take photo"""
        result = self.conn.send_command("/gopro/camera/shutter/start")
        if result:
            self.is_recording = True
        return result is not None

    def shutter_off(self) -> bool:
        """Stop recording"""
        result = self.conn.send_command("/gopro/camera/shutter/stop")
        if result:
            self.is_recording = False
        return result is not None

    # Seconds to let the GoPro settle after a photo before restarting the preview.
    # The camera can't stream for ~8s after a capture; a clean start before then
    # never locks. After settling, a clean start comes up stable in ~2s.
    _PHOTO_SETTLE_SEC = 8.0

    def take_photo(self) -> bool:
        """Take a single photo. If preview was live, restore it in the background.

        A photo requires switching to the photo preset, which stops the video stream;
        the GoPro then can't stream again for ~8s. So we fully stop the stream (which
        also makes the preset switch reliable — no HTTP 500), take the shot, and kick
        off a background task that waits for the camera to settle, then does a clean
        stream restart. take_photo itself returns fast; the client polls /stream/ready
        to reconnect the preview once frames are genuinely flowing."""
        # Re-assert wired USB control — the camera drops it after sleeping.
        self.conn.enable_wired_usb_control()
        was_streaming = self.conn.stream_active
        # Fully stop streaming (FFmpeg + GoPro feed). Leaving FFmpeg running on a dead
        # UDP socket through the photo prevents it from ever re-locking cleanly.
        if was_streaming:
            self.conn.stop_mjpeg_stream()
            time.sleep(0.3)
        ok = self.set_mode_photo()
        if ok:
            time.sleep(0.5)
            result = self.shutter_on()
        else:
            result = False
        self.is_recording = False  # a photo is not a recording
        self.set_mode_video()
        if was_streaming:
            threading.Thread(target=self._restore_preview_bg, daemon=True).start()
        return result

    def _restore_preview_bg(self) -> None:
        """Background: wait for the camera to settle after a photo, then bring the
        preview back with a clean start, retrying until frames actually flow."""
        time.sleep(self._PHOTO_SETTLE_SEC)
        for _ in range(3):
            self.conn.start_mjpeg_stream()
            deadline = time.time() + 12
            while time.time() < deadline:
                if self.conn.stream_ready():
                    print(f"[{self.conn.name}] preview restored after photo")
                    return
                time.sleep(0.5)
            print(f"[{self.conn.name}] preview not back yet — retrying clean restart")
        print(f"[{self.conn.name}] preview restore gave up (camera may need a manual Start Preview)")

    def start_video(self) -> bool:
        """Start video recording"""
        # Re-assert wired USB control — the camera drops it after sleeping.
        self.conn.enable_wired_usb_control()
        if not self.set_mode_video():
            return False
        time.sleep(0.5)
        result = self.shutter_on()
        if result:
            self.is_recording = True
        return result

    def stop_video(self) -> bool:
        """Stop video recording"""
        result = self.shutter_off()
        if result:
            self.is_recording = False
        return result

    # === Timer/Interval Functions ===

    def take_photo_with_delay(self, delay_seconds: int, callback: Callable = None) -> None:
        """Take a photo after a delay"""
        def delayed_capture():
            time.sleep(delay_seconds)
            result = self.take_photo()
            if callback:
                callback(result)

        thread = threading.Thread(target=delayed_capture)
        thread.start()

    def start_interval_photos(self, interval_seconds: int, callback: Callable = None) -> bool:
        """Start taking photos at regular intervals"""
        if self.timer_running:
            return False

        self.timer_running = True

        def interval_capture():
            while self.timer_running:
                result = self.take_photo()
                if callback:
                    callback(result)
                time.sleep(interval_seconds)

        self.timer_thread = threading.Thread(target=interval_capture)
        self.timer_thread.start()
        return True

    def stop_interval_photos(self) -> bool:
        """Stop interval photo capture"""
        self.timer_running = False
        if self.timer_thread:
            self.timer_thread.join(timeout=2)
        return True

    # === Camera Settings ===

    def get_status(self) -> Optional[dict]:
        """Get camera status including battery, recording state, etc."""
        state = self.conn.get_camera_state()
        if state:
            return {
                "connected": True,
                "recording": self.is_recording,
                "state": state
            }
        return {"connected": False}

    def set_resolution(self, resolution: str) -> bool:
        """Set video resolution (e.g., '4k', '2.7k', '1080')"""
        # Resolution setting IDs for Hero 12
        resolutions = {
            "5.3k": 100,
            "4k": 1,
            "2.7k": 4,
            "1080": 9,
            "720": 12
        }

        if resolution.lower() not in resolutions:
            return False

        result = self.conn.send_command(
            "/gopro/camera/setting",
            {"setting": 2, "option": resolutions[resolution.lower()]}
        )
        return result is not None

    def set_fps(self, fps: int) -> bool:
        """Set video frame rate"""
        # FPS setting IDs
        fps_options = {
            240: 0,
            120: 1,
            60: 5,
            30: 8,
            24: 10
        }

        if fps not in fps_options:
            return False

        result = self.conn.send_command(
            "/gopro/camera/setting",
            {"setting": 3, "option": fps_options[fps]}
        )
        return result is not None

    def power_off(self) -> bool:
        """Turn off the camera"""
        result = self.conn.send_command("/gopro/camera/control/power_off")
        return result is not None
