"""Test GoPro preview stream - with proper setup"""
import requests
import subprocess
import time

print("=" * 50)
print("GoPro Preview Stream Test v2")
print("=" * 50)

# Step 1: Test basic connection
print("\n1. Testing GoPro API connection...")
try:
    response = requests.get("http://10.5.5.9:8080/gopro/camera/state", timeout=5)
    print(f"   Status: {response.status_code}")
    if response.status_code == 200:
        print("   OK - GoPro reachable")
except Exception as e:
    print(f"   FAIL: {e}")
    exit(1)

# Step 2: Stop any existing stream first
print("\n2. Stopping any existing stream...")
try:
    response = requests.get("http://10.5.5.9:8080/gopro/camera/stream/stop", timeout=5)
    print(f"   Status: {response.status_code}")
except Exception as e:
    print(f"   Note: {e}")

time.sleep(1)

# Step 3: Set camera to video mode (required for preview)
print("\n3. Setting camera to video mode...")
try:
    # Load video preset
    response = requests.get("http://10.5.5.9:8080/gopro/camera/presets/set_group?id=1000", timeout=5)
    print(f"   Status: {response.status_code}")
except Exception as e:
    print(f"   Note: {e}")

time.sleep(1)

# Step 4: Start the stream
print("\n4. Starting GoPro preview stream...")
try:
    response = requests.get("http://10.5.5.9:8080/gopro/camera/stream/start", timeout=5)
    print(f"   Status: {response.status_code}")
    if response.status_code == 200:
        print("   OK - Stream start command sent")
    else:
        print(f"   Response: {response.text}")
except Exception as e:
    print(f"   FAIL: {e}")
    exit(1)

# Step 5: Wait for stream to initialize
print("\n5. Waiting for stream to initialize...")
time.sleep(3)
print("   OK - Waited 3 seconds")

# Step 6: Try to receive UDP stream with FFmpeg
print("\n6. Testing UDP stream reception...")
print("   Running FFmpeg to capture from udp://0.0.0.0:8554...")

try:
    # Try to capture a single frame to test
    cmd = [
        "C:\\ffmpeg\\bin\\ffmpeg.exe",
        "-y",
        "-fflags", "nobuffer",
        "-flags", "low_delay",
        "-i", "udp://0.0.0.0:8554?timeout=5000000",  # 5 second timeout
        "-frames:v", "1",
        "-f", "image2",
        "C:\\Users\\woody\\Desktop\\gopro-control\\test_frame.jpg"
    ]

    result = subprocess.run(cmd, capture_output=True, text=True, timeout=15)

    if result.returncode == 0:
        print("   OK - Captured test frame to test_frame.jpg!")
        print("   SUCCESS! Preview stream is working!")
    else:
        print(f"   FAIL - FFmpeg returned {result.returncode}")
        if result.stderr:
            # Show last part of stderr
            lines = result.stderr.strip().split('\n')
            print("   Last few lines of FFmpeg output:")
            for line in lines[-5:]:
                print(f"   {line}")

except subprocess.TimeoutExpired:
    print("   TIMEOUT - No stream data received in 15 seconds")
    print("   The GoPro may not be sending UDP packets to this computer")
except Exception as e:
    print(f"   FAIL: {e}")

# Step 7: Stop the stream
print("\n7. Stopping GoPro preview stream...")
try:
    response = requests.get("http://10.5.5.9:8080/gopro/camera/stream/stop", timeout=5)
    print(f"   Status: {response.status_code}")
except Exception as e:
    print(f"   Error stopping: {e}")

print("\n" + "=" * 50)
print("Test complete")
print("=" * 50)
