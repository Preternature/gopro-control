"""
GoPro Connection Module
Handles communication with GoPro Hero 12 via HTTP API (WiFi or USB)
Supports multiple simultaneous cameras.
"""

import requests
import socket
import subprocess
import re
import os
import time
import threading
import asyncio
from typing import Optional, Tuple, List
from bleak import BleakScanner, BleakClient
from requests.adapters import HTTPAdapter
from urllib3.poolmanager import PoolManager


class SourceAddressAdapter(HTTPAdapter):
    """Binds all requests to a specific local IP, forcing them through a specific network adapter."""
    def __init__(self, source_ip: str, **kwargs):
        self.source_ip = source_ip
        super().__init__(**kwargs)

    def init_poolmanager(self, num_pools, maxsize, block=False, **kw):
        self.poolmanager = PoolManager(
            num_pools=num_pools,
            maxsize=maxsize,
            block=block,
            source_address=(self.source_ip, 0),
            **kw
        )

class GoProConnection:
    """Manages connection to a single GoPro camera over WiFi or USB"""

    # GoPro always uses this IP on its own WiFi AP
    WIFI_IP = "10.5.5.9"
    USB_IP_PATTERN = r"172\.2\d\.1\d{2}\.51"
    GOPRO_PORT = 8080

    # Default WiFi credentials (first camera)
    DEFAULT_GOPRO_SSID = "GP25102353"
    HOME_SSID = "Bitterroot"

    # BLE UUIDs for GoPro
    BLE_WIFI_AP_SSID_UUID = "b5f90002-aa8d-11e3-9046-0002a5d5c51b"
    BLE_WIFI_AP_PASSWORD_UUID = "b5f90003-aa8d-11e3-9046-0002a5d5c51b"
    BLE_COMMAND_UUID = "b5f90072-aa8d-11e3-9046-0002a5d5c51b"

    def __init__(self, name="cam1", gopro_ip=None, stream_udp_port=8554, gopro_ssid=None, ble_name=None):
        """
        Args:
            name: Identifier for this camera (e.g. "cam1", "cam2")
            gopro_ip: Known IP to connect to. None = auto-discover on connect.
            stream_udp_port: UDP port for preview stream (8554 for cam1, 8555 for cam2, etc.)
            gopro_ssid: WiFi SSID for this camera (used by WiFi switch commands)
        """
        self.name = name
        self._configured_ip = gopro_ip  # User-specified IP; None = auto-discover
        self.gopro_ip = gopro_ip or self.WIFI_IP
        self.base_url = f"http://{self.gopro_ip}:{self.GOPRO_PORT}" if gopro_ip else None
        self.connected = False
        self.connection_type = "unknown"
        self.timeout = 5
        self.ffmpeg_process = None
        self.stream_active = False
        self.ble_device = None
        self.stream_udp_port = stream_udp_port
        self.GOPRO_SSID = gopro_ssid or self.DEFAULT_GOPRO_SSID
        self.BLE_NAME = ble_name  # exact BLE advertisement name, e.g. "GoPro 4477"
        self._session = requests.Session()  # may be replaced with a bound session on WiFi connect

    # === Static / Class Methods ===

    @staticmethod
    def _test_ip(ip: str, port: int = GOPRO_PORT, source_ip: str = None) -> bool:
        """Test if a GoPro is reachable at the given IP, optionally bound to a source adapter IP"""
        try:
            url = f"http://{ip}:{port}/gopro/camera/state"
            if source_ip:
                s = requests.Session()
                s.mount("http://", SourceAddressAdapter(source_ip))
                response = s.get(url, timeout=2)
            else:
                response = requests.get(url, timeout=2)
            return response.status_code == 200
        except:
            return False

    @classmethod
    def scan_all_usb_gopros(cls) -> List[str]:
        """
        Scan for ALL GoPros connected via USB.
        Returns a list of IP addresses (e.g. ['172.20.180.51', '172.21.180.51']).
        """
        found = []
        try:
            result = subprocess.run(
                ["ipconfig"],
                capture_output=True,
                text=True,
                timeout=5
            )
            # Each USB GoPro creates a 172.2x.1xx.x subnet
            ip_matches = re.findall(r"172\.2\d\.1\d{2}\.\d+", result.stdout)
            seen_subnets = set()
            for ip in ip_matches:
                parts = ip.split('.')
                subnet = f"{parts[0]}.{parts[1]}.{parts[2]}"
                if subnet in seen_subnets:
                    continue
                seen_subnets.add(subnet)
                gopro_ip = f"{subnet}.51"
                if cls._test_ip(gopro_ip):
                    found.append(gopro_ip)
        except Exception as e:
            print(f"Error scanning USB GoPros: {e}")
        return found

    @classmethod
    def auto_detect_all(cls) -> List[dict]:
        """
        Detect all connected GoPros (USB and WiFi).
        Returns list of dicts: [{"ip": ..., "type": "usb"/"wifi"}, ...]
        """
        cameras = []

        # Scan USB first (preferred — no IP conflicts)
        usb_ips = cls.scan_all_usb_gopros()
        for ip in usb_ips:
            cameras.append({"ip": ip, "type": "usb"})

        # Check WiFi (only if not already found via USB at same subnet)
        if cls._test_ip(cls.WIFI_IP):
            cameras.append({"ip": cls.WIFI_IP, "type": "wifi"})

        return cameras

    # === BLE ===

    async def _scan_for_gopro_ble(self):
        """Scan for this camera's GoPro BLE device"""
        print(f"[{self.name}] Scanning for GoPro BLE devices (target: {self.BLE_NAME or self.GOPRO_SSID})...")
        devices = await BleakScanner.discover(timeout=5.0)
        best = None
        for device in devices:
            if not (device.name and "GoPro" in device.name):
                continue
            print(f"[{self.name}] Found GoPro BLE: {device.name} ({device.address})")
            # Exact match by stored BLE name (most reliable)
            if self.BLE_NAME and device.name == self.BLE_NAME:
                print(f"[{self.name}] Exact BLE name match: {device.name}")
                return device
            # Fallback: match by last 4 chars of SSID
            ssid_suffix = self.GOPRO_SSID[-4:] if len(self.GOPRO_SSID) >= 4 else self.GOPRO_SSID
            if ssid_suffix in device.name:
                print(f"[{self.name}] Matched by SSID suffix '{ssid_suffix}': {device.name}")
                return device
            if best is None:
                best = device
        if best:
            print(f"[{self.name}] No exact match — falling back to first found: {best.name}")
        return best

    async def _enable_wifi_via_ble(self, device_address: str) -> bool:
        """Send BLE command to enable WiFi AP mode"""
        try:
            async with BleakClient(device_address) as client:
                if not client.is_connected:
                    print(f"[{self.name}] Failed to connect to GoPro via BLE")
                    return False

                print(f"[{self.name}] Connected to GoPro via BLE")
                enable_wifi_cmd = bytes([0x03, 0x17, 0x01, 0x01])
                await client.write_gatt_char(self.BLE_COMMAND_UUID, enable_wifi_cmd)
                print(f"[{self.name}] Sent WiFi enable command")
                await asyncio.sleep(3)
                return True

        except Exception as e:
            print(f"[{self.name}] BLE error: {e}")
            return False

    def wake_gopro_wifi(self) -> dict:
        """Use BLE to wake up GoPro's WiFi"""
        try:
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)

            device = loop.run_until_complete(self._scan_for_gopro_ble())
            if not device:
                return {"success": False, "error": "GoPro not found via Bluetooth. Make sure it's on and nearby."}

            self.ble_device = device
            result = loop.run_until_complete(self._enable_wifi_via_ble(device.address))
            loop.close()

            if result:
                # WiFi is now on — switch the PC to that network
                print(f"[{self.name}] WiFi enabled, switching PC to {self.GOPRO_SSID}...")
                time.sleep(2)  # Give camera a moment to fully start the AP
                wifi_ok = self.switch_to_gopro_wifi()
                if wifi_ok:
                    return {"success": True, "message": f"WiFi enabled and connected to {self.GOPRO_SSID}"}
                else:
                    return {"success": True, "message": f"WiFi enabled on {device.name} — but couldn't auto-join {self.GOPRO_SSID} (try Join button)"}
            else:
                return {"success": False, "error": "Failed to enable WiFi via BLE"}

        except Exception as e:
            return {"success": False, "error": str(e)}

    # === Connection ===

    def connect(self) -> bool:
        """
        Connect to this camera.
        If a specific IP was configured, try that. Otherwise auto-discover.
        """
        if self._configured_ip:
            # Try the configured IP directly
            if self._test_ip(self._configured_ip):
                self.gopro_ip = self._configured_ip
                self.connection_type = "usb" if self._configured_ip != self.WIFI_IP else "wifi"
                self.base_url = f"http://{self.gopro_ip}:{self.GOPRO_PORT}"
                self.connected = True
                print(f"[{self.name}] Connected at {self.gopro_ip} ({self.connection_type})")
                return True
            else:
                print(f"[{self.name}] Not reachable at configured IP {self._configured_ip}")
                self.connected = False
                return False

        # Auto-discover: check WiFi first, then USB
        if self._test_ip(self.WIFI_IP):
            # Verify this is actually our camera by checking its SSID
            try:
                info = requests.get(
                    f"http://{self.WIFI_IP}:{self.GOPRO_PORT}/gopro/camera/info", timeout=2
                ).json()
                if info.get("ap_ssid") != self.GOPRO_SSID:
                    print(f"[{self.name}] {self.WIFI_IP} is '{info.get('ap_ssid')}', not our camera '{self.GOPRO_SSID}' — skipping")
                else:
                    self.gopro_ip = self.WIFI_IP
                    self.connection_type = "wifi"
                    self.base_url = f"http://{self.WIFI_IP}:{self.GOPRO_PORT}"
                    self.connected = True
                    print(f"[{self.name}] Connected via WiFi at {self.WIFI_IP} (confirmed {self.GOPRO_SSID})")
                    return True
            except Exception:
                pass  # fall through to USB scan

        usb_ip = self._find_usb_gopro()
        if usb_ip:
            self.gopro_ip = usb_ip
            self.connection_type = "usb"
            self.base_url = f"http://{usb_ip}:{self.GOPRO_PORT}"
            self.connected = True
            self._session = requests.Session()  # plain session — USB has unique IP, no binding needed
            print(f"[{self.name}] Connected via USB at {usb_ip}")
            return True

        print(f"[{self.name}] GoPro not found")
        self.connected = False
        return False

    def check_connection(self) -> bool:
        """Check if this camera is currently reachable"""
        if self.gopro_ip and self._test_ip(self.gopro_ip):
            self.connected = True
            return True
        # Try to reconnect
        return self.connect()

    def _find_usb_gopro(self) -> Optional[str]:
        """Find the first GoPro connected via USB"""
        try:
            result = subprocess.run(
                ["ipconfig"],
                capture_output=True,
                text=True,
                timeout=5
            )
            ip_matches = re.findall(r"172\.2\d\.1\d{2}\.\d+", result.stdout)
            seen = set()
            for ip in ip_matches:
                parts = ip.split('.')
                gopro_ip = f"{parts[0]}.{parts[1]}.{parts[2]}.51"
                if gopro_ip in seen:
                    continue
                seen.add(gopro_ip)
                if self._test_ip(gopro_ip):
                    return gopro_ip
        except Exception as e:
            print(f"[{self.name}] Error scanning for USB GoPro: {e}")
        return None

    def _test_connection(self, ip: str) -> bool:
        """Instance wrapper for _test_ip (backward compat)"""
        return self._test_ip(ip)

    # === Camera Commands ===

    def get_camera_state(self) -> Optional[dict]:
        """Get current camera state and settings"""
        if not self.base_url:
            if not self.connect():
                return None
        try:
            response = self._session.get(
                f"{self.base_url}/gopro/camera/state",
                timeout=self.timeout
            )
            if response.status_code == 200:
                return response.json()
            return None
        except requests.exceptions.RequestException as e:
            print(f"[{self.name}] Error getting camera state: {e}")
            return None

    def get_camera_info(self) -> Optional[dict]:
        """Get camera info (model, serial number, firmware)"""
        if not self.base_url:
            if not self.connect():
                return None
        try:
            response = self._session.get(
                f"{self.base_url}/gopro/camera/info",
                timeout=self.timeout
            )
            if response.status_code == 200:
                return response.json()
            return None
        except requests.exceptions.RequestException:
            return None

    def send_command(self, endpoint: str, params: dict = None) -> Optional[dict]:
        """Send a command to the GoPro"""
        if not self.base_url:
            if not self.connect():
                return None
        try:
            url = f"{self.base_url}{endpoint}"
            response = self._session.get(url, params=params, timeout=self.timeout)
            if response.status_code == 200:
                if response.text:
                    return response.json()
                return {"status": "success"}
            else:
                print(f"[{self.name}] Command failed: {response.status_code}")
                return None
        except requests.exceptions.RequestException as e:
            print(f"[{self.name}] Error sending command: {e}")
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
            response = self._session.get(
                f"{self.base_url}/gopro/media/list",
                timeout=self.timeout
            )
            if response.status_code == 200:
                return response.json()
            return None
        except requests.exceptions.RequestException as e:
            print(f"[{self.name}] Error getting media list: {e}")
            return None

    def get_stream_url(self) -> str:
        return f"udp://0.0.0.0:{self.stream_udp_port}"

    def start_preview_stream(self) -> bool:
        """Start the preview stream, directing it to this camera's UDP port"""
        print(f"[{self.name}] Starting stream on UDP port {self.stream_udp_port}")
        result = self.send_command("/gopro/camera/stream/start", {"port": self.stream_udp_port})
        if result is None:
            print(f"[{self.name}] ERROR: stream/start returned None")
        else:
            print(f"[{self.name}] Stream start result: {result}")
        return result is not None

    def stop_preview_stream(self) -> bool:
        """Stop the preview stream"""
        result = self.send_command("/gopro/camera/stream/stop")
        return result is not None

    def get_connection_info(self) -> dict:
        return {
            "name": self.name,
            "connected": self.connected,
            "ip": self.gopro_ip,
            "type": self.connection_type,
            "url": self.base_url,
            "stream_active": self.stream_active,
            "stream_port": self.stream_udp_port,
        }

    # === WiFi helpers ===

    def is_gopro_wifi_available(self) -> bool:
        """Check if this camera's WiFi network is visible"""
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
        """Switch to this camera's WiFi network, retrying until the AP is visible"""
        print(f"[{self.name}] Waiting for '{self.GOPRO_SSID}' to appear...")
        for attempt in range(8):
            if self.is_gopro_wifi_available():
                break
            print(f"[{self.name}] '{self.GOPRO_SSID}' not visible yet (attempt {attempt+1}/8), waiting...")
            time.sleep(2)
        else:
            print(f"[{self.name}] GoPro WiFi '{self.GOPRO_SSID}' never appeared.")
            return False

        try:
            # Find the primary WiFi interface name
            iface_result = subprocess.run(
                ["netsh", "wlan", "show", "interfaces"],
                capture_output=True, text=True, timeout=5
            )
            iface_name = None
            for line in iface_result.stdout.splitlines():
                line = line.strip()
                if line.startswith("Name") and ":" in line:
                    iface_name = line.split(":", 1)[1].strip()
                    break  # use the first interface found

            cmd = ["netsh", "wlan", "connect", f"name={self.GOPRO_SSID}", f"ssid={self.GOPRO_SSID}"]
            if iface_name:
                cmd += [f"interface={iface_name}"]
                print(f"[{self.name}] Connecting via interface '{iface_name}'")

            result = subprocess.run(cmd, capture_output=True, text=True, timeout=10)
            print(f"[{self.name}] netsh connect result: {result.stdout.strip()}")
            if "completed successfully" in result.stdout:
                time.sleep(3)
                self.gopro_ip = self.WIFI_IP
                self.base_url = f"http://{self.WIFI_IP}:{self.GOPRO_PORT}"
                self.connected = True
                self.connection_type = "wifi"
                # Bind session to the local IP of this adapter so requests
                # go through the right adapter even when both are on 10.5.5.x
                local_ip = self._get_local_ip_for_ssid(self.GOPRO_SSID)
                if local_ip:
                    print(f"[{self.name}] Binding session to local IP {local_ip} (adapter on {self.GOPRO_SSID})")
                    self._session = requests.Session()
                    self._session.mount("http://", SourceAddressAdapter(local_ip))
                return True
            return False
        except Exception as e:
            print(f"[{self.name}] Error switching WiFi: {e}")
            return False

    def _get_local_ip_for_ssid(self, ssid: str) -> Optional[str]:
        """Find the local IP of the adapter currently connected to the given SSID"""
        try:
            result = subprocess.run(
                ["netsh", "wlan", "show", "interfaces"],
                capture_output=True, text=True, timeout=5
            )
            # Parse blocks per interface
            current_ssid = None
            for line in result.stdout.splitlines():
                line = line.strip()
                if line.startswith("SSID") and "BSSID" not in line and ":" in line:
                    current_ssid = line.split(":", 1)[1].strip()
                if current_ssid == ssid and line.startswith("IPv4") and ":" in line:
                    return line.split(":", 1)[1].strip()
            # Fallback: parse ipconfig for the IP on the right subnet
        except Exception as e:
            print(f"[{self.name}] Could not find local IP for SSID {ssid}: {e}")
        return None

    def switch_to_home_wifi(self) -> bool:
        """Switch back to home WiFi network"""
        try:
            result = subprocess.run(
                ["netsh", "wlan", "connect", f"name={self.HOME_SSID}"],
                capture_output=True, text=True, timeout=10
            )
            return "completed successfully" in result.stdout
        except Exception as e:
            print(f"[{self.name}] Error switching to home WiFi: {e}")
            return False

    # === HLS Streaming ===

    def start_mjpeg_stream(self) -> bool:
        """Start FFmpeg outputting MJPEG frames to a pipe (low-latency preview)"""
        if self.ffmpeg_process:
            self.stop_mjpeg_stream()

        self.stop_preview_stream()
        time.sleep(0.5)

        print(f"[{self.name}] Starting GoPro preview stream...")
        if not self.start_preview_stream():
            print(f"[{self.name}] Failed to start GoPro preview stream")
            return False

        time.sleep(0.5)

        cmd = [
            "C:\\ffmpeg\\bin\\ffmpeg.exe",
            "-fflags", "nobuffer+genpts+discardcorrupt",
            "-flags", "low_delay",
            "-probesize", "500000",
            "-analyzeduration", "500000",
            "-i", f"udp://0.0.0.0:{self.stream_udp_port}?timeout=5000000&overrun_nonfatal=1",
            "-map", "0:v:0",
            "-c:v", "mjpeg",
            "-q:v", "4",
            "-f", "mjpeg",
            "pipe:1"
        ]

        try:
            print(f"[{self.name}] Starting FFmpeg MJPEG pipe")
            self.ffmpeg_process = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL
            )
            self.stream_active = True
            return True
        except FileNotFoundError:
            print(f"[{self.name}] ERROR: FFmpeg not found")
            return False
        except Exception as e:
            print(f"[{self.name}] ERROR starting stream: {e}")
            return False

    def mjpeg_frames(self):
        """Generator yielding raw JPEG bytes from the FFmpeg pipe"""
        buf = b''
        SOI = b'\xff\xd8'
        EOI = b'\xff\xd9'
        while self.stream_active and self.ffmpeg_process:
            try:
                chunk = self.ffmpeg_process.stdout.read(4096)
            except Exception:
                break
            if not chunk:
                break
            buf += chunk
            # Extract complete frames
            while True:
                start = buf.find(SOI)
                if start == -1:
                    buf = b''
                    break
                end = buf.find(EOI, start + 2)
                if end == -1:
                    buf = buf[start:]  # keep partial frame
                    break
                yield buf[start:end + 2]
                buf = buf[end + 2:]

    def stop_mjpeg_stream(self) -> bool:
        """Stop the FFmpeg HLS stream"""
        if self.ffmpeg_process:
            self.ffmpeg_process.terminate()
            self.ffmpeg_process = None

        if hasattr(self, 'ffmpeg_log') and self.ffmpeg_log:
            self.ffmpeg_log.close()
            self.ffmpeg_log = None

        self.stop_preview_stream()
        self.stream_active = False
        return True
