"""
GoPro Controller - Native Tkinter App with External VLC
"""

import tkinter as tk
from tkinter import ttk, messagebox
import subprocess
import threading
import requests
import time
import asyncio
from bleak import BleakScanner, BleakClient

class GoProGUI:
    GOPRO_IP = "10.5.5.9"
    GOPRO_PORT = 8080
    GOPRO_SSID = "GP25102353"
    WIFI_INTERFACE = "Wi-Fi 6"  # TP-Link adapter for GoPro
    BLE_COMMAND_UUID = "b5f90072-aa8d-11e3-9046-0002a5d5c51b"

    def __init__(self, root):
        self.root = root
        self.root.title("GoPro Controller")
        self.root.geometry("500x300")

        self.connected = False
        self.streaming = False
        self.vlc_process = None
        self.shutdown = False  # Flag to stop background threads

        self.setup_ui()

        # Auto-connect on startup
        self.root.after(500, self.full_connect)

    def setup_ui(self):
        main_frame = ttk.Frame(self.root, padding="10")
        main_frame.pack(fill=tk.BOTH, expand=True)

        # Status
        self.status_label = ttk.Label(main_frame, text="Starting...", foreground="orange")
        self.status_label.pack(pady=(0, 10))

        # Connection buttons
        conn_frame = ttk.Frame(main_frame)
        conn_frame.pack(fill=tk.X, pady=5)
        ttk.Button(conn_frame, text="Full Connect", command=self.full_connect).pack(side=tk.LEFT, padx=5)

        # Preview
        preview_frame = ttk.Frame(main_frame)
        preview_frame.pack(fill=tk.X, pady=5)
        self.btn_preview = ttk.Button(preview_frame, text="Start Preview (VLC)", command=self.start_preview)
        self.btn_preview.pack(side=tk.LEFT, padx=5)
        ttk.Button(preview_frame, text="Stop Preview", command=self.stop_preview).pack(side=tk.LEFT, padx=5)

        # Controls
        ctrl_frame = ttk.Frame(main_frame)
        ctrl_frame.pack(fill=tk.X, pady=5)
        ttk.Button(ctrl_frame, text="Take Photo", command=self.take_photo).pack(side=tk.LEFT, padx=5)
        ttk.Button(ctrl_frame, text="Start Video", command=self.start_video).pack(side=tk.LEFT, padx=5)
        ttk.Button(ctrl_frame, text="Stop Video", command=self.stop_video).pack(side=tk.LEFT, padx=5)

        self.root.protocol("WM_DELETE_WINDOW", self.on_close)

    def full_connect(self):
        """BLE wake + WiFi connect with auto-retry"""
        def connect():
            while not self.shutdown:
                try:
                    self.root.after(0, lambda: self.status_label.config(text="Scanning for GoPro (BLE)...", foreground="orange"))

                    # BLE scan
                    loop = asyncio.new_event_loop()
                    asyncio.set_event_loop(loop)
                    devices = loop.run_until_complete(BleakScanner.discover(timeout=5.0))

                    gopro = None
                    for d in devices:
                        if d.name and "GoPro" in d.name:
                            gopro = d
                            break

                    if gopro:
                        self.root.after(0, lambda: self.status_label.config(text=f"Found {gopro.name}, waking WiFi...", foreground="orange"))

                        # Send WiFi enable
                        async def wake():
                            client = BleakClient(gopro.address)
                            await client.connect()
                            cmd = bytes([0x03, 0x17, 0x01, 0x01])
                            await client.write_gatt_char(self.BLE_COMMAND_UUID, cmd, response=False)
                            await asyncio.sleep(2)
                            await client.disconnect()

                        loop.run_until_complete(wake())
                        loop.close()
                        time.sleep(1)

                    # Connect WiFi adapter to GoPro
                    self.root.after(0, lambda: self.status_label.config(text="Connecting WiFi adapter...", foreground="orange"))
                    subprocess.run(["netsh", "wlan", "connect", f"name={self.GOPRO_SSID}", f"interface={self.WIFI_INTERFACE}"],
                                  capture_output=True, timeout=10)
                    time.sleep(3)

                    # Check connection
                    try:
                        response = requests.get(f"http://{self.GOPRO_IP}:{self.GOPRO_PORT}/gopro/camera/state", timeout=3)
                        if response.status_code == 200:
                            self.connected = True
                            self.root.after(0, lambda: self.status_label.config(text="Connected", foreground="green"))
                            break  # Success, exit loop
                        else:
                            raise Exception("Connection check failed")
                    except:
                        raise Exception("Connection check failed")

                except Exception as e:
                    self.root.after(0, lambda: self.status_label.config(text=f"Retrying connection...", foreground="orange"))
                    time.sleep(2)  # Wait before retry

        threading.Thread(target=connect, daemon=True).start()

    def check_connection(self):
        def check():
            try:
                response = requests.get(f"http://{self.GOPRO_IP}:{self.GOPRO_PORT}/gopro/camera/state", timeout=3)
                if response.status_code == 200:
                    self.connected = True
                    self.root.after(0, lambda: self.status_label.config(text="Connected", foreground="green"))
                else:
                    self.connected = False
                    self.root.after(0, lambda: self.status_label.config(text="Disconnected", foreground="red"))
            except:
                self.connected = False
                self.root.after(0, lambda: self.status_label.config(text="Disconnected", foreground="red"))

        threading.Thread(target=check, daemon=True).start()

    def send_command(self, endpoint):
        try:
            response = requests.get(f"http://{self.GOPRO_IP}:{self.GOPRO_PORT}{endpoint}", timeout=5)
            return response.status_code == 200
        except:
            return False

    def start_preview(self):
        if not self.connected:
            messagebox.showwarning("Not Connected", "GoPro not connected")
            return

        # Stop any existing preview first
        self.stop_preview()

        def start():
            self.send_command("/gopro/camera/stream/start")
            time.sleep(1)

            # Launch external VLC
            cmd = [
                r"C:\Program Files\VideoLAN\VLC\vlc.exe",
                "udp://@:8554",
                "--network-caching=300",
                "--no-video-title-show"
            ]
            self.vlc_process = subprocess.Popen(cmd)
            self.streaming = True
            self.root.after(0, lambda: self.status_label.config(text="Preview running in VLC", foreground="green"))

        threading.Thread(target=start, daemon=True).start()

    def stop_preview(self):
        self.streaming = False
        if self.vlc_process:
            self.vlc_process.terminate()
            self.vlc_process = None
        self.send_command("/gopro/camera/stream/stop")
        self.status_label.config(text="Connected", foreground="green")

    def take_photo(self):
        if self.send_command("/gopro/camera/shutter/start"):
            self.status_label.config(text="Photo taken!", foreground="green")
        else:
            messagebox.showerror("Error", "Failed to take photo")

    def start_video(self):
        self.send_command("/gopro/camera/presets/set_group?id=1000")
        time.sleep(0.5)
        if self.send_command("/gopro/camera/shutter/start"):
            self.status_label.config(text="Recording...", foreground="red")
        else:
            messagebox.showerror("Error", "Failed to start video")

    def stop_video(self):
        if self.send_command("/gopro/camera/shutter/stop"):
            self.status_label.config(text="Recording stopped", foreground="green")
        else:
            messagebox.showerror("Error", "Failed to stop video")

    def on_close(self):
        self.shutdown = True  # Stop background threads
        self.streaming = False
        if self.vlc_process:
            self.vlc_process.terminate()
        self.send_command("/gopro/camera/stream/stop")
        self.root.destroy()

if __name__ == "__main__":
    root = tk.Tk()
    app = GoProGUI(root)
    root.mainloop()
