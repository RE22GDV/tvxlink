@echo off
rem Manual installation of the TI TUSB3410 driver (Start-Windows.bat does this by itself).
rem Run as administrator. The driver files are fetched from the PROMAX NetUpdate package first.
chcp 65001 >nul
cd /d "%~dp0\..\.."
set "ARCH=x86"
if /i "%PROCESSOR_ARCHITECTURE%"=="AMD64" set "ARCH=x64"
if /i "%PROCESSOR_ARCHITEW6432%"=="AMD64" set "ARCH=x64"
set "PY=py -3"
if exist "%LOCALAPPDATA%\TVXLink\python\python.exe" set "PY=%LOCALAPPDATA%\TVXLink\python\python.exe"
%PY% -m tvxlink.vendor || goto failed
pnputil /add-driver "%LOCALAPPDATA%\TVXLink\vendor\TUSB3410\manual_%ARCH%\usbuart3410.inf" /install || goto failed
echo.
echo OK: reconnect the meter — "UMP USB Serial Port (COMn)" appears in Device Manager.
pause
exit /b 0
:failed
echo Driver installation failed. Device Manager - TUSB3410 Device - Update driver - Browse -
echo %LOCALAPPDATA%\TVXLink\vendor\TUSB3410\manual_%ARCH%
pause
exit /b 1
