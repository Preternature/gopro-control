"""
RAIL KILL SWITCH — self-contained emergency stop.

Run:  python kill_switch.py

One giant red button. Clicking it:
  1. Sends an immediate stop (X) through the running server, if any.
  2. Force-kills every python process running main.py (the LCA server) so
     nothing can send further motion commands.
  3. Opens the Arduino's COM port directly — the DTR toggle hardware-resets
     the Mega, which halts the step generator dead — then sends X for good
     measure and closes.

Safe to press repeatedly. Window stays on top.
"""

import os
import subprocess
import threading
import time
import tkinter as tk
import urllib.request


def http_stop():
    try:
        req = urllib.request.Request(
            "http://127.0.0.1:5000/api/arduino/rail/stop", data=b"{}",
            headers={"Content-Type": "application/json"}, method="POST")
        urllib.request.urlopen(req, timeout=1.5)
        return "sent stop via server"
    except Exception:
        return "server unreachable (ok)"


def kill_all_python():
    """Kill EVERY other python process — not just main.py. Anything could be
    driving the motor (test scripts, probes), and killing them also frees the
    COM port so the hardware reset below can get in."""
    me = os.getpid()
    try:
        ps = (
            "Get-CimInstance Win32_Process | "
            "Where-Object { $_.Name -match '^python' -and $_.ProcessId -ne %d } | "
            "ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }"
        ) % me
        subprocess.run(["powershell", "-NoProfile", "-Command", ps],
                       capture_output=True, timeout=15)
        return "killed all python"
    except Exception as e:
        return f"python kill failed: {e}"


def reset_arduino():
    """DTR-reset every Arduino-ish COM port. Retries — the port may take a
    moment to free after its holder was killed."""
    try:
        import serial
        import serial.tools.list_ports
    except ImportError:
        return "pyserial missing — skipped hardware reset"
    hits = []
    for p in serial.tools.list_ports.comports():
        desc = p.description or ""
        if any(k in desc for k in ("Arduino", "CH340", "USB Serial", "Mega")):
            for attempt in range(6):
                try:
                    # Opening the port toggles DTR -> hardware reset -> motor halts
                    s = serial.Serial(p.device, 9600, timeout=0.5)
                    time.sleep(0.3)
                    for _ in range(3):
                        s.write(b"X\n")
                        time.sleep(0.1)
                    s.close()
                    hits.append(p.device)
                    break
                except Exception:
                    time.sleep(0.5)  # port still freeing up — retry
    return f"HARDWARE RESET {', '.join(hits)}" if hits else "NO PORT OPENED — pull motor power!"


def do_kill(status, button):
    button.config(state="disabled", text="KILLING...")
    status.set("working...")

    def worker():
        lines = [http_stop(), kill_all_python()]
        time.sleep(0.7)  # let the killed process release the COM port
        lines.append(reset_arduino())
        status.set(" | ".join(lines))
        button.config(state="normal", text="☠  KILL RAIL  ☠")

    threading.Thread(target=worker, daemon=True).start()


def main():
    root = tk.Tk()
    root.title("RAIL KILL SWITCH")
    root.attributes("-topmost", True)
    root.configure(bg="#1a1a1a")
    root.geometry("360x220+60+60")

    status = tk.StringVar(value="armed — click to kill all rail control")

    button = tk.Button(
        root, text="☠  KILL RAIL  ☠",
        font=("Segoe UI", 22, "bold"),
        bg="#cc1111", fg="white", activebackground="#ff2222",
        activeforeground="white", relief="raised", bd=6, cursor="hand2")
    button.config(command=lambda: do_kill(status, button))
    button.pack(fill="both", expand=True, padx=16, pady=(16, 8))

    tk.Label(root, textvariable=status, bg="#1a1a1a", fg="#aaaaaa",
             font=("Segoe UI", 9), wraplength=330).pack(fill="x", padx=10, pady=(0, 10))

    root.mainloop()


if __name__ == "__main__":
    main()
