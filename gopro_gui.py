"""
GoPro Controller - Native Tkinter App with VLC
Low-latency video preview using VLC player
"""

import tkinter as tk
from tkinter import ttk, messagebox
import subprocess
import threading
import requests
import time
import sys
import asyncio
from bleak import BleakScanner, BleakClient

class GoProGUI:
    # GoPro constants
    GOPRO_IP = "10.5.5.9"
    GOPRO_PORT = 8080
    GOPRO_SSID = "GP25102353"

    # BLE UUIDs
    BLE_COMMAND_UUID = "b5f90072-aa8d-11e3-9046-0002a5d5c51b"

    def __init__(self, root):
        self.root = root
        self.root.title("GoPro Controller")
        self.root.geometry("900x650")

        # State
        self.connected = False
        self.streaming = False
        self.vlc_process = None

        self.setup_ui()
        self.check_connection()

    def setup_ui(self):
        # Main frame
        main_frame = ttk.Frame(self.root, padding="10")
        main_frame.pack(fill=tk.BOTH, expand=True)

        # Connection status
        status_frame = ttk.Frame(main_frame)
        status_frame.pack(fill=tk.X, pady=(0, 10))

        self.status_label = ttk.Label(status_frame, text="Disconnected", foreground="red")
        self.status_label.pack(side=tk.LEFT)

        ttk.Button(status_frame, text="Wake WiFi (BLE)", command=self.wake_wifi_ble).pack(side=tk.RIGHT, padx=5)
        ttk.Button(status_frame, text="Retry Connection", command=self.check_connection).pack(side=tk.RIGHT)

        # Video preview frame - use a Canvas for VLC
        self.video_frame = ttk.LabelFrame(main_frame, text="Preview", padding="5")
        self.video_frame.pack(fill=tk.BOTH, expand=True, pady=(0, 10))

        # Canvas to embed VLC
        self.video_canvas = tk.Canvas(self.video_frame, bg='black', width=800, height=450)
        self.video_canvas.pack(fill=tk.BOTH, expand=True)

        # Preview controls
        preview_controls = ttk.Frame(main_frame)
        preview_controls.pack(fill=tk.X, pady=(0, 10))

        self.btn_start_preview = ttk.Button(preview_controls, text="Start Preview", command=self.start_preview)
        self.btn_start_preview.pack(side=tk.LEFT, padx=5)

        self.btn_stop_preview = ttk.Button(preview_controls, text="Stop Preview", command=self.stop_preview, state=tk.DISABLED)
        self.btn_stop_preview.pack(side=tk.LEFT, padx=5)

        # Photo/Video controls
        controls_frame = ttk.LabelFrame(main_frame, text="Controls", padding="10")
        controls_frame.pack(fill=tk.X)

        ttk.Button(controls_frame, text="Take Photo", command=self.take_photo).pack(side=tk.LEFT, padx=5)
        ttk.Button(controls_frame, text="Start Video", command=self.start_video).pack(side=tk.LEFT, padx=5)
        ttk.Button(controls_frame, text="Stop Video", command=self.stop_video).pack(side=tk.LEFT, padx=5)

        # Bind window close
        self.root.protocol("WM_DELETE_WINDOW", self.on_close)

    def check_connection(self):
        """Check if GoPro is reachable"""
        def check():
            try:
                response = requests.get(
                    f"http://{self.GOPRO_IP}:{self.GOPRO_PORT}/gopro/camera/state",
                    timeout=2
                )
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

    def wake_wifi_ble(self):
        """Wake GoPro WiFi via Bluetooth"""
        def wake():
            try:
                self.root.after(0, lambda: self.status_label.config(text="Scanning BLE...", foreground="orange"))

                loop = asyncio.new_event_loop()
                asyncio.set_event_loop(loop)

                # Scan for GoPro
                devices = loop.run_until_complete(BleakScanner.discover(timeout=5.0))
                gopro_device = None
                for device in devices:
                    if device.name and "GoPro" in device.name:
                        gopro_device = device
                        break

                if not gopro_device:
                    self.root.after(0, lambda: messagebox.showerror("Error", "GoPro not found via Bluetooth"))
                    self.root.after(0, lambda: self.status_label.config(text="Disconnected", foreground="red"))
                    return

                # Connect and send WiFi enable command
                async def enable_wifi():
                    async with BleakClient(gopro_device.address) as client:
                        enable_cmd = bytes([0x03, 0x17, 0x01, 0x01])
                        await client.write_gatt_char(self.BLE_COMMAND_UUID, enable_cmd)
                        await asyncio.sleep(2)

                loop.run_until_complete(enable_wifi())
                loop.close()

                self.root.after(0, lambda: self.status_label.config(text="WiFi enabled, connecting...", foreground="orange"))
                time.sleep(2)
                self.check_connection()

            except Exception as e:
                self.root.after(0, lambda: messagebox.showerror("BLE Error", str(e)))
                self.root.after(0, lambda: self.status_label.config(text="Disconnected", foreground="red"))

        threading.Thread(target=wake, daemon=True).start()

    def send_command(self, endpoint):
        """Send command to GoPro"""
        try:
            response = requests.get(
                f"http://{self.GOPRO_IP}:{self.GOPRO_PORT}{endpoint}",
                timeout=5
            )
            return response.status_code == 200
        except:
            return False

    def start_preview(self):
        """Start live preview stream"""
        if not self.connected:
            messagebox.showwarning("Not Connected", "GoPro not connected")
            return

        def start():
            # Start GoPro stream
            self.send_command("/gopro/camera/stream/start")
            time.sleep(1)

            # Launch VLC externally with low latency settings
            self.vlc_process = subprocess.Popen([
                'C:\\Program Files\\VideoLAN\\VLC\\vlc.exe',
                'udp://@:8554',
                '--network-caching=300'
            ])

            self.streaming = True
            self.root.after(0, lambda: self.btn_start_preview.config(state=tk.DISABLED))
            self.root.after(0, lambda: self.btn_stop_preview.config(state=tk.NORMAL))

        threading.Thread(target=start, daemon=True).start()

    def stop_preview(self):
        """Stop live preview"""
        self.streaming = False

        if self.vlc_process:
            self.vlc_process.terminate()
            self.vlc_process = None

        self.send_command("/gopro/camera/stream/stop")

        self.btn_start_preview.config(state=tk.NORMAL)
        self.btn_stop_preview.config(state=tk.DISABLED)

    def take_photo(self):
        """Take a photo"""
        if self.send_command("/gopro/camera/shutter/start"):
            self.status_label.config(text="Photo taken!", foreground="green")
            self.root.after(2000, self.check_connection)
        else:
            messagebox.showerror("Error", "Failed to take photo")

    def start_video(self):
        """Start video recording"""
        # Set to video mode first
        self.send_command("/gopro/camera/presets/set_group?id=1000")
        time.sleep(0.5)
        if self.send_command("/gopro/camera/shutter/start"):
            self.status_label.config(text="Recording...", foreground="red")
        else:
            messagebox.showerror("Error", "Failed to start video")

    def stop_video(self):
        """Stop video recording"""
        if self.send_command("/gopro/camera/shutter/stop"):
            self.status_label.config(text="Recording stopped", foreground="green")
            self.root.after(2000, self.check_connection)
        else:
            messagebox.showerror("Error", "Failed to stop video")

    def on_close(self):
        """Clean up on window close"""
        self.streaming = False
        if self.vlc_process:
            self.vlc_process.terminate()
            self.vlc_process = None
        self.send_command("/gopro/camera/stream/stop")
        self.root.destroy()

if __name__ == "__main__":
    root = tk.Tk()
    app = GoProGUI(root)
    root.mainloop()
