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

    def __init__(self, connection: GoProConnection):
        self.conn = connection
        self.is_recording = False
        self.timer_thread = None
        self.timer_running = False

    # === Mode Control ===

    def set_mode_video(self) -> bool:
        """Set camera to video mode"""
        # Preset 0 = Standard video
        result = self.conn.send_command("/gopro/camera/presets/load", {"id": 0})
        return result is not None

    def set_mode_photo(self) -> bool:
        """Set camera to photo mode"""
        # Preset 1 = Photo
        result = self.conn.send_command("/gopro/camera/presets/load", {"id": 1})
        return result is not None

    def set_mode_timelapse(self) -> bool:
        """Set camera to timelapse mode"""
        # Preset 2 = Timelapse
        result = self.conn.send_command("/gopro/camera/presets/load", {"id": 2})
        return result is not None

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

    def take_photo(self) -> bool:
        """Take a single photo"""
        self.set_mode_photo()
        time.sleep(0.5)  # Wait for mode change
        return self.shutter_on()

    def start_video(self) -> bool:
        """Start video recording"""
        self.set_mode_video()
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
