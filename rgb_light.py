"""
RGB Light Controller — serial interface for rgb_gui_controller.ino
Protocol: RGB:r,g,b  |  EFFECT:RAINBOW/FADE/STOP  |  PINS:r,g,b
Baud: 9600
"""

import serial
import serial.tools.list_ports
import threading
import time
from typing import Optional


class RGBLight:

    def __init__(self, light_id: int, name: str = ""):
        self.light_id = light_id
        self.name = name or f"Light {light_id}"
        self.ser: Optional[serial.Serial] = None
        self.port: Optional[str] = None
        self.connected = False
        self._lock = threading.Lock()

        # State
        self.r, self.g, self.b = 255, 255, 255
        self.brightness = 100   # 0–100 %
        self.effect: Optional[str] = None

    # ── Helpers ──────────────────────────────────────────────────────────────

    def _scaled(self, channel: int) -> int:
        return int(round(channel * self.brightness / 100))

    @staticmethod
    def list_ports() -> list:
        return [p.device for p in serial.tools.list_ports.comports()]

    # ── Connection ───────────────────────────────────────────────────────────

    def connect(self, port: str) -> bool:
        try:
            if self.ser and self.ser.is_open:
                self.ser.close()
            s = serial.Serial(port, 9600, timeout=1)
            time.sleep(2)           # wait for Arduino reset
            s.flushInput()
            # Probe: send a black command and look for OK echo
            s.write(b"RGB:0,0,0\n")
            s.flush()
            time.sleep(0.4)
            confirmed = False
            if s.in_waiting:
                resp = s.readline().decode(errors="ignore").strip()
                confirmed = "OK:RGB" in resp
            # Accept connection even if probe not echoed (firmware may be
            # mid-startup sequence and eating the response)
            self.ser = s
            self.port = port
            self.connected = True
            print(f"[light{self.light_id}] Connected on {port}"
                  + (" (confirmed)" if confirmed else " (assumed)"))
            return True
        except Exception as e:
            print(f"[light{self.light_id}] Connect error: {e}")
            self.connected = False
            return False

    def disconnect(self):
        if self.ser and self.ser.is_open:
            self.ser.close()
        self.connected = False
        self.ser = None
        self.port = None

    def get_status(self) -> dict:
        return {
            "id":         self.light_id,
            "name":       self.name,
            "connected":  self.connected,
            "port":       self.port,
            "r":          self.r,
            "g":          self.g,
            "b":          self.b,
            "brightness": self.brightness,
            "effect":     self.effect,
        }

    # ── Serial send ──────────────────────────────────────────────────────────

    def send(self, cmd: str) -> bool:
        with self._lock:
            if not self.ser or not self.ser.is_open:
                return False
            try:
                self.ser.write((cmd + "\n").encode())
                self.ser.flush()
                return True
            except Exception as e:
                print(f"[light{self.light_id}] Send error: {e}")
                self.connected = False
                return False

    # ── Commands ─────────────────────────────────────────────────────────────

    def set_color(self, r: int, g: int, b: int) -> bool:
        self.r, self.g, self.b = r, g, b
        self.effect = None
        sr = self._scaled(r)
        sg = self._scaled(g)
        sb = self._scaled(b)
        return self.send(f"RGB:{sr},{sg},{sb}")

    def set_brightness(self, brightness: int) -> bool:
        self.brightness = max(0, min(100, brightness))
        # Re-send current color at new brightness
        return self.set_color(self.r, self.g, self.b)

    def set_effect(self, effect: str) -> bool:
        effect = effect.upper()
        self.effect = None if effect == "STOP" else effect
        return self.send(f"EFFECT:{effect}")

    def turn_off(self) -> bool:
        return self.set_color(0, 0, 0)
