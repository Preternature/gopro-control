@echo off
echo === GoPro Detection ===
echo.

echo [1] WiFi scan - looking for GP* SSIDs:
netsh wlan show networks | findstr /i "SSID"
echo.

echo [2] USB adapters - 172.2x.x.x addresses (each GoPro USB shows one):
ipconfig | findstr /i "172.2"
echo.

echo [3] Testing WiFi IP (10.5.5.9):
curl -s --max-time 2 http://10.5.5.9:8080/gopro/camera/state >nul 2>&1
if %errorlevel%==0 (
    echo   REACHABLE - GoPro on WiFi at 10.5.5.9
) else (
    echo   Not found at 10.5.5.9
)

echo.
echo NOTE: For USB cameras, find the 172.2x.1xx.x address shown in [2] above.
echo       The GoPro IP will be that subnet with .51 at the end.
echo       Example: if you see 172.20.180.51 - that IS the GoPro.
echo.
pause
