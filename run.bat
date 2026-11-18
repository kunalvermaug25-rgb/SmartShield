@echo off
title SmartShield server
cd /d "%~dp0backend"

where python >nul 2>nul
if errorlevel 1 (
  echo [ERROR] Python nahi mila. python.org se install karo aur "Add python.exe to PATH" tick karna.
  pause
  exit /b 1
)

if not exist .venv\Scripts\python.exe (
  echo Virtual environment bana raha hoon...
  python -m venv .venv
)

echo Packages install ho rahe hain (pehli baar 1-2 minute lagega)...
.venv\Scripts\python.exe -m pip install --disable-pip-version-check -r requirements.txt
if errorlevel 1 (
  echo.
  echo [ERROR] Packages install nahi hue. Upar ka error dekho / screenshot bhejo.
  pause
  exit /b 1
)

if not exist .env copy .env.example .env >nul

echo.
echo ==========================================
echo   SmartShield chal raha hai:
echo   http://localhost:8000
echo   (ye window band mat karna)
echo ==========================================
echo.
start "" cmd /c "timeout /t 5 >nul & start http://localhost:8000"
.venv\Scripts\python.exe -m uvicorn app:app --host 0.0.0.0 --port 8000

echo.
echo [INFO] Server band ho gaya. Upar koi error ho toh screenshot bhejo.
pause
