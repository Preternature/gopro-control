"""
Arduino Serial Controller
Handles communication with the Arduino Mega running the unified Camera Rail +
Gimbal + RGB firmware (unified_controller.ino).

Rail model (Sep 2026, "rotor" leadscrew rail):
  * The board is the source of truth for position. Home (pos 0) and the far mark
    (LEN) are set BY HAND: jog near the motor end -> mark_home (firmware ZERO),
    jog near the far end -> mark_far (LEN). No stall-hunting in normal use.
  * We connect WITHOUT asserting DTR, so a server restart does not reset the Mega
    and the marks survive. If the board is already homed when we connect we adopt
    its zero and length instead of pushing ours over it.
  * Per-rail profile lives in LCA/rail_calib.json, shared with rail_lab.py:
    hdir/hspd/hmax/hcur (homing), cur/ramp/mode/speed/sgt/scurve (run settings),
    length (fence), plus top-level locked (marks protected) and mirror (UI sides).
  * Speed map measured with the camera: firmware tops out ~12k steps/s (S0..S30
    all the same), S40-S50 sits on the rig's resonance, S60+ is quiet.
    step period ~= 2*delay + 60 us  -> used by go(seconds=...) to fit a move to a
    duration.
"""

import os
import json
import serial
import serial.tools.list_ports
import threading
import time
from typing import Optional

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CALIB = os.path.join(HERE, "rail_calib.json")

RAIL_PROFILES = {
    "timer": {"hdir": "U", "hspd": 150, "hmax": 200000, "hcur": 0,
              "cur": 1100, "ramp": 1000, "mode": "STEALTH", "speed": 60, "sgt": 80, "scurve": True},
    "rotor": {"hdir": "U", "hspd": 150, "hmax": 60000, "hcur": 600,
              "cur": 1200, "ramp": 2000, "mode": "STEALTH", "speed": 60, "sgt": 80, "scurve": True},
}
PRESETS = {
    "slow": {"speed": 60, "mode": "STEALTH"},   # quiet, below the resonance band
    "fast": {"speed": 10, "mode": "SPREAD"},    # 12k steps/s ceiling, clean
}
STEP_OVERHEAD_US = 60      # per-step loop overhead on top of 2*delay (measured)
END_MARGIN = 1000          # steps kept back from the far mark on go('end')
BAUD = 9600


class ArduinoController:
    def __init__(self):
        self.ser: Optional[serial.Serial] = None
        self.port: Optional[str] = None
        self.connected = False
        self.preserved = False          # board state survived our connect (no reset)
        self._lock = threading.Lock()
        self._reconnecting = False
        self.firmware = ""

        # Gimbal state
        self.base_angle = 90
        self.cam_us = 1900

        # Rail position state, mirrored from firmware POS? replies
        self.rail = {
            "pos": 0, "homed": False, "stalled": False,
            "length": 0, "moving": False, "homing": False,
            "last_msg": "",
        }
        self.calib = self._load_calib()

        threading.Thread(target=self._watchdog, daemon=True).start()

    # ── Profile (rail_calib.json, shared with rail_lab.py) ───────────────────

    def _load_calib(self) -> dict:
        try:
            with open(CALIB) as f:
                d = json.load(f)
        except Exception:
            d = {}
        if "rails" not in d:
            d = {"active": "rotor", "rails": {}}
        d.setdefault("active", "rotor")
        d.setdefault("locked", True)
        d.setdefault("mirror", False)
        for rt, prof in RAIL_PROFILES.items():
            r = d["rails"].setdefault(rt, {})
            r.setdefault("length", 0)
            for k, v in prof.items():
                r.setdefault(k, v)
        return d

    def save_calib(self):
        try:
            with open(CALIB, "w") as f:
                json.dump(self.calib, f, indent=2)
        except Exception as e:
            print(f"[arduino] calib save failed: {e}")

    def prof(self) -> dict:
        return self.calib["rails"][self.calib["active"]]

    @property
    def speed(self) -> int:
        return int(self.prof().get("speed", 60))

    # ── Watchdog ─────────────────────────────────────────────────────────────

    def _watchdog(self):
        while True:
            time.sleep(3)
            if self.port and not self.connected and not self._reconnecting:
                print("[arduino] Connection lost - attempting reconnect...")
                self.connect()

    # ── Connection ───────────────────────────────────────────────────────────

    def _open_no_reset(self, device: str) -> Optional[serial.Serial]:
        """Open the port without a DTR edge (no Mega auto-reset). Returns the
        port if the board answers PONG; falls back to a real reset if silent."""
        s = serial.Serial()
        s.port = device
        s.baudrate = BAUD
        s.timeout = 0.05           # fixed for the session - never reconfigure later
        s.dtr = False
        s.rts = False
        s.open()
        time.sleep(0.3)
        s.reset_input_buffer()
        if self._probe(s):
            self.preserved = True
            return s
        # silent -> reset it properly and wait for boot
        self.preserved = False
        s.dtr = False; time.sleep(0.1); s.dtr = True
        time.sleep(2.5)
        s.reset_input_buffer()
        if self._probe(s):
            return s
        s.close()
        return None

    @staticmethod
    def _probe(s: serial.Serial) -> bool:
        for _ in range(3):
            s.reset_input_buffer()
            s.write(b"P\n"); s.flush()
            deadline = time.time() + 0.8
            buf = b""
            while time.time() < deadline:
                chunk = s.read(s.in_waiting or 1)
                if chunk:
                    buf += chunk
                    if b"PONG" in buf or b"READY" in buf:
                        return True
        return False

    def find_arduino(self):
        for p in serial.tools.list_ports.comports():
            desc = p.description or ""
            if any(kw in desc for kw in ("Arduino", "CH340", "USB Serial", "ttyUSB", "ttyACM")):
                try:
                    s = self._open_no_reset(p.device)
                    if s:
                        return s, p.device
                except Exception as e:
                    print(f"[arduino] {p.device}: {e}  (is Rail Lab holding the port?)")
                    continue
        return None, None

    def connect(self) -> bool:
        if self._reconnecting:
            return False
        if self.connected and self.ser and self.ser.is_open:
            if self.query("P", timeout=0.6, expect="PONG"):
                return True
        self._reconnecting = True
        try:
            if self.ser and self.ser.is_open:
                self.ser.close()
            ser, port = self.find_arduino()
            if not ser:
                self.ser = None; self.port = None; self.connected = False
                print("[arduino] Not found")
                return False
            self.ser = ser
            self.port = port
            self.connected = True
            ver = self.query("VER?", expect="VER:")
            self.firmware = ver[4:] if ver else ""
            print(f"[arduino] Connected on {port} ({'board state preserved' if self.preserved else 'fresh boot'}), firmware {self.firmware or '?'}")
            tmc = self.query("TMC?", expect="TMC:")
            print(f"[arduino] {tmc or 'TMC? no reply'}")
            self.apply_profile()
            return True
        finally:
            self._reconnecting = False

    def disconnect(self):
        if self.ser and self.ser.is_open:
            self.ser.close()
        self.connected = False

    def get_status(self) -> dict:
        p = self.prof()
        return {
            "connected": self.connected,
            "port": self.port,
            "firmware": self.firmware,
            "preserved": self.preserved,
            "speed": p["speed"],
            "mode": p["mode"],
            "scurve": bool(p.get("scurve", True)),
            "base_angle": self.base_angle,
            "cam_us": self.cam_us,
        }

    # ── Profile push ─────────────────────────────────────────────────────────

    def apply_profile(self, force: bool = False):
        """Push the active rail profile. If the board is still homed (we did not
        reset it), its zero and LEN are the truth: adopt the length, and do NOT
        send HDIR (clears homed) or LEN. force=True pushes everything."""
        p = self.prof()
        live = None
        if not force:
            st = self.rail_status()
            if st.get("homed"):
                live = st
        if live:
            if live["length"] > 0 and live["length"] != p["length"]:
                p["length"] = live["length"]
                self.save_calib()
            print(f"[arduino] board still homed - kept its zero, length {p['length']}")
        else:
            self.send(f"HDIR:{p['hdir']}")
            self.send(f"LEN{p['length']}")
        self.send(f"HSPD{p['hspd']}")
        self.send(f"HMAX{p['hmax']}")
        self.send(f"HCUR{p['hcur']}")
        self.apply_run_settings()

    def apply_run_settings(self):
        p = self.prof()
        self.send(f"CUR{p['cur']}")
        self.send(f"RAMP{p['ramp']}")
        self.send(f"SGT{p['sgt']}")
        self.send(f"TMODE:{p['mode']}")
        self.send("SCURVE1" if p.get("scurve", True) else "SCURVE0")
        self.send(f"S{p['speed']},10000")

    # ── Serial send / query ──────────────────────────────────────────────────

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

    def query(self, cmd: str, timeout: float = 1.0, expect: str = None) -> Optional[str]:
        """Send a command and return the firmware's one-line reply (None on failure).
        Never reassigns self.ser.timeout (Windows SetCommTimeouts mid-session
        produces dud reads); accumulates bytes against its own deadline."""
        with self._lock:
            if not self.ser or not self.ser.is_open:
                return None
            try:
                self.ser.reset_input_buffer()
                self.ser.write((cmd + "\n").encode())
                self.ser.flush()
                deadline = time.time() + timeout
                buf = b""
                while time.time() < deadline:
                    chunk = self.ser.read(self.ser.in_waiting or 1)
                    if not chunk:
                        continue
                    buf += chunk
                    while b"\n" in buf:
                        raw, buf = buf.split(b"\n", 1)
                        line = raw.decode(errors="ignore").strip()
                        if not line:
                            continue
                        if expect is None or line.startswith(expect):
                            return line
                return None
            except Exception as e:
                print(f"[arduino] Query error: {e}")
                self.connected = False
                return None

    # ── Rail: status ─────────────────────────────────────────────────────────

    def rail_status(self) -> dict:
        """Poll firmware position state (answered mid-move too)."""
        if not self.rail["homing"]:
            reply = self.query("POS?", expect="POS:")
            if reply:
                try:
                    pos, homed, stalled, length, moving = (int(x) for x in reply[4:].split(","))
                    self.rail.update({
                        "pos": pos, "homed": bool(homed), "stalled": bool(stalled),
                        "length": length, "moving": bool(moving),
                    })
                except ValueError:
                    pass
        p = self.prof()
        return {
            **self.rail,
            "connected": self.connected,
            "speed": p["speed"], "mode": p["mode"], "scurve": bool(p.get("scurve", True)),
            "locked": bool(self.calib.get("locked", True)),
            "mirror": bool(self.calib.get("mirror", False)),
            "rail_type": self.calib["active"],
            "end_margin": END_MARGIN,
            "max_rate": self.rate_for_delay(0),
        }

    # ── Rail: settings ───────────────────────────────────────────────────────

    def rail_set_speed(self, speed: int) -> int:
        speed = max(0, min(200, int(speed)))
        self.prof()["speed"] = speed
        self.save_calib()
        self.send(f"S{speed},10000")
        return speed

    def rail_set_mode(self, mode: str) -> bool:
        mode = mode.upper()
        if mode not in ("AUTO", "STEALTH", "SPREAD"):
            return False
        self.prof()["mode"] = mode
        self.save_calib()
        return self.send(f"TMODE:{mode}")

    def rail_set_scurve(self, on: bool) -> bool:
        self.prof()["scurve"] = bool(on)
        self.save_calib()
        return self.send("SCURVE1" if on else "SCURVE0")

    def rail_preset(self, name: str) -> Optional[dict]:
        pr = PRESETS.get(name)
        if not pr:
            return None
        self.rail_set_speed(pr["speed"])
        self.rail_set_mode(pr["mode"])
        return pr

    def rail_set_lock(self, locked: bool):
        self.calib["locked"] = bool(locked)
        self.save_calib()

    def rail_set_mirror(self, mirror: bool):
        self.calib["mirror"] = bool(mirror)
        self.save_calib()

    # ── Rail: marks ──────────────────────────────────────────────────────────

    def rail_mark_home(self) -> dict:
        if self.calib.get("locked", True):
            return {"success": False, "error": "marks are locked"}
        self.send("X"); time.sleep(0.3)
        r = self.query("ZERO", expect="OK:ZERO", timeout=1.5)
        return {"success": r is not None, "reply": r}

    def rail_mark_far(self) -> dict:
        if self.calib.get("locked", True):
            return {"success": False, "error": "marks are locked"}
        self.send("X"); time.sleep(0.3)
        st = self.rail_status()
        if not st.get("homed"):
            return {"success": False, "error": "mark home first"}
        pos = int(st["pos"])
        if pos <= 0:
            return {"success": False, "error": "far mark must be beyond home"}
        r = self.query(f"LEN{pos}", expect="OK:LEN", timeout=1.5)
        if r:
            self.prof()["length"] = pos
            self.save_calib()
        return {"success": r is not None, "length": pos}

    # ── Rail: motion ─────────────────────────────────────────────────────────

    @staticmethod
    def rate_for_delay(delay: int) -> float:
        """steps/s for a step delay (measured model: period = 2*delay + overhead)."""
        return 1e6 / (2 * max(0, delay) + STEP_OVERHEAD_US)

    def delay_for_seconds(self, steps: int, seconds: float) -> dict:
        """Step delay that makes `steps` take `seconds`, accounting for the
        S-curve ramps (they cost about one rampMs in total). Clamped 0..200."""
        p = self.prof()
        cruise = float(seconds) - (p["ramp"] / 1000.0 if p.get("scurve", True) else p["ramp"] / 2000.0)
        if steps <= 0:
            return {"delay": p["speed"], "ok": True}
        if cruise <= 0.05:
            return {"delay": 0, "ok": False, "reason": "too short for the ramps"}
        period_us = cruise * 1e6 / steps
        delay = (period_us - STEP_OVERHEAD_US) / 2.0
        if delay < 0:
            return {"delay": 0, "ok": False, "reason": "faster than the rail can go",
                    "min_seconds": round(steps / self.rate_for_delay(0) + p["ramp"] / 1000.0, 2)}
        return {"delay": int(min(200, round(delay))), "ok": True}

    def resolve_target(self, target) -> Optional[int]:
        """'home' | 'end' | int steps | {'percent': p} -> absolute steps (fenced)."""
        st = self.rail
        L = int(st["length"])
        if target == "home":
            return 0
        if target == "end":
            return max(0, L - END_MARGIN) if L > 0 else None
        if isinstance(target, dict) and "percent" in target:
            if L <= 0:
                return None
            pct = max(0.0, min(100.0, float(target["percent"])))
            return int(round(pct / 100.0 * max(0, L - END_MARGIN)))
        try:
            return max(0, int(target))
        except (TypeError, ValueError):
            return None

    def rail_go(self, target, speed: Optional[int] = None, seconds: Optional[float] = None) -> dict:
        """Fenced absolute move. speed = step delay for this move only (profile
        speed otherwise); seconds = fit the move to a duration instead."""
        st = self.rail_status()
        if not st.get("homed"):
            return {"success": False, "error": "no zero on the board - jog to the motor end and mark home"}
        tgt = self.resolve_target(target)
        if tgt is None:
            return {"success": False, "error": "no far mark yet" if target in ("end",) or isinstance(target, dict) else "bad target"}
        if st["length"] > 0:
            tgt = min(tgt, int(st["length"]))
        delay = self.speed if speed is None else max(0, min(200, int(speed)))
        fit = None
        if seconds is not None:
            fit = self.delay_for_seconds(abs(tgt - int(st["pos"])), float(seconds))
            delay = fit["delay"]
        self.send(f"S{delay},10000")
        ok = self.send(f"M{tgt}")
        return {"success": ok, "target": tgt, "delay": delay, "fit": fit}

    def rail_jog(self, direction: str) -> bool:
        """Timed jog in a raw firmware direction (U = toward home/motor, D = toward
        the far end). Uses the profile speed; ends on stop() or after 60 s."""
        d = direction.upper()
        if d not in ("U", "D"):
            return False
        self.send(f"S{self.speed},60000")
        return self.send(d)

    def rail_stop(self) -> bool:
        return self.send("X")

    # Backwards-compatible names used by older routes / timeline code
    def rail_goto(self, steps: int) -> bool:
        return self.rail_go(int(steps)).get("success", False)

    def rail_away(self) -> bool:      # legacy: "away" was U
        return self.rail_jog("U")

    def rail_toward(self) -> bool:    # legacy: "toward" was D
        return self.rail_jog("D")

    def rail_set_duration(self, duration: int):
        pass  # duration is fixed per jog/move now

    # ── Rail: StallGuard homing (kept for scripts; not in the UI) ────────────

    def rail_home(self, full: bool = False) -> bool:
        if self.rail["homing"]:
            return False
        threading.Thread(target=self._home_worker, args=(full,), daemon=True).start()
        return True

    def _home_worker(self, full: bool):
        self.rail["homing"] = True
        self.rail["last_msg"] = ""
        msg = ""
        try:
            with self._lock:
                if not self.ser or not self.ser.is_open:
                    msg = "ERR:NOT_CONNECTED"; return
                self.ser.reset_input_buffer()
                self.ser.write((("HF" if full else "H") + "\n").encode())
                self.ser.flush()
            deadline = time.time() + 300
            buf = b""
            while time.time() < deadline:
                lines = []
                with self._lock:
                    if not self.ser or not self.ser.is_open:
                        msg = "ERR:DISCONNECTED"; return
                    chunk = self.ser.read(self.ser.in_waiting or 1)
                    if chunk:
                        buf += chunk
                        while b"\n" in buf:
                            raw, buf = buf.split(b"\n", 1)
                            lines.append(raw.decode(errors="ignore").strip())
                for line in lines:
                    if line.startswith("HOMED") or line.startswith("ERR") or line == "STOPPED" or line.startswith("STALL"):
                        msg = line; break
                if msg:
                    break
                time.sleep(0.05)
            else:
                msg = "ERR:NO_REPLY"
        except Exception as e:
            msg = f"ERR:{e}"
        finally:
            self.rail["last_msg"] = msg
            self.rail["homing"] = False
        self.rail_status()

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
        self.gimbal_cam_us(1900)
        return True

    def gimbal_sweep_base(self) -> bool:
        return self.send("b")

    def gimbal_sweep_cam(self) -> bool:
        return self.send("c")

    def gimbal_sweep_both(self) -> bool:
        return self.send("a")

    # ── RGB Lights ───────────────────────────────────────────────────────────

    def rgb_color(self, light: int, r: int, g: int, b: int) -> bool:
        return self.send(f"RGB{light}:{r},{g},{b}")

    def rgb_effect(self, light: int, effect: str) -> bool:
        return self.send(f"EFFECT{light}:{effect.upper()}")

    def rgb_off(self, light: int) -> bool:
        return self.send(f"RGB{light}:0,0,0")
