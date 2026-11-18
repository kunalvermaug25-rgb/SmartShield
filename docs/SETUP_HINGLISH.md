# SmartShield 🛡️ — Road pothole + flood monitoring (ESP32-CAM prototype)

Website (5 pages) + Python backend + ESP32-CAM firmware.

| Page | Design | Kya hai |
|---|---|---|
| `/login` | Mossline | Email/password login + sign up, Google login |
| `/intro` | Mossline scroll story | Floating island → sensor pole fly-in, live sensor hotspots |
| `/dashboard` | Mossline glass | Location (editable), saare sensors live, live alerts, alert history |
| `/camera` | Strata AI (dark) | ESP32-CAM stream, Gemini AI detection, test-with-photo |
| `/settings` | Mainframe | Profile/photo, device, Gemini key, limits, notifications, data |

Left side pe macOS jaisa glass dock hai (hover pe icons magnify hote hain).

---

## 1. Website chalana (Windows)

1. Python 3.10+ install hona chahiye (python.org, install karte time **"Add to PATH"** tick karna).
2. Folder mein **`run.bat`** pe double-click karo.
   Pehli baar packages install honge, phir browser mein `http://localhost:8000` khul jaayega.
3. **Sign up** karo (email + password) → intro → dashboard.

Mac/Linux: `./run.sh`

> Jab tak ESP32 connect nahi hai, dashboard **Demo mode** mein simulated readings dikhata hai.
> ESP ka IP daalte hi demo mode apne aap band ho jaata hai.

## 2. Gemini API key
1. https://aistudio.google.com/apikey se key banao.
2. Website → **Settings → Tune detection** → key paste → *Save key*.
   (Ya `backend/.env` file mein `GEMINI_API_KEY=` ke aage daal do.)
3. Camera page pe **AI detection** ON karo.

Key sirf backend mein rehti hai — browser ko kabhi wapas nahi bheji jaati.
Default model `gemini-3.5-flash-lite` (fast + sasta). Settings se change kar sakte ho.

Camera ke bina test: Camera page → **Test with photo** → kisi pothole ki photo choose karo.

## 3. Google login (optional)
1. https://console.cloud.google.com → naya project → **APIs & Services → OAuth consent screen** (External, app name SmartShield, apna email).
2. **Credentials → Create credentials → OAuth client ID → Web application**.
3. *Authorized JavaScript origins* mein `http://localhost:8000` add karo → Create.
4. Client ID copy karke `backend/.env` mein: `GOOGLE_CLIENT_ID=xxxx.apps.googleusercontent.com`
5. `run.bat` dobara chalao. Login page pe Google ka button aa jaayega.

## 4. ESP32-CAM firmware
File: `firmware/SmartShield_ESP32CAM/SmartShield_ESP32CAM.ino`

1. Arduino IDE → Board: **AI Thinker ESP32-CAM**, Partition: **Huge APP**.
2. File ke upar **WIFI_SSID / WIFI_PASS** aur **PINS** apni wiring ke hisaab se badlo.
3. Upload → Serial Monitor (115200) mein IP dikhega, jaise `192.168.1.50`.
4. Website → Camera page → IP daalo → **Connect**.

Default pins (zaroorat ho toh badal lo):

| Part | ESP32-CAM pin |
|---|---|
| HC-SR04 TRIG | GPIO 13 |
| HC-SR04 ECHO | GPIO 15 (**1k + 2k voltage divider**, ECHO 5V deta hai) |
| Buzzer IO | GPIO 14 |
| LED 1 / LED 2 | GPIO 12 / GPIO 2 (220Ω resistor ke saath) |
| MH-RD DO | GPIO 3 (RX) |
| HC-SR04 VCC | 5V · baaki sab VCC 3.3V |

ESP32 ke endpoints:
- `http://IP:81/stream` live video → website pe dikhta hai
- `http://IP/capture` ek photo → backend Gemini ko bhejta hai
- `http://IP/status` sensors ka JSON → dashboard har second padhta hai
- `http://IP/config?limit=10&empty=20` → website se limit change hoti hai (flash mein save)
- `http://IP/buzz` → Settings ka "Test buzzer"

**Laptop aur ESP32 same Wi-Fi / hotspot pe hone chahiye.**

## 5. Logic
- **Water level** = (sensor se khaali box ka bottom) − (abhi ki distance). Settings → *Calibrate now* (box khaali ho tab).
- Water ≥ limit → buzzer + dono LED ON (ESP khud karta hai, Wi-Fi ke bina bhi). Dashboard pe Flood alert log hota hai.
- **Rain detected** → system apne aap **ALERT mode** (water har 0.5 s check). Koi message nahi jaata.
- **Pothole** → Gemini severity (low/medium/high) + size batata hai → alert with location, time, snapshot.
- **24 ghante wala rule:** same location pe Pending alert hai toh 24 h tak naya alert nahi (sirf "seen N×" badhta hai).
  24 h baad bhi pothole hai → "Still unresolved — reported again". Resolved mark karne ke baad dikha → turant naya alert.

## 6. Demo script
1. Login → intro scroll karke dikhao → Dashboard.
2. Camera page → phone pe pothole ki photo camera ke saamne → Gemini alert → dashboard pe toast + sound.
3. Rain sensor pe paani ki boondein → Dashboard: *Rain detected · Alert mode*.
4. Box mein paani daalo → limit cross → buzzer + LEDs + *Flood alarm*.

## Troubleshooting
- **Can't reach the camera** → IP galat ya alag Wi-Fi. ESP ka IP browser mein khol ke check karo.
- **Gemini error 4xx** → key galat, ya model ka naam badal gaya ho (Settings → Model).
- **Water reading 0 / ajeeb** → ECHO divider check karo, *Calibrate now* karo.
- **Brownout / camera init failed** → ESP32-CAM ko 5V 2A supply do, USB-TTL ka 3.3V kaafi nahi hota.
