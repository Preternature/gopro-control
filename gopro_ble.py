"""
GoPro BLE (Bluetooth Low Energy) Module
Enables GoPro WiFi without needing the Quik app
"""

import asyncio
from bleak import BleakScanner, BleakClient

# GoPro BLE UUIDs
GOPRO_SERVICE = "0000fea6-0000-1000-8000-00805f9b34fb"
COMMAND_UUID = "b5f90072-aa8d-11e3-9046-0002a5d5c51b"
COMMAND_RESPONSE_UUID = "b5f90073-aa8d-11e3-9046-0002a5d5c51b"

# Commands
CMD_ENABLE_WIFI = bytearray([0x03, 0x17, 0x01, 0x01])  # Enable WiFi AP
CMD_DISABLE_WIFI = bytearray([0x03, 0x17, 0x01, 0x00])  # Disable WiFi AP

async def find_gopro():
    """Scan for GoPro devices"""
    print("Scanning for GoPro...")
    devices = await BleakScanner.discover(timeout=10)

    for device in devices:
        if device.name and "GoPro" in device.name:
            print(f"Found: {device.name} ({device.address})")
            return device

    print("No GoPro found via Bluetooth")
    return None

async def enable_wifi(device_address):
    """Connect to GoPro via BLE and enable WiFi"""
    print(f"Connecting to {device_address}...")
    print("(If this fails, disconnect other devices and put GoPro in pairing mode)")

    try:
        async with BleakClient(device_address, timeout=20) as client:
            if not client.is_connected:
                print("Failed to connect")
                return False

            print("Connected! Enabling WiFi...")

            # Subscribe to responses
            response_received = asyncio.Event()

            def response_handler(sender, data):
                print(f"Response: {data.hex()}")
                response_received.set()

            await client.start_notify(COMMAND_RESPONSE_UUID, response_handler)

            # Send enable WiFi command
            await client.write_gatt_char(COMMAND_UUID, CMD_ENABLE_WIFI, response=True)

            # Wait for response
            try:
                await asyncio.wait_for(response_received.wait(), timeout=5)
                print("WiFi AP enabled! Look for the GoPro network now.")
                return True
            except asyncio.TimeoutError:
                print("No response received, but WiFi may still be enabled")
                return True

    except Exception as e:
        print(f"Connection error: {e}")
        print("\nTry: Preferences > Connections > Reset Connections on GoPro")
        return False

async def main():
    """Main entry point"""
    device = await find_gopro()
    if device:
        await enable_wifi(device.address)
    else:
        print("\nMake sure:")
        print("  1. GoPro is turned ON")
        print("  2. Bluetooth is enabled on your PC")
        print("  3. GoPro is not connected to another device")

if __name__ == "__main__":
    asyncio.run(main())
