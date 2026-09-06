"""
RAIL LAB — self-contained rail control + calibration bench.

Talks DIRECTLY to the Arduino on its COM port (no LCA server). Prototype for
what folds back into LCA's rail panel once proven.

Features:
  - Big KILL button (stops motion, DTR-resets the Mega).
  - Live position / homed / length / SG-load readout.
  - Nudge toward/away at a chosen speed; Stop.
  - Guided calibrate: HOME (away end) -> creep toward motor end -> you press
    "MARK END" when it's a few cm shy -> that step count becomes the length.

Coordinate convention (matches firmware): pos 0 = the homing hard stop
(direction set by HDIR), positions increase moving AWAY from it.

Rail types (Jul 17 2026):
  timer — the original belt rail. Hard stops both ends; home = U end;
          length calibrated at 124335 steps.
  rotor — leadscrew rail. ONE hard stop only: home there at reduced current
          (HCUR, anti-wedge) and NEVER stall-hunt the open end — the nut can
          run off the screw. Far end is fenced by a user-marked LEN.
"""

import json
import os
import threading
import time
import tkinter as tk

import serial
import serial.tools.list_ports

BAUD = 9600
HERE = os.path.dirname(os.path.abspath(__file__))
STATE_LOG = os.path.join(HERE, "rail_state.json")   # live state, I read this
CALIB = os.path.join(HERE, "rail_calib.json")       # persisted length
CMD_IN = os.path.join(HERE, "rail_cmd.json")        # command inbox (assistant -> rail)
CMD_OUT = os.path.join(HERE, "rail_reply.json")     # last command reply
SPEEDS = os.path.join(HERE, "rail_speeds.json")     # smart-test results
PROGRESS = os.path.join(HERE, "rail_progress.log")  # status mirror (assistant reads)

# Speed-finder ladder: step delay in µs, SLOW (safe) -> FAST. Lower = faster.
SPEED_LADDER = [130, 100, 78, 60, 46, 36, 28, 22, 17, 13]

# Per-rail homing defaults, sent to the firmware on connect / rail switch.
# hdir: which jog direction (U/D) creeps toward the homing hard stop.
# rotor starts with a tight hmax (unknown travel) + reduced homing current
# so a missed stall can't wedge the leadscrew; hdir is a guess until the
# first live jog confirms which way the hard stop is.
RAIL_PROFILES = {
    # homing profile + run settings; everything here is pushed to the board on connect
    # and saved back to rail_calib.json whenever a button/slider changes it.
    "timer": {"hdir": "U", "hspd": 150, "hmax": 200000, "hcur": 0,
              "cur": 1100, "ramp": 1000, "mode": "STEALTH", "speed": 60, "sgt": 80, "scurve": True},
    "rotor": {"hdir": "U", "hspd": 150, "hmax": 60000,  "hcur": 600,
              "cur": 1200, "ramp": 2000, "mode": "STEALTH", "speed": 60, "sgt": 80, "scurve": True},
}
# Speed map measured with the camera (Sep 5 2026): firmware tops out ~12k steps/s, so
# S10..S30 are the same speed; S40-S50 sit on the rig's resonance; S60+ is quiet.
SWAP_TXT    = "SWAP" + chr(10) + "sides"
SWAPPED_TXT = "SWAPPED" + chr(10) + "(end on left)"
PRESET_SLOW = {"speed": 60, "mode": "STEALTH"}
PRESET_FAST = {"speed": 10, "mode": "SPREAD"}


def find_port():
    for p in serial.tools.list_ports.comports():
        d = (p.description or "")
        if any(k in d for k in ("Arduino", "CH340", "USB Serial", "Mega")):
            return p.device
    return None


class Rail:
    def __init__(self):
        self.ser = None
        self.lock = threading.Lock()
        self.port = None
        self.preserved = False

    def open(self):
        self.port = find_port()
        if not self.port:
            return False
        # Open WITHOUT asserting DTR: the Mega only auto-resets on a DTR edge, so
        # this keeps position / homed / LEN marks alive across a Rail Lab relaunch.
        self.ser = serial.Serial()
        self.ser.port = self.port
        self.ser.baudrate = BAUD
        self.ser.timeout = 0.05
        self.ser.dtr = False
        self.ser.rts = False
        self.ser.open()
        time.sleep(0.3)
        self.ser.reset_input_buffer()
        if self.query("P", "PONG", timeout=1.0):
            self.preserved = True          # board state survived
            return True
        # no answer -> fall back to a real reset (fresh boot)
        self.preserved = False
        self.reset_hard()
        time.sleep(2.5)
        self.ser.reset_input_buffer()
        return True

    def send(self, cmd):
        with self.lock:
            if not self.ser:
                return
            self.ser.write((cmd + "\n").encode())
            self.ser.flush()

    def query(self, cmd, expect, timeout=1.0):
        with self.lock:
            if not self.ser:
                return None
            self.ser.reset_input_buffer()
            self.ser.write((cmd + "\n").encode())
            self.ser.flush()
            end = time.time() + timeout
            buf = b""
            while time.time() < end:
                chunk = self.ser.read(self.ser.in_waiting or 1)
                if not chunk:
                    continue
                buf += chunk
                while b"\n" in buf:
                    raw, buf = buf.split(b"\n", 1)
                    line = raw.decode(errors="ignore").strip()
                    if line.startswith(expect):
                        return line
            return None

    def long_query(self, cmd, expect, timeout=90.0):
        """For slow firmware ops (H). Holds the lock only per read so the poll,
        STOP and every other button keep working while we wait for a verdict."""
        with self.lock:
            if not self.ser:
                return None
            self.ser.reset_input_buffer()
            self.ser.write((cmd + "\n").encode())
            self.ser.flush()
        end = time.time() + timeout
        buf = b""
        while time.time() < end:
            with self.lock:
                if not self.ser:
                    return None
                chunk = self.ser.read(self.ser.in_waiting or 1)
            if not chunk:
                time.sleep(0.02)
                continue
            buf += chunk
            while b"\n" in buf:
                raw, buf = buf.split(b"\n", 1)
                line = raw.decode(errors="ignore").strip()
                if line.startswith(expect) or line.startswith(("ERR", "STALL", "STOPPED", "TMC:NOT")):
                    return line
        return None

    def reset_hard(self):
        """Toggle DTR to hardware-reset the Mega -> motor halts instantly."""
        try:
            if self.ser:
                self.ser.setDTR(False)
                time.sleep(0.1)
                self.ser.setDTR(True)
        except Exception:
            pass


class App:
    def __init__(self, root):
        self.root = root
        self.rail = Rail()
        root.title("RAIL LAB")
        root.configure(bg="#161616")
        root.geometry("560x700+40+40")
        root.attributes("-topmost", True)

        # ---- KILL ----
        self.kill_btn = tk.Button(
            root, text="☠  KILL  ☠", font=("Segoe UI", 20, "bold"),
            bg="#cc1111", fg="white", activebackground="#ff2222",
            activeforeground="white", bd=5, cursor="hand2", command=self.kill)
        self.kill_btn.pack(fill="x", padx=12, pady=(12, 8))

        # ---- live readout ----
        self.readout = tk.Label(root, text="connecting...", font=("Consolas", 13),
                                bg="#161616", fg="#33dd88", justify="left", anchor="w")
        self.readout.pack(fill="x", padx=14, pady=4)

        # ---- rail type ----
        rt = tk.Frame(root, bg="#161616")
        rt.pack(fill="x", padx=14, pady=(2, 0))
        tk.Label(rt, text="rail:", bg="#161616", fg="#aaa",
                 font=("Segoe UI", 9)).pack(side="left")
        self.rail_lbl = tk.Label(rt, text="—", bg="#161616", fg="#ffb74d",
                                 font=("Segoe UI", 9, "bold"))
        self.rail_lbl.pack(side="right")
        self._mk(rt, "TIMER\n(belt)", "#455a64",
                 lambda: self.set_rail("timer")).pack(side="left", expand=True, fill="x", padx=2)
        self._mk(rt, "ROTOR\n(leadscrew)", "#8d6e63",
                 lambda: self.set_rail("rotor")).pack(side="left", expand=True, fill="x", padx=2)
        self._mk(rt, "flip home\ndir (U/D)", "#555",
                 self.toggle_hdir).pack(side="left", expand=True, fill="x", padx=2)

        # ---- mode ----
        mo = tk.Frame(root, bg="#161616")
        mo.pack(fill="x", padx=14, pady=(2, 4))
        tk.Label(mo, text="motor mode:", bg="#161616", fg="#aaa",
                 font=("Segoe UI", 9)).pack(side="left")
        self.mode_lbl = tk.Label(mo, text="STEALTH", bg="#161616", fg="#33dd88",
                                 font=("Segoe UI", 9, "bold"))
        self.mode_lbl.pack(side="right")
        self._mk(mo, "quiet\n(stealth)", "#2e7d32",
                 lambda: self.set_mode("STEALTH")).pack(side="left", expand=True, fill="x", padx=2)
        self._mk(mo, "STRONG\n(spread)", "#c62828",
                 lambda: self.set_mode("SPREAD")).pack(side="left", expand=True, fill="x", padx=2)
        self._mk(mo, "auto", "#555",
                 lambda: self.set_mode("AUTO")).pack(side="left", expand=True, fill="x", padx=2)

        # ---- speed ----
        sf = tk.Frame(root, bg="#161616")
        sf.pack(fill="x", padx=14, pady=6)
        tk.Label(sf, text="speed delay (µs, lower=faster)", bg="#161616",
                 fg="#aaa", font=("Segoe UI", 9)).pack(anchor="w")
        self.speed = tk.Scale(sf, from_=0, to=200, orient="horizontal",
                              bg="#161616", fg="#ddd", troughcolor="#333",
                              highlightthickness=0)
        self.speed.set(60)
        self.speed.pack(fill="x")
        self.speed.bind("<ButtonRelease-1>", lambda e: self.save_speed())
        pf = tk.Frame(sf, bg="#161616")
        pf.pack(fill="x", pady=(2, 0))
        self._mk(pf, "SLOW 60\n(quiet)", "#2e7d32",
                 lambda: self.preset(PRESET_SLOW)).pack(side="left", expand=True, fill="x", padx=2)
        self._mk(pf, "FAST 10\n(strong)", "#ad1457",
                 lambda: self.preset(PRESET_FAST)).pack(side="left", expand=True, fill="x", padx=2)
        tk.Label(pf, text="avoid 40-50: resonance", bg="#161616", fg="#777",
                 font=("Segoe UI", 8)).pack(side="left", padx=6)
        self.scurve_var = tk.BooleanVar(value=True)
        tk.Checkbutton(sf, text="S-curve ramps  (smooth start + stop, no end jolt)",
                       variable=self.scurve_var, command=self.toggle_scurve,
                       bg="#161616", fg="#ddd", selectcolor="#333", activebackground="#161616",
                       activeforeground="#ddd", font=("Segoe UI", 9)).pack(anchor="w", pady=(4, 0))

        # ---- jog (U/D are raw firmware directions; home side = the HDIR one) ----
        jf = tk.Frame(root, bg="#161616")
        jf.pack(fill="x", padx=14, pady=6)
        self.jog_u_btn = self._mk(jf, "◄ jog U", "#c8a96e", lambda: self.jog("U"))
        self.stop_btn  = self._mk(jf, "STOP", "#e08000", self.stop)
        self.jog_d_btn = self._mk(jf, "jog D ►", "#2e7d32", lambda: self.jog("D"))
        self.swap_btn  = self._mk(jf, "⇄ SWAP\nsides", "#37474f", self.toggle_mirror)
        self.jog_row = [self.jog_u_btn, self.stop_btn, self.jog_d_btn]

        # ---- calibrate ----
        cf = tk.Frame(root, bg="#1f1f1f", bd=1, relief="groove")
        cf.pack(fill="x", padx=14, pady=10)
        tk.Label(cf, text="CALIBRATE  (jog to an end, then mark it)",
                 bg="#1f1f1f", fg="#66ccff",
                 font=("Segoe UI", 10, "bold")).pack(anchor="w", padx=8, pady=(6, 2))
        rf = tk.Frame(cf, bg="#1f1f1f")
        rf.pack(fill="x", padx=8, pady=3)
        self.go_home_btn = self._mk(rf, "GO HOME", "#1565c0", lambda: self.rush(0))
        self.go_end_btn  = self._mk(rf, "GO END", "#6a1b9a", lambda: self.rush(None))
        self.go_row = [self.go_home_btn, self.go_end_btn]
        mf = tk.Frame(cf, bg="#1f1f1f")
        mf.pack(fill="x", padx=8, pady=(3, 8))
        self.mark_home_btn = self._mk(mf, "MARK HOME\n(zero here)", "#c8a96e", self.mark_home)
        self.mark_far_btn  = self._mk(mf, "MARK FAR END\n(set as length)", "#6a1b9a", self.mark_far)
        self.mark_row = [self.mark_home_btn, self.mark_far_btn]
        self.lock_btn = self._mk(cf, "", "#555", self.toggle_lock)
        self.lock_btn.pack(fill="x", padx=8, pady=(0, 8))

        # ---- smart speed test ----
        self.smart_btn = self._mk(root, "🏁 SMART SPEED TEST (find max safe speed)",
                                  "#00695c", self.smart_test)
        # SMART SPEED TEST retired (Sep 5 2026): button not shown; inbox SMARTTEST still works.

        self.status = tk.Label(root, text="", bg="#161616", fg="#ccc",
                               font=("Segoe UI", 9), wraplength=520, justify="left")
        self.status.pack(fill="x", padx=14, pady=4)

        self.state = {}
        # Adopt whatever id is already sitting in the inbox so a fresh launch never
        # replays the previous session's last command (an old H/M/D fired on start).
        try:
            with open(CMD_IN) as f:
                self.last_cmd_id = json.load(f).get("id")
        except Exception:
            self.last_cmd_id = None
        self.abort = False       # set by KILL — the finder checks this between moves
        self.testing = False
        self.calib = self.load_calib()
        self.mirror = bool(self.calib.get("mirror", False))
        self.locked = bool(self.calib.get("locked", True))   # locked by default
        self.layout_rows()
        self.render_lock()
        if self.rail.open():
            self.set_status(f"connected on {self.rail.port}")
            self.apply_profile()
        else:
            self.set_status("NO ARDUINO FOUND — is the LCA server holding the port?")
        self.poll()

    def layout_rows(self):
        """Pack the jog row and the mark row left->right, or mirrored when
        self.mirror is set, so the on-screen left button matches whichever
        physical side the user is standing on. U/D commands never change -
        only where the buttons sit and which way the arrows point."""
        for w in self.jog_row + [self.swap_btn] + self.mark_row + self.go_row:
            w.pack_forget()
        jog = list(reversed(self.jog_row)) if self.mirror else list(self.jog_row)
        for w in jog:
            w.pack(side="left", expand=True, fill="x", padx=3)
        self.swap_btn.pack(side="left", fill="x", padx=3)
        for w in (reversed(self.mark_row) if self.mirror else self.mark_row):
            w.pack(side="left", expand=True, fill="x", padx=3)
        for w in (reversed(self.go_row) if self.mirror else self.go_row):
            w.pack(side="left", expand=True, fill="x", padx=3)
        if self.mirror:
            self.jog_u_btn.config(text="jog U (home) >")
            self.jog_d_btn.config(text="< jog D (end)")
            self.go_home_btn.config(text="GO HOME >")
            self.go_end_btn.config(text="< GO END")
            self.swap_btn.config(bg="#00838f", text=SWAPPED_TXT)
        else:
            self.jog_u_btn.config(text="< jog U (home)")
            self.jog_d_btn.config(text="jog D (end) >")
            self.go_home_btn.config(text="< GO HOME")
            self.go_end_btn.config(text="GO END >")
            self.swap_btn.config(bg="#37474f", text=SWAP_TXT)

    def render_lock(self):
        st = "disabled" if self.locked else "normal"
        for b in self.mark_row:
            b.config(state=st, bg=("#3a3a3a" if self.locked else None) or b.cget("bg"))
        self.mark_home_btn.config(bg="#3a3a3a" if self.locked else "#c8a96e")
        self.mark_far_btn.config(bg="#3a3a3a" if self.locked else "#6a1b9a")
        self.lock_btn.config(text=("LOCKED - marks protected (click to unlock)" if self.locked
                                   else "UNLOCKED - marking allowed (click to lock)"),
                             bg="#b71c1c" if self.locked else "#2e7d32")

    def toggle_lock(self):
        self.locked = not self.locked
        self.calib["locked"] = self.locked
        self.save_calib()
        self.render_lock()
        self.set_status("marks LOCKED - MARK HOME / MARK FAR END disabled" if self.locked
                        else "marks UNLOCKED - be sure before you press")

    def toggle_mirror(self):
        self.mirror = not self.mirror
        self.calib["mirror"] = self.mirror
        self.save_calib()
        self.layout_rows()
        self.set_status("button sides SWAPPED (D/far on the left)" if self.mirror
                        else "button sides normal (U/home on the left)")

    def load_calib(self):
        """Full calib dict {active, rails:{type:{length,hdir,hspd,hmax,hcur}}}.
        Migrates the legacy single-rail {"length": N} file (that was the timer rail)."""
        try:
            with open(CALIB) as f:
                d = json.load(f)
        except Exception:
            d = {}
        if "rails" not in d:
            d = {"active": "timer",
                 "rails": {"timer": {"length": int(d.get("length", 0) or 0)}}}
        d.setdefault("active", "timer")
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
        except Exception:
            pass

    def prof(self):
        """Active rail's profile dict (mutable — edit then save_calib())."""
        return self.calib["rails"][self.calib["active"]]

    def apply_profile(self, force=False):
        """Push the active rail's homing profile + length fence to the firmware.
        If the board is still alive and homed (relaunch without reset), its zero and
        LEN are the truth: adopt its length into the profile and do NOT send HDIR
        (which clears homed) or LEN. force=True (explicit rail switch) pushes all."""
        p = self.prof()
        live = None
        if not force:
            r = self.rail.query("POS?", "POS:", timeout=1.0)
            if r:
                f = r[4:].split(",")
                if len(f) >= 5 and f[1] == "1":
                    live = f
        if live:
            L = int(live[3])
            if L > 0:
                p["length"] = L
                self.save_calib()
            self.rail.send(f"HSPD{p['hspd']}")
            self.rail.send(f"HMAX{p['hmax']}")
            self.rail.send(f"HCUR{p['hcur']}")
            self.apply_run_settings(p)
            self.rail_lbl.config(text=f"{self.calib['active'].upper()}  home={p['hdir']}")
            self.set_status(f"board still homed - kept its zero, adopted len={p['length']} "
                            f"into {self.calib['active']} profile; run: {p['mode']} S{p['speed']} "
                            f"{p['cur']}mA ramp{p['ramp']}")
            return
        self.rail.send(f"HDIR:{p['hdir']}")
        self.rail.send(f"HSPD{p['hspd']}")
        self.rail.send(f"HMAX{p['hmax']}")
        self.rail.send(f"HCUR{p['hcur']}")
        self.rail.send(f"LEN{p['length']}")
        self.apply_run_settings(p)
        self.rail_lbl.config(text=f"{self.calib['active'].upper()}  home={p['hdir']}")
        self.set_status(f"rail={self.calib['active']}  hdir={p['hdir']} "
                        f"hspd={p['hspd']} hmax={p['hmax']} hcur={p['hcur']} "
                        f"len={p['length']}")

    def set_rail(self, rail_type):
        self.calib["active"] = rail_type
        self.save_calib()
        self.apply_profile(force=True)

    def toggle_hdir(self):
        p = self.prof()
        p["hdir"] = "D" if p["hdir"] == "U" else "U"
        self.save_calib()
        self.apply_profile()
        self.set_status(f"homing direction -> {p['hdir']}  (re-home required)")

    def _mk(self, parent, text, color, cmd):
        return tk.Button(parent, text=text, bg=color, fg="white",
                         font=("Segoe UI", 10, "bold"), bd=2, cursor="hand2",
                         activebackground="#555", command=cmd)

    def set_status(self, msg):
        self.status.config(text=msg)
        try:
            with open(PROGRESS, "a") as f:
                f.write(f"{time.time():.0f} {msg}\n")
        except Exception:
            pass

    # ---- actions ----
    def kill(self):
        self.abort = True        # halt any running smart test
        def w():
            self.rail.send("X")
            self.rail.reset_hard()
            self.set_status("KILLED — motor halted, Mega reset, test aborted")
        threading.Thread(target=w, daemon=True).start()

    def stop(self):
        self.rail.send("X")

    def apply_run_settings(self, p):
        """Motor current, ramp, StallGuard threshold, chopper mode and speed slider
        from the profile -> board + UI. None of these touch zero/homed/LEN."""
        self.rail.send(f"CUR{p['cur']}")
        self.rail.send(f"RAMP{p['ramp']}")
        self.rail.send(f"SGT{p['sgt']}")
        self.rail.send(f"TMODE:{p['mode']}")
        self.rail.send("SCURVE1" if p.get("scurve", True) else "SCURVE0")
        self.scurve_var.set(bool(p.get("scurve", True)))
        self.mode_lbl.config(text=p['mode'])
        self.speed.set(p['speed'])

    def toggle_scurve(self):
        on = bool(self.scurve_var.get())
        self.prof()["scurve"] = on
        self.save_calib()
        threading.Thread(target=self.rail.send, args=("SCURVE1" if on else "SCURVE0",), daemon=True).start()
        self.set_status("S-curve ramps ON (smooth start/stop)" if on else "S-curve ramps OFF (legacy linear ramp, dead stop)")

    def save_speed(self):
        p = self.prof()
        p["speed"] = int(self.speed.get())
        self.save_calib()

    def preset(self, pr):
        self.speed.set(pr["speed"])
        self.save_speed()
        self.set_mode(pr["mode"])
        self.set_status(f"preset: S{pr['speed']} {pr['mode']}  (saved)")

    def set_mode(self, mode):
        threading.Thread(target=self.rail.send, args=(f"TMODE:{mode}",), daemon=True).start()
        self.mode_lbl.config(text=mode)
        self.prof()["mode"] = mode
        self.save_calib()
        self.set_status(f"motor mode -> {mode}"
                        + ("  (fast, but NO stall detection)" if mode == "SPREAD" else ""))

    def jog(self, direction):
        sp = self.speed.get()
        hdir = self.prof()["hdir"]
        side = "toward HOME stop" if direction == hdir else "toward FAR end"
        self.rail.send(f"S{sp},60000")   # long duration; user stops manually
        self.rail.send(direction)
        self.set_status(f"jogging {direction} ({side}) @ {sp}µs")

    def home(self):
        def w():
            p = self.prof()
            self.set_status(f"homing into hard stop ({p['hdir']} dir, "
                            f"{p['hcur'] or 'full'} mA)... KILL if it grinds")
            r = self.rail.long_query("H", "HOMED", timeout=90)
            hint = f"  (creep cap is {p['hmax']} steps - jog closer first)" if r and r.startswith("ERR") else ""
            self.set_status(f"HOME result: {r}{hint}" if r else "HOME: no verdict (timeout)")
        threading.Thread(target=w, daemon=True).start()

    RUSH_MARGIN = 1000   # steps kept back from the far mark on RUSH END

    def rush(self, target):
        """Fenced absolute move at the CURRENT slider speed and current mode.
        target None = far mark minus margin. Needs a zero (MARK HOME) first."""
        def w():
            r = self.rail.query("POS?", "POS:", timeout=1.0)
            f = r[4:].split(",") if r else []
            if len(f) < 5 or f[1] != "1":
                self.set_status("GO: no zero on the board - jog to the motor end and MARK HOME first")
                return
            length = int(f[3])
            if target is None and length <= 0:
                self.set_status("GO END: no far mark - MARK FAR END first")
                return
            tgt = target if target is not None else max(0, length - self.RUSH_MARGIN)
            sp = int(self.speed.get())
            self.rail.send(f"S{sp},10000")
            self.rail.send(f"M{tgt}")
            self.set_status(f"GO -> {tgt} at S{sp} ({self.mode_lbl.cget('text')})")
        threading.Thread(target=w, daemon=True).start()

    def mark_home(self):
        if self.locked:
            self.set_status("marks are LOCKED - unlock first"); return
        def w():
            self.rail.send("X")
            time.sleep(0.3)
            r = self.rail.query("ZERO", "OK:ZERO", timeout=1.5)
            self.set_status("HOME = zero (position reset here)"
                            if r else "MARK HOME: no ack")
        threading.Thread(target=w, daemon=True).start()

    def mark_far(self):
        if self.locked:
            self.set_status("marks are LOCKED - unlock first"); return
        def w():
            self.rail.send("X")
            time.sleep(0.3)
            r = self.rail.query("POS?", "POS:", timeout=1.5)
            if not r:
                self.set_status("MARK FAR END: couldn't read position")
                return
            pos = abs(int(r[4:].split(",")[0]))
            self.rail.send(f"LEN{pos}")
            self.prof()["length"] = pos
            self.save_calib()
            self.set_status(f"FAR END = length {pos} steps "
                            f"(fence set + saved for {self.calib['active']})")
        threading.Thread(target=w, daemon=True).start()

    def smart_test(self):
        if self.testing:
            return
        threading.Thread(target=self._smart_worker, daemon=True).start()

    def _home_blocking(self):
        r = self.rail.query("H", "HOMED", timeout=90)
        return r is not None and r.startswith("HOMED")

    def _roundtrip(self, speed, lo, hi):
        """One end-to-end round trip at `speed`. Returns 'OK', 'STALL', or 'ABORT'.
        Speed is set only at rest; each M is blocking; nothing sent mid-move."""
        for target in (hi, lo):
            if self.abort:
                return "ABORT"
            self.rail.query(f"S{speed},60000", "OK", timeout=3)   # at rest
            r = self.rail.query(f"M{target}", "OK POS", timeout=60)
            if self.abort:
                return "ABORT"
            if r is None or r.startswith("STALL"):
                return "STALL"
        return "OK"

    def _smart_worker(self):
        self.testing = True
        self.abort = False
        self.smart_btn.config(state="disabled")
        best = None
        try:
            # Traverse band comes from the active rail's calibrated length —
            # a hardcoded band sized for the timer rail could overdrive the
            # rotor rail's open end.
            length = int(self.prof().get("length", 0))
            if length < 20000:
                self.set_status("SMART TEST: calibrate this rail's length first "
                                f"(len={length})")
                return
            lo, hi = max(2000, length // 20), int(length * 0.85)
            self.set_status("SMART TEST: homing first...")
            if not self._home_blocking():
                self.set_status("SMART TEST: home failed — aborting")
                return
            for speed in SPEED_LADDER:          # slow -> fast
                if self.abort:
                    self.set_status("SMART TEST: aborted by KILL")
                    return
                self.set_status(f"SMART TEST: trying delay {speed}µs "
                                f"(best so far {best or '—'})...")
                res = self._roundtrip(speed, lo, hi)
                if res == "ABORT":
                    self.set_status("SMART TEST: aborted by KILL")
                    return
                if res == "OK":
                    best = speed                # faster; keep going
                    continue
                # STALL — this speed is too fast; previous `best` is the limit
                self.set_status(f"SMART TEST: jammed at {speed}µs — re-homing...")
                self._home_blocking()           # stall lost position
                break
            if best is None:
                self.set_status("SMART TEST: even the slowest speed jammed?! check rig")
                return
            # safety margin: recommend ~15% slower than the fastest that passed
            safe = int(best * 1.15)
            fast, med, slow = best, int(best * 1.6), int(best * 2.6)
            json.dump({"max_clean": best, "safe": safe,
                       "fast": fast, "med": med, "slow": slow},
                      open(SPEEDS, "w"))
            self.set_status(f"SMART TEST DONE ✓  max clean delay {best}µs "
                            f"(safe cruise {safe}µs). Saved presets.")
        finally:
            self.testing = False
            self.abort = False
            self.smart_btn.config(state="normal")

    def drain_inbox(self):
        """Execute one queued command from the assistant, if new. Format:
        {"id": <n>, "cmd": "M9000", "expect": "OK POS", "timeout": 20}
        expect optional (query vs fire-and-forget)."""
        try:
            with open(CMD_IN) as f:
                c = json.load(f)
        except Exception:
            return
        if not c or c.get("id") == self.last_cmd_id:
            return
        self.last_cmd_id = c.get("id")
        cmd = c.get("cmd", "")
        if not cmd:
            return
        if cmd == "SMARTTEST":          # trigger the finder (runs in its own thread)
            self.smart_test()
            try:
                json.dump({"id": self.last_cmd_id, "cmd": cmd, "reply": "started"},
                          open(CMD_OUT, "w"))
            except Exception:
                pass
            return
        self.set_status(f"[remote] {cmd}")
        if c.get("expect"):
            reply = self.rail.query(cmd, c["expect"], timeout=float(c.get("timeout", 5)))
        else:
            self.rail.send(cmd)
            reply = "sent"
        try:
            with open(CMD_OUT, "w") as f:
                json.dump({"id": self.last_cmd_id, "cmd": cmd,
                           "reply": reply, "t": time.time()}, f)
        except Exception:
            pass

    def poll(self):
        def w():
            try:
                self._poll_body()
            except Exception as e:
                self.status.config(text=f"poll error: {e!r}")
            finally:
                self.root.after(600, self.poll)
        threading.Thread(target=w, daemon=True).start()

    def _poll_body(self):
        if True:
            self.drain_inbox()
            r = self.rail.query("POS?", "POS:", timeout=0.8)
            g = self.rail.query("SG?", "SG:", timeout=0.5)
            if r and len(r[4:].split(",")) >= 5:
                p = r[4:].split(",")
                sg = g[3:] if g else "?"
                txt = (f"pos    {p[0]:>8}\n"
                       f"homed  {'YES' if p[1]=='1' else 'no'}    "
                       f"moving {'YES' if p[4]=='1' else 'no'}\n"
                       f"length {p[3]:>8}\n"
                       f"load   {sg:>8}  (low=working hard)")
                self.readout.config(text=txt)
                try:
                    with open(STATE_LOG, "w") as f:
                        json.dump({"pos": int(p[0]), "homed": p[1] == "1",
                                   "stalled": p[2] == "1", "length": int(p[3]),
                                   "moving": p[4] == "1",
                                   "load": int(sg) if sg.isdigit() else None,
                                   "t": time.time()}, f)
                except Exception:
                    pass


if __name__ == "__main__":
    r = tk.Tk()
    App(r)
    r.mainloop()
