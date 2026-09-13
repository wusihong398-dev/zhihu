@echo off
setlocal
cd /d "%~dp0"
py -3.12 -m pip install -r requirements.txt
py -3.12 -m PyInstaller --noconfirm --clean --onefile --windowed --name TOTOD-Windows-Publisher --collect-all playwright totod_publisher.py
echo.
echo Build complete: dist\TOTOD-Windows-Publisher.exe
pause
