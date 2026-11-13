@echo off
rem TVXLink for Windows 10/11: double-click. The first start installs everything
rem (Python if missing, packages, the TI TUSB3410 meter driver); later starts just open the program.
chcp 65001 >nul
setlocal
cd /d "%~dp0"
set "PYTHONUTF8=1"
set "STATE=%LOCALAPPDATA%\TVXLink"
set "EMB=%STATE%\python"
set "PYURL=https://www.python.org/ftp/python/3.12.10/python-3.12.10-embed-amd64.zip"
set "PYSHA=4ACBED6DD1C744B0376E3B1CF57CE906F9DC9E95E68824584C8099A63025A3C3"

set "PY="
if exist "%EMB%\python.exe" set "PY=%EMB%\python.exe" & goto run
py -3 -c "import sys; sys.exit(0 if sys.version_info >= (3, 9) else 1)" >nul 2>&1 && set "PY=py -3" && goto run
python -c "import sys; sys.exit(0 if sys.version_info >= (3, 9) else 1)" >nul 2>&1 && set "PY=python" && goto run

echo Python не знайдено — завантажую офіційний Python 3.12 (~11 МБ) у %EMB%
echo Python not found — downloading the official Python 3.12 (~11 MB) into %EMB%
if not exist "%STATE%" mkdir "%STATE%"
powershell -NoProfile -ExecutionPolicy Bypass -Command ^
  "$ErrorActionPreference='Stop'; [Net.ServicePointManager]::SecurityProtocol='Tls12';" ^
  "Invoke-WebRequest -UseBasicParsing '%PYURL%' -OutFile '%STATE%\python.zip';" ^
  "if ((Get-FileHash '%STATE%\python.zip' -Algorithm SHA256).Hash -ne '%PYSHA%') { throw 'SHA-256 mismatch' };" ^
  "Expand-Archive -Force '%STATE%\python.zip' '%EMB%';" ^
  "Set-Content -Encoding ASCII '%EMB%\python312._pth' 'python312.zip','.','Lib\site-packages','import site';" ^
  "Remove-Item '%STATE%\python.zip'"
if errorlevel 1 goto failed
set "PY=%EMB%\python.exe"

:run
%PY% launcher.py %*
if errorlevel 1 goto failed
exit /b 0

:failed
echo.
echo Щось пішло не так — текст помилки вище. Перевірте інтернет і запустіть ще раз.
echo Something went wrong — see the message above. Check the internet connection and run again.
pause
exit /b 1
