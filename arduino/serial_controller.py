"""
Arduino Serial Controller
Handles communication with the Arduino running Camera Rail + Gimbal firmware.
"""

import serial
import serial.tools.list_ports
import threading
import time
from typing import Optional


class ArduinoController:
    def __init__(self):
        self.ser: Optional[serial.Serial] = None
        self.port: Optional[str] = None
        self.connected = False
        self._lock = threading.Lock()
        self._reconnecting = False

        # State
        self.speed = 91
        self.duration = 10000
        self.base_angle = 90
        self.cam_us = 1500

    # ── Connection ──────────────────────────────────────────────────────────

    def find_arduino(self):
        ports = serial.tools.list_ports.comports()
        for p in ports:
            desc = p.description or ""
            if any(kw in desc for kw in ("Arduino", "CH340", "USB Serial", "ttyUSB", "ttyACM")):
                try:
                    s = serial.Serial(p.device, 9600, timeout=1)
                    time.sleep(2)
                    for _ in range(3):
                        s.flushInput()
                        s.write(b"P\n")
                        s.flush()
                        time.sleep(0.3)
                        if s.in_waiting:
                            resp = s.readline().decode(errors="ignore").strip()
                            if "PONG" in resp or "READY" in resp:
                                return s, p.device
                    s.close()
                except Exception:
                    continue
        return None, None

    def connect(self) -> bool:
        if self._reconnecting:
            return False
        self._reconnecting = True
        try:
            if self.ser and self.ser.is_open:
                self.ser.close()
            ser, port = self.find_arduino()
            if ser:
                self.ser = ser
                self.port = port
                self.connected = True
                self._send_settings()
                print(f"[arduino] Connected on {port}")
                return True
            else:
                self.ser = None
                self.port = None
                self.connected = False
                print("[arduino] Not found")
                return False
        finally:
            self._reconnecting = False

    def disconnect(self):
        if self.ser and self.ser.is_open:
            self.ser.close()
        self.connected = False

    def get_status(self) -> dict:
        return {
            "connected": self.connected,
            "port": self.port,
            "speed": self.speed,
            "duration": self.duration,
            "base_angle": self.base_angle,
            "cam_us": self.cam_us,
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
                print(f"[arduino] Send error: {e}")
                self.connected = False
                return False

    def _send_settings(self):
        self.send(f"S{self.speed},{self.duration}")

    # ── Rail ─────────────────────────────────────────────────────────────────

    def rail_set_speed(self, speed: int):
        self.speed = max(10, min(2000, speed))
        self._send_settings()

    def rail_set_duration(self, duration: int):
        self.duration = max(100, min(60000, duration))
        self._send_settings()

    def rail_away(self) -> bool:
        return self.send("U")

    def rail_toward(self) -> bool:
        return self.send("D")

    def rail_stop(self) -> bool:
        return self.send("X")

    # ── Gimbal ───────────────────────────────────────────────────────────────

    def gimbal_base_angle(self, angle: int) -> bool:
        angle = max(0, min(180, angle))
        self.base_angle = angle
        return self.send(f"B{angle}")

    def gimbal_base_us(self, us: int) -> bool:
        us = max(400, min(2600, us))
        return self.send(f"BUS{us}")

    def gimbal_cam_angle(self, angle: int) -> bool:
        angle = max(0, min(180, angle))
        return self.send(f"C{angle}")

    def gimbal_cam_us(self, us: int) -> bool:
        us = max(400, min(2600, us))
        self.cam_us = us
        return self.send(f"CUS{us}")

    def gimbal_center(self) -> bool:
        self.gimbal_base_angle(90)
        self.gimbal_cam_us(1500)
        return True

    def gimbal_sweep_base(self) -> bool:
        return self.send("b")

    def gimbal_sweep_cam(self) -> bool:
        return self.send("c")

    def gimbal_sweep_both(self) -> bool:
        return self.send("a")

    # ── RGB Lights ───────────────────────────────────────────────────────────
    # Commands: RGB<n>:r,g,b  |  EFFECT<n>:RAINBOW/FADE/STOP
    # where n is the light number (1, 2, …)

    def rgb_color(self, light: int, r: int, g: int, b: int) -> bool:
        return self.send(f"RGB{light}:{r},{g},{b}")

    def rgb_effect(self, light: int, effect: str) -> bool:
        return self.send(f"EFFECT{light}:{effect.upper()}")

    def rgb_off(self, light: int) -> bool:
        return self.send(f"RGB{light}:0,0,0")
