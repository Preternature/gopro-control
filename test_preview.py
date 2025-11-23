"""Test GoPro preview stream step by step"""
import requests
import subprocess
import time

print("=" * 50)
print("GoPro Preview Stream Test")
print("=" * 50)

# Step 1: Test basic connection
print("\n1. Testing GoPro API connection...")
try:
    response = requests.get("http://10.5.5.9:8080/gopro/camera/state", timeout=5)
    print(f"   Status: {response.status_code}")
    if response.status_code == 200:
        print("   OK - GoPro reachable")
    else:
        print("   FAIL - Bad status code")
        exit(1)
except Exception as e:
    print(f"   FAIL: {e}")
    exit(1)

# Step 2: Start the stream
print("\n2. Starting GoPro preview stream...")
try:
    response = requests.get("http://10.5.5.9:8080/gopro/camera/stream/start", timeout=5)
    print(f"   Status: {response.status_code}")
    if response.status_code == 200:
        print("   OK - Stream start command sent")
    else:
        print(f"   FAIL - Status {response.status_code}")
        exit(1)
except Exception as e:
    print(f"   FAIL: {e}")
    exit(1)

# Step 3: Wait for stream to initialize
print("\n3. Waiting for stream to initialize...")
time.sleep(2)
print("   OK - Waited 2 seconds")

# Step 4: Test FFmpeg
print("\n4. Testing FFmpeg availability...")
try:
    result = subprocess.run(
        ["C:\\ffmpeg\\bin\\ffmpeg.exe", "-version"],
        capture_output=True,
        text=True,
        timeout=5
    )
    if result.returncode == 0:
        print("   OK - FFmpeg found")
    else:
        print("   FAIL - FFmpeg error")
        exit(1)
except FileNotFoundError:
    print("   FAIL - FFmpeg not found at C:\\ffmpeg\\bin\\ffmpeg.exe")
    exit(1)
except Exception as e:
    print(f"   FAIL: {e}")
    exit(1)

# Step 5: Try to receive UDP stream with FFmpeg (just test for a few seconds)
print("\n5. Testing UDP stream reception (5 seconds)...")
print("   Running FFmpeg to capture from udp://0.0.0.0:8554...")

try:
    # Try to capture a single frame to test
    cmd = [
        "C:\\ffmpeg\\bin\\ffmpeg.exe",
        "-y",  # Overwrite output
        "-fflags", "nobuffer",
        "-flags", "low_delay",
        "-i", "udp://0.0.0.0:8554",
        "-frames:v", "1",  # Just capture 1 frame
        "-f", "image2",
        "C:\\Users\\woody\\Desktop\\gopro-control\\test_frame.jpg"
    ]

    result = subprocess.run(cmd, capture_output=True, text=True, timeout=10)

    if result.returncode == 0:
        print("   OK - Captured test frame to test_frame.jpg")
    else:
        print(f"   FAIL - FFmpeg returned {result.returncode}")
        print(f"   stderr: {result.stderr[-500:] if result.stderr else 'none'}")

except subprocess.TimeoutExpired:
    print("   TIMEOUT - No stream data received in 10 seconds")
    print("   This likely means the GoPro isn't sending UDP data")
except Exception as e:
    print(f"   FAIL: {e}")

# Step 6: Stop the stream
print("\n6. Stopping GoPro preview stream...")
try:
    response = requests.get("http://10.5.5.9:8080/gopro/camera/stream/stop", timeout=5)
    print(f"   Status: {response.status_code}")
except Exception as e:
    print(f"   Error stopping: {e}")

print("\n" + "=" * 50)
print("Test complete")
print("=" * 50)
