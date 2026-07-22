@echo off
cd /d "d:\cuoti"
echo Starting server and tunnel...
start "" /MIN D:\anaconda\python.exe app.py
timeout /t 4 /nobreak >nul
echo Tunnel starting - keep this window open!
ssh -o StrictHostKeyChecking=no -o ServerAliveInterval=30 -R 80:localhost:5000 nokey@localhost.run
taskkill /FI "WINDOWTITLE eq Flask*" /F >nul 2>&1
pause
