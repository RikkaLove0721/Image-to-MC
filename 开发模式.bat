@echo off
setlocal
cd /d "%~dp0web"
echo Dev mode: http://localhost:4310
start "" /min powershell -NoProfile -Command "Start-Sleep 12; Start-Process 'http://localhost:4310'"
call npm run dev
