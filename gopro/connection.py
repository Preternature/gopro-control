"""
GoPro Connection Module
Handles communication with GoPro Hero 12 via HTTP API (WiFi or USB)
"""

import requests
import socket
import subprocess
import re
import time
import threading
import asyncio
from typing import Optional, Tuple
from bleak import BleakScanner, BleakClient

class GoProConnection:
    """Manages connection to GoPro camera over WiFi or USB"""

    # Known GoPro IP patterns
    WIFI_IP = "10.5.5.9"
    USB_IP_PATTERN = r"172\.2\d\.1\d{2}\.51"  # USB typically 172.2x.1xx.51
    GOPRO_PORT = 8080

    # WiFi network names
    GOPRO_SSID = "GP25102353"
    HOME_SSID = "Bitterroot"

    # BLE UUIDs for GoPro
    BLE_WIFI_AP_SSID_UUID = "b5f90002-aa8d-11e3-9046-0002a5d5c51b"
    BLE_WIFI_AP_PASSWORD_UUID = "b5f90003-aa8d-11e3-9046-0002a5d5c51b"
    BLE_COMMAND_UUID = "b5f90072-aa8d-11e3-9046-0002a5d5c51b"

    def __init__(self):
        # With dual-adapter setup, GoPro is always at 10.5.5.9
        self.gopro_ip = self.WIFI_IP
        self.base_url = f"http://{self.WIFI_IP}:{self.GOPRO_PORT}"
        self.connected = False
        self.connection_type = "wifi"
        self.timeout = 5
        self.ffmpeg_process = None
        self.stream_active = False
        self.ble_device = None

    async def _scan_for_gopro_ble(self):
        """Scan for GoPro BLE device"""
        print("Scanning for GoPro BLE devices...")
        devices = await BleakScanner.discover(timeout=5.0)
        for device in devices:
            if device.name and "GoPro" in device.name:
                print(f"Found GoPro: {device.name} ({device.address})")
                return device
        return None

    async def _enable_wifi_via_ble(self, device_address: str) -> bool:
        """Send BLE command to enable WiFi AP mode"""
        try:
            async with BleakClient(device_address) as client:
                if not client.is_connected:
                    print("Failed to connect to GoPro via BLE")
                    return False

                print("Connected to GoPro via BLE")

                # Command to enable WiFi AP: 0x03 0x17 0x01 0x01
                # 0x03 = length, 0x17 = AP control, 0x01 = enable, 0x01 = AP mode
                enable_wifi_cmd = bytes([0x03, 0x17, 0x01, 0x01])

                await client.write_gatt_char(self.BLE_COMMAND_UUID, enable_wifi_cmd)
                print("Sent WiFi enable command")

                # Wait for WiFi to start
                await asyncio.sleep(3)
                return True

        except Exception as e:
            print(f"BLE error: {e}")
            return False

    def wake_gopro_wifi(self) -> dict:
        """Use BLE to wake up GoPro's WiFi - call this from sync code"""
        try:
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)

            # Scan for GoPro
            device = loop.run_until_complete(self._scan_for_gopro_ble())
            if not device:
                return {"success": False, "error": "GoPro not found via Bluetooth. Make sure it's on and nearby."}

            self.ble_device = device

            # Enable WiFi
            result = loop.run_until_complete(self._enable_wifi_via_ble(device.address))
            loop.close()

            if result:
                return {"success": True, "message": f"WiFi enabled on {device.name}"}
            else:
                return {"success": False, "error": "Failed to enable WiFi via BLE"}

        except Exception as e:
            return {"success": False, "error": str(e)}

    def discover_gopro(self) -> Tuple[Optional[str], Optional[str]]:
        """
        Auto-discover GoPro IP address.
        Returns (ip_address, connection_type) or (None, None)
        """
        # First try USB connection (more common for wired setup)
        usb_ip = self._find_usb_gopro()
        if usb_ip:
            return usb_ip, "usb"

        # Fall back to WiFi
        if self._test_connection(self.WIFI_IP):
            return self.WIFI_IP, "wifi"

        return None, None

    def _find_usb_gopro(self) -> Optional[str]:
        """Find GoPro connected via USB by scanning network interfaces"""
        try:
            # Get all network interfaces using ipconfig on Windows
            result = subprocess.run(
                ["ipconfig"],
                capture_output=True,
                text=True,
                timeout=5
            )

            # Look for GoPro USB interface (typically shows as "Remote NDIS" or similar)
            # USB connection creates a 172.2x.1xx.x subnet
            ip_matches = re.findall(r"172\.2\d\.1\d{2}\.\d+", result.stdout)

            for ip in ip_matches:
                # GoPro is typically at .51 on the subnet
                parts = ip.split('.')
                gopro_ip = f"{parts[0]}.{parts[1]}.{parts[2]}.51"

                if self._test_connection(gopro_ip):
                    return gopro_ip

            # Also try common USB IP ranges directly
            for second in range(20, 30):
                for third in range(100, 120):
                    gopro_ip = f"172.{second}.{third}.51"
                    if self._test_connection(gopro_ip):
                        return gopro_ip

        except Exception as e:
            print(f"Error scanning for USB GoPro: {e}")

        return None

    def _test_connection(self, ip: str) -> bool:
        """Test if GoPro is reachable at given IP"""
        try:
            url = f"http://{ip}:{self.GOPRO_PORT}/gopro/camera/state"
            response = requests.get(url, timeout=2)
            return response.status_code == 200
        except:
            return False

    def connect(self) -> bool:
        """Connect to GoPro - with dual-adapter, just check if 10.5.5.9 is reachable"""
        # With dual-adapter setup, GoPro is always at 10.5.5.9
        if self._test_connection(self.WIFI_IP):
            self.gopro_ip = self.WIFI_IP
            self.connection_type = "wifi"
            self.base_url = f"http://{self.WIFI_IP}:{self.GOPRO_PORT}"
            self.connected = True
            print(f"Connected to GoPro at {self.WIFI_IP}")
            return True
        else:
            print("GoPro not reachable at 10.5.5.9 - check WiFi adapter connection")
            self.connected = False
            return False

    def check_connection(self) -> bool:
        """Check if GoPro is reachable"""
        # With dual-adapter, just test 10.5.5.9 directly
        if self._test_connection(self.WIFI_IP):
            self.gopro_ip = self.WIFI_IP
            self.base_url = f"http://{self.WIFI_IP}:{self.GOPRO_PORT}"
            self.connected = True
            return True
        else:
            self.connected = False
            return False

    def get_camera_state(self) -> Optional[dict]:
        """Get current camera state and settings"""
        if not self.base_url:
            if not self.connect():
                return None

        try:
            response = requests.get(
                f"{self.base_url}/gopro/camera/state",
                timeout=self.timeout
            )
            if response.status_code == 200:
                return response.json()
            return None
        except requests.exceptions.RequestException as e:
            print(f"Error getting camera state: {e}")
            return None

    def send_command(self, endpoint: str, params: dict = None) -> Optional[dict]:
        """Send a command to the GoPro"""
        if not self.base_url:
            if not self.connect():
                return None

        try:
            url = f"{self.base_url}{endpoint}"
            response = requests.get(url, params=params, timeout=self.timeout)

            if response.status_code == 200:
                # Some endpoints return empty response
                if response.text:
                    return response.json()
                return {"status": "success"}
            else:
                print(f"Command failed: {response.status_code}")
                return None
        except requests.exceptions.RequestException as e:
            print(f"Error sending command: {e}")
            return None

    def keep_alive(self) -> bool:
        """Send keep-alive to prevent camera from sleeping"""
        result = self.send_command("/gopro/camera/keep_alive")
        return result is not None

    def get_media_list(self) -> Optional[dict]:
        """Get list of media files on the camera"""
        if not self.base_url:
            if not self.connect():
                return None

        try:
            response = requests.get(
                f"{self.base_url}/gopro/media/list",
                timeout=self.timeout
            )
            if response.status_code == 200:
                return response.json()
            return None
        except requests.exceptions.RequestException as e:
            print(f"Error getting media list: {e}")
            return None

    def get_stream_url(self) -> str:
        """Get the URL for live preview stream"""
        return f"udp://0.0.0.0:8554"

    def start_preview_stream(self) -> bool:
        """Start the preview stream"""
        print(f"Sending stream start command to {self.base_url}/gopro/camera/stream/start")
        result = self.send_command("/gopro/camera/stream/start")
        if result is None:
            print("ERROR: stream/start command returned None")
        else:
            print(f"Stream start result: {result}")
        return result is not None

    def stop_preview_stream(self) -> bool:
        """Stop the preview stream"""
        result = self.send_command("/gopro/camera/stream/stop")
        return result is not None

    def get_connection_info(self) -> dict:
        """Get current connection information"""
        return {
            "connected": self.connected,
            "ip": self.gopro_ip,
            "type": self.connection_type,
            "url": self.base_url,
            "stream_active": self.stream_active
        }

    def is_gopro_wifi_available(self) -> bool:
        """Check if GoPro WiFi network is visible"""
        try:
            result = subprocess.run(
                ["netsh", "wlan", "show", "networks"],
                capture_output=True,
                text=True,
                timeout=5
            )
            return self.GOPRO_SSID in result.stdout
        except:
            return False

    def switch_to_gopro_wifi(self) -> bool:
        """Switch to GoPro's WiFi network"""
        # Check if GoPro WiFi is available first
        if not self.is_gopro_wifi_available():
            print(f"GoPro WiFi '{self.GOPRO_SSID}' not found. Make sure GoPro is on and use the Quik app to activate WiFi.")
            return False

        try:
            result = subprocess.run(
                ["netsh", "wlan", "connect", f"name={self.GOPRO_SSID}"],
                capture_output=True,
                text=True,
                timeout=10
            )
            if "completed successfully" in result.stdout:
                time.sleep(3)  # Wait for connection
                self.gopro_ip = self.WIFI_IP
                self.base_url = f"http://{self.WIFI_IP}:{self.GOPRO_PORT}"
                self.connected = True
                self.connection_type = "wifi"
                return True
            return False
        except Exception as e:
            print(f"Error switching to GoPro WiFi: {e}")
            return False

    def switch_to_home_wifi(self) -> bool:
        """Switch back to home WiFi network"""
        try:
            result = subprocess.run(
                ["netsh", "wlan", "connect", f"name={self.HOME_SSID}"],
                capture_output=True,
                text=True,
                timeout=10
            )
            return "completed successfully" in result.stdout
        except Exception as e:
            print(f"Error switching to home WiFi: {e}")
            return False

    def start_mjpeg_stream(self, output_port=8090) -> bool:
        """Start FFmpeg to convert GoPro UDP stream to MJPEG for browser"""
        if self.ffmpeg_process:
            self.stop_mjpeg_stream()

        # Stop any existing stream first
        print("Stopping any existing stream...")
        self.stop_preview_stream()
        time.sleep(1)

        # Start the GoPro preview stream
        print("Starting GoPro preview stream...")
        if not self.start_preview_stream():
            print("Failed to start GoPro preview stream")
            return False
        print("GoPro preview stream started")

        # Wait for stream to initialize
        time.sleep(2)

        try:
            # Create HLS output directory
            import os
            hls_dir = "C:\\Users\\woody\\Desktop\\gopro-control\\static\\hls"
            os.makedirs(hls_dir, exist_ok=True)

            # Clean old segments
            for f in os.listdir(hls_dir):
                os.remove(os.path.join(hls_dir, f))

            # FFmpeg command to output HLS stream - optimized for low latency
            cmd = [
                "C:\\ffmpeg\\bin\\ffmpeg.exe",
                "-fflags", "+genpts+discardcorrupt",
                "-flags", "low_delay",
                "-probesize", "5000000",
                "-analyzeduration", "2000000",
                "-i", "udp://0.0.0.0:8554?timeout=5000000&overrun_nonfatal=1",
                "-c:v", "libx264",
                "-preset", "ultrafast",
                "-tune", "zerolatency",
                "-g", "15",  # Keyframe every 0.5s for faster seeking
                "-sc_threshold", "0",
                "-an",  # No audio for faster streaming
                "-f", "hls",
                "-hls_time", "0.5",  # 0.5 second segments instead of 1
                "-hls_list_size", "3",
                "-hls_flags", "delete_segments+split_by_time",
                f"{hls_dir}\\stream.m3u8"
            ]

            print(f"Starting FFmpeg with command: {' '.join(cmd)}")

            # Log FFmpeg output to a file for debugging
            log_file = open(f"{hls_dir}\\ffmpeg.log", "w")
            self.ffmpeg_process = subprocess.Popen(
                cmd,
                stdout=log_file,
                stderr=log_file
            )
            self.ffmpeg_log = log_file
            self.stream_active = True
            print(f"HLS stream started at /static/hls/stream.m3u8")
            return True
        except FileNotFoundError:
            print(f"ERROR: FFmpeg not found at C:\\ffmpeg\\bin\\ffmpeg.exe")
            return False
        except Exception as e:
            print(f"ERROR starting MJPEG stream: {type(e).__name__}: {e}")
            return False

    def stop_mjpeg_stream(self) -> bool:
        """Stop the FFmpeg MJPEG stream"""
        if self.ffmpeg_process:
            self.ffmpeg_process.terminate()
            self.ffmpeg_process = None

        if hasattr(self, 'ffmpeg_log') and self.ffmpeg_log:
            self.ffmpeg_log.close()
            self.ffmpeg_log = None

        self.stop_preview_stream()
        self.stream_active = False
        return True
