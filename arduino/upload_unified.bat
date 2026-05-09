@echo off
setlocal enabledelayedexpansion

cd /d "%~dp0"

echo =============================
echo Stopping GoPro server (frees COM port)...
echo =============================
taskkill /f /fi "WINDOWTITLE eq GoPro*" /im python.exe >nul 2>&1
taskkill /f /fi "COMMANDLINE eq *main.py*" /im python.exe >nul 2>&1
timeout /t 2 /nobreak >nul

echo =============================
echo Compiling unified_controller...
echo =============================

arduino-cli compile --fqbn arduino:avr:mega unified_controller

IF %ERRORLEVEL% NEQ 0 (
    echo Compilation failed!
    pause
    exit /b %ERRORLEVEL%
)

echo =============================
echo Detecting Arduino Mega...
echo =============================

set MEGA_PORT=
for /f "tokens=1,2 delims==" %%a in ('wmic path Win32_SerialPort get DeviceID^,Description /format:list ^| find "="') do (
    if "%%a"=="Description" set DESC=%%b
    if "%%a"=="DeviceID" (
        echo %%b - !DESC!
        echo !DESC! | find /i "Mega" >nul && set MEGA_PORT=%%b
    )
)

if "%MEGA_PORT%"=="" (
    echo Arduino Mega not found! Check USB connection.
    pause
    exit /b 1
)

echo Found Arduino Mega on %MEGA_PORT%
echo =============================
echo Uploading to %MEGA_PORT%...
echo =============================

arduino-cli upload -p %MEGA_PORT% --fqbn arduino:avr:mega unified_controller

IF %ERRORLEVEL% NEQ 0 (
    echo Upload failed!
    pause
    exit /b %ERRORLEVEL%
)

echo =============================
echo Done! unified_controller uploaded.
echo Lights, gimbal, and rail are all on one Arduino now.
echo =============================
pause
