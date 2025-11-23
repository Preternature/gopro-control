@echo off
netsh wlan show networks | findstr /i "GP25102353" >nul
if %errorlevel%==0 (
    echo GoPro WiFi found: GP25102353
) else (
    echo GoPro WiFi NOT found. Make sure GoPro is on and use Quik app to activate WiFi.
)
pause
