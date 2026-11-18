# 🛡️ SmartShield — Smart Road Safety Monitoring System

> **AI-powered pothole detection + flood monitoring using an ESP32-CAM, IoT sensors and Google Gemini.**

SmartShield is an IoT prototype meant to be mounted on a roadside pole. It watches the road through a camera, uses **Google Gemini AI** to spot potholes, measures **water-logging** with an ultrasonic sensor, detects **rain**, and raises **real-time alerts** on a web dashboard — with a buzzer and LEDs on the device itself.

![ESP32-CAM](https://img.shields.io/badge/Hardware-ESP32--CAM-E7352C?logo=espressif&logoColor=white)
![Python](https://img.shields.io/badge/Backend-Python%20%7C%20FastAPI-3776AB?logo=python&logoColor=white)
![Gemini](https://img.shields.io/badge/AI-Google%20Gemini-8E75B2?logo=googlegemini&logoColor=white)
![SQLite](https://img.shields.io/badge/Database-SQLite-003B57?logo=sqlite&logoColor=white)
![Arduino](https://img.shields.io/badge/Firmware-Arduino%20C%2B%2B-00979D?logo=arduino&logoColor=white)

---

## 📌 Problem Statement

- **Potholes** cause accidents and vehicle damage, but they are usually reported manually and late.
- **Water-logging** during rain hides potholes and makes roads dangerous, especially in underpasses.
- Authorities have **no live view** of road conditions, so action happens only after complaints.

**SmartShield** automates this: the device detects the problem, the AI confirms it, and the dashboard shows the alert with location, time, severity and a snapshot — instantly.

---

## ✨ Key Features

| Feature | Description |
|---|---|
| 🕳️ **AI Pothole Detection** | ESP32-CAM snapshots are analysed by Google Gemini, which returns pothole **severity** (low / medium / high) and approximate size. |
| 🌊 **Flood / Water-Level Alarm** | HC-SR04 ultrasonic sensor measures water depth. Above the limit → **buzzer + LEDs turn ON** on the device (works even without Wi-Fi) and a flood alert is logged. |
| 🌧️ **Rain-Triggered Alert Mode** | MH-RD rain sensor automatically switches the system into **Alert Mode**, where water level is checked every 0.5 s. |
| 🔁 **Smart 24-Hour Rule** | No duplicate alerts for the same pothole: while an alert is pending, repeats only increase its "seen" count. If still unresolved after 24 h, it is re-reported. |
| 📺 **Live Camera Stream** | MJPEG live video from the ESP32-CAM directly on the website. |
| 📊 **Live Dashboard** | All sensors live, alert feed, alert history with filters, CSV export, editable location. |
| 🧪 **Demo Mode** | Without hardware, the dashboard shows simulated readings — useful for presentations. |
| 🔐 **Authentication** | Email/password sign-up (PBKDF2-SHA256 hashed passwords) and optional **Google Sign-In**. |
| 🖼️ **Test With Photo** | Upload any road photo to test Gemini detection without the camera. |

---

## 🏗️ System Architecture

```
 ┌──────────────────────── Roadside Pole ────────────────────────┐
 │  ESP32-CAM (AI Thinker)                                        │
 │   ├─ OV2640 Camera ──► /stream (live video), /capture (photo)  │
 │   ├─ HC-SR04 Ultrasonic ─► water level                         │
 │   ├─ MH-RD Rain sensor  ─► rain / alert mode                   │
 │   └─ Buzzer + 2 LEDs    ◄─ local flood alarm                   │
 └───────────────┬────────────────────────────────────────────────┘
                 │  Wi-Fi (HTTP: /status, /capture, /config, /buzz)
                 ▼
 ┌──────────── Python Backend (FastAPI + SQLite) ─────────────┐
 │  • polls sensors every second   • runs pothole detection     │
 │  • 24-hour alert rule           • users, sessions, settings  │
 └───────────────┬───────────────────────────┬────────────────┘
                 │ REST API                   │ image
                 ▼                            ▼
       Web Dashboard (5 pages)        Google Gemini API
```

---

## 🛠️ Tech Stack

| Layer | Technology |
|---|---|
| **Hardware** | AI Thinker ESP32-CAM, HC-SR04 ultrasonic sensor, MH-RD rain sensor, buzzer, LEDs |
| **Firmware** | Arduino C++ (`esp_camera`, `esp_http_server`, `Preferences` for flash storage) |
| **Backend** | Python, FastAPI, Uvicorn, httpx (async), SQLite |
| **AI** | Google Gemini API (default model `gemini-3.5-flash-lite`, configurable) |
| **Frontend** | HTML5, CSS3 (glassmorphism, macOS-style dock), Vanilla JavaScript |
| **Auth** | PBKDF2 password hashing, session tokens, Google OAuth (optional) |

---

## 🖥️ Website Pages

| Page | What it does |
|---|---|
| `/login` | Email/password login and sign-up, Google login |
| `/intro` | Animated scroll story of the sensor pole with live sensor hotspots |
| `/dashboard` | Location, all sensors live, live alerts, alert history, CSV export |
| `/camera` | ESP32-CAM live stream, Gemini AI detection, test-with-photo |
| `/settings` | Profile, device IP, Gemini key & model, water limits, calibration, notifications |

---

## 📂 Project Structure

```
SmartShield/
├── backend/
│   ├── app.py               # FastAPI server: API, sensor loop, Gemini detection
│   ├── requirements.txt
│   └── .env.example         # copy to .env and add your keys
├── firmware/
│   └── SmartShield_ESP32CAM/
│       └── SmartShield_ESP32CAM.ino   # ESP32-CAM code
├── web/
│   ├── login.html  intro.html  dashboard.html  camera.html  settings.html
│   └── assets/              # common.css, common.js
├── docs/
│   └── SETUP_HINGLISH.md    # setup guide in Hinglish
├── run.bat                  # one-click start (Windows)
└── run.sh                   # start (Mac / Linux)
```

---

## 🚀 Getting Started

### 1. Run the website

**Requirements:** Python 3.10+ (tick **"Add python.exe to PATH"** while installing).

```bash
git clone https://github.com/kunalvermaug25-rgb/SmartShield.git
cd SmartShield
```

- **Windows:** double-click **`run.bat`**
- **Mac / Linux:** `./run.sh`

The first run creates a virtual environment and installs packages. Then open **http://localhost:8000**, sign up, and you're in.

> Until an ESP32 is connected, the dashboard runs in **Demo Mode** with simulated readings.

### 2. Add a Gemini API key

1. Create a key at [aistudio.google.com/apikey](https://aistudio.google.com/apikey).
2. Website → **Settings → AI Detection** → paste key → **Save**
   (or put it in `backend/.env` as `GEMINI_API_KEY=...`).
3. Turn **AI detection ON** on the Camera page.

The key stays on the backend only and is never sent to the browser.

### 3. Google Sign-In (optional)

1. In [Google Cloud Console](https://console.cloud.google.com), create an **OAuth client ID → Web application**.
2. Add `http://localhost:8000` to **Authorized JavaScript origins**.
3. Put the client ID in `backend/.env` as `GOOGLE_CLIENT_ID=...` and restart.

### 4. Flash the ESP32-CAM

1. Open `firmware/SmartShield_ESP32CAM/SmartShield_ESP32CAM.ino` in Arduino IDE.
2. Board: **AI Thinker ESP32-CAM**, Partition scheme: **Huge APP**.
3. Set `WIFI_SSID` / `WIFI_PASS` (and pins, if your wiring differs).
4. Upload → open Serial Monitor (115200) → note the IP address.
5. Website → **Camera** → enter the IP → **Connect**.

> The laptop and the ESP32 must be on the **same Wi-Fi / hotspot**.

---

## 🔌 Wiring (default pins)

| Component | ESP32-CAM Pin | Note |
|---|---|---|
| HC-SR04 TRIG | GPIO 13 | |
| HC-SR04 ECHO | GPIO 15 | Use a **1kΩ + 2kΩ voltage divider** (ECHO outputs 5 V) |
| Buzzer | GPIO 14 | |
| LED 1 / LED 2 | GPIO 12 / GPIO 2 | With 220 Ω resistors |
| MH-RD Rain DO | GPIO 3 (RX) | |
| HC-SR04 VCC | 5 V | All other modules on 3.3 V |

### ESP32 HTTP Endpoints

| Endpoint | Purpose |
|---|---|
| `http://<IP>:81/stream` | Live MJPEG video |
| `http://<IP>/capture` | Single JPEG snapshot (sent to Gemini) |
| `http://<IP>/status` | Sensor readings as JSON |
| `http://<IP>/config?limit=10&empty=20` | Update water limit / calibration (saved in flash) |
| `http://<IP>/buzz` | Test the buzzer |

---

## ⚙️ How It Works

1. **Water level** = distance to the empty box bottom − current distance to the water surface. Use **Settings → Calibrate now** when the box is empty.
2. **Water ≥ limit** → the ESP32 itself turns on the buzzer and both LEDs, and the dashboard logs a **Flood** alert.
3. **Rain detected** → the system enters **Alert Mode** automatically (faster water checks).
4. **Pothole** → camera snapshot → Gemini returns severity + size → alert with location, time and snapshot.
5. **24-hour rule** → prevents alert spam for the same unresolved pothole; re-alerts if it's still there after 24 h.

---

## 🎬 Demo Flow

1. Log in → scroll through the intro → open the Dashboard.
2. **Camera page** → hold a pothole photo in front of the camera → Gemini alert → toast + sound on the dashboard.
3. Put water drops on the **rain sensor** → dashboard shows *Rain detected · Alert mode*.
4. Pour water into the box until it crosses the limit → **buzzer + LEDs + Flood alarm**.

---

## 🧯 Troubleshooting

| Problem | Fix |
|---|---|
| Can't reach the camera | Wrong IP or different Wi-Fi. Open the ESP's IP in a browser to check. |
| Gemini error 4xx | Invalid key, or the model name changed — update it in **Settings → Model**. |
| Water reading is 0 / strange | Check the ECHO voltage divider, then **Calibrate now**. |
| Brownout / camera init failed | Power the ESP32-CAM from a **5 V 2 A** supply; USB-TTL 3.3 V is not enough. |

---

## 🔮 Future Scope

- GPS module for automatic pothole location
- SMS / WhatsApp alerts to municipal authorities
- Solar-powered pole unit
- Map view of all reported potholes across the city
- On-device (edge) AI model to reduce cloud calls

---

## 👥 Team

Built as an IoT course project by BTech CSE (AI/ML) students at **IILM University, Gurugram**.

---

⭐ If you found this project useful, consider giving it a star!
