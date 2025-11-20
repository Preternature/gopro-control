"""
GoPro Connection Module
Handles communication with GoPro Hero 12 via HTTP API (WiFi or USB)
"""

import requests
import socket
import subprocess
import re
from typing import Optional, Tuple

class GoProConnection:
    """Manages connection to GoPro camera over WiFi or USB"""

    # Known GoPro IP patterns
    WIFI_IP = "10.5.5.9"
    USB_IP_PATTERN = r"172\.2\d\.1\d{2}\.51"  # USB typically 172.2x.1xx.51
    GOPRO_PORT = 8080

    def __init__(self):
        self.gopro_ip = None
        self.base_url = None
        self.connected = False
        self.connection_type = None  # 'wifi' or 'usb'
        self.timeout = 5

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
        """Connect to GoPro (auto-discover IP)"""
        print("Searching for GoPro...")

        ip, conn_type = self.discover_gopro()

        if ip:
            self.gopro_ip = ip
            self.connection_type = conn_type
            self.base_url = f"http://{ip}:{self.GOPRO_PORT}"
            self.connected = True
            print(f"Connected to GoPro via {conn_type.upper()} at {ip}")
            return True
        else:
            print("GoPro not found. Make sure it's connected and USB mode is set to 'GoPro Connect'")
            self.connected = False
            return False

    def check_connection(self) -> bool:
        """Check if GoPro is reachable"""
        # If not connected yet, try to discover
        if not self.gopro_ip:
            return self.connect()

        # Test existing connection
        if self._test_connection(self.gopro_ip):
            self.connected = True
            return True

        # Connection lost, try to rediscover
        print("Connection lost, attempting to reconnect...")
        return self.connect()

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
        result = self.send_command("/gopro/camera/stream/start")
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
            "url": self.base_url
        }
