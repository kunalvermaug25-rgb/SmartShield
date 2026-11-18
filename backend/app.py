"""
SmartShield backend — FastAPI
Road pothole + flood monitoring for an ESP32-CAM prototype.

Run:  python -m uvicorn app:app --reload --port 8000   (from the backend folder)
Then open http://localhost:8000
"""
import asyncio
import base64
import csv
import hashlib
import io
import json
import math
import os
import re
import secrets
import sqlite3
import threading
import time
from collections import deque
from contextlib import asynccontextmanager
from pathlib import Path

import httpx
from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles

BASE = Path(__file__).resolve().parent
ROOT = BASE.parent
WEB = ROOT / "web"
DATA = BASE / "data"
SNAPS = DATA / "snaps"
SNAPS.mkdir(parents=True, exist_ok=True)
load_dotenv(BASE / ".env")

GOOGLE_CLIENT_ID = os.getenv("GOOGLE_CLIENT_ID", "").strip()
ENV_GEMINI_KEY = os.getenv("GEMINI_API_KEY", "").strip()
SESSION_COOKIE = "ss_session"
DAY = 24 * 3600

# --------------------------------------------------------------------------- DB
_lock = threading.Lock()
db = sqlite3.connect(DATA / "smartshield.db", check_same_thread=False)
db.row_factory = sqlite3.Row
db.executescript(
    """
CREATE TABLE IF NOT EXISTS users(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  email TEXT UNIQUE NOT NULL,
  name TEXT NOT NULL,
  password_hash TEXT,
  google_sub TEXT,
  avatar TEXT,
  created_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS sessions(
  token TEXT PRIMARY KEY,
  user_id INTEGER NOT NULL,
  created_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS settings(
  key TEXT PRIMARY KEY,
  value TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS alerts(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  type TEXT NOT NULL,              -- pothole | flood
  location TEXT NOT NULL,
  lat REAL, lon REAL,
  severity TEXT NOT NULL,          -- low | medium | high
  size TEXT,
  confidence REAL,
  description TEXT,
  status TEXT NOT NULL,            -- Pending | Resolved | Active | Cleared
  snapshot TEXT,
  created_at REAL NOT NULL,
  last_seen REAL NOT NULL,
  seen_count INTEGER NOT NULL DEFAULT 1,
  resolved_at REAL,
  repeat_of INTEGER,
  source TEXT
);
"""
)
db.commit()


def q(sql, args=(), one=False):
    with _lock:
        cur = db.execute(sql, args)
        rows = cur.fetchall()
        db.commit()
        if one:
            return rows[0] if rows else None
        return rows


def exec_(sql, args=()):
    with _lock:
        cur = db.execute(sql, args)
        db.commit()
        return cur.lastrowid


# --------------------------------------------------------------------- Settings
DEFAULTS = {
    "location_name": "Sector 21, Dwarka, New Delhi",
    "lat": 28.5523,
    "lon": 77.0583,
    "esp_ip": "",
    "demo_mode": True,
    "water_limit_cm": 10.0,
    "empty_cm": 20.0,
    "ai_enabled": False,
    "detect_interval": 5,
    "min_severity": "low",
    "gemini_model": "gemini-3.5-flash-lite",
    "gemini_key": "",
    "sound": True,
    "notifications": True,
    "accent": "lime",
    "theme": "light",
}
SECRET_KEYS = {"gemini_key"}


def get_settings():
    s = dict(DEFAULTS)
    for r in q("SELECT key,value FROM settings"):
        try:
            s[r["key"]] = json.loads(r["value"])
        except Exception:
            pass
    return s


def set_setting(k, v):
    exec_("INSERT INTO settings(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (k, json.dumps(v)))


def gemini_key():
    return (get_settings().get("gemini_key") or ENV_GEMINI_KEY or "").strip()


def public_settings():
    s = get_settings()
    out = {k: v for k, v in s.items() if k not in SECRET_KEYS}
    key = gemini_key()
    out["gemini_key_set"] = bool(key)
    out["gemini_key_hint"] = ("••••" + key[-4:]) if key else ""
    out["gemini_key_from_env"] = bool(ENV_GEMINI_KEY) and not s.get("gemini_key")
    return out


# ------------------------------------------------------------------------- Auth
def hash_pw(pw: str) -> str:
    salt = secrets.token_hex(16)
    h = hashlib.pbkdf2_hmac("sha256", pw.encode(), salt.encode(), 200_000).hex()
    return f"{salt}${h}"


def check_pw(pw: str, stored: str) -> bool:
    if not stored or "$" not in stored:
        return False
    salt, h = stored.split("$", 1)
    return secrets.compare_digest(hashlib.pbkdf2_hmac("sha256", pw.encode(), salt.encode(), 200_000).hex(), h)


def user_out(u):
    return {
        "id": u["id"],
        "email": u["email"],
        "name": u["name"],
        "avatar": u["avatar"],
        "google": bool(u["google_sub"]),
        "has_password": bool(u["password_hash"]),
        "created_at": u["created_at"],
    }


def current_user(request: Request):
    tok = request.cookies.get(SESSION_COOKIE)
    if not tok:
        return None
    return q("SELECT u.* FROM sessions s JOIN users u ON u.id=s.user_id WHERE s.token=?", (tok,), one=True)


def require_user(request: Request):
    u = current_user(request)
    if not u:
        raise HTTPException(401, "Please log in")
    return u


def start_session(resp: Response, user_id: int):
    tok = secrets.token_urlsafe(32)
    exec_("INSERT INTO sessions(token,user_id,created_at) VALUES(?,?,?)", (tok, user_id, time.time()))
    resp.set_cookie(SESSION_COOKIE, tok, httponly=True, samesite="lax", max_age=30 * DAY)


# ---------------------------------------------------------------- Live sensor state
sensors = {
    "online": False,
    "source": "offline",  # esp | demo | offline
    "distance_cm": None,
    "water_cm": None,
    "rain": False,
    "buzzer": False,
    "led1": False,
    "led2": False,
    "mode": "NORMAL",  # NORMAL | ALERT (rain) | FLOOD
    "limit_cm": DEFAULTS["water_limit_cm"],
    "rssi": None,
    "updated": None,
    "error": None,
}
history = deque(maxlen=180)  # (t, water_cm, rain)
camera = {
    "frames": 0,
    "potholes": 0,
    "suppressed": 0,
    "last_latency_ms": None,
    "last_result": None,
    "last_error": None,
    "last_run": None,
    "busy": False,
}
_flood_active_id = None
_last_pushed_cfg = None


def esp_base(ip: str) -> str:
    ip = (ip or "").strip().rstrip("/")
    if not ip:
        return ""
    if not ip.startswith("http"):
        ip = "http://" + ip
    return ip


def track_flood(water, limit, loc, lat, lon):
    """Log a flood event when water crosses the limit; clear it when it drops."""
    global _flood_active_id
    if water is None:
        return
    if water >= limit and _flood_active_id is None:
        over = water - limit
        sev = "high" if over >= 3 else "medium"
        _flood_active_id = exec_(
            "INSERT INTO alerts(type,location,lat,lon,severity,size,confidence,description,status,created_at,last_seen,source)"
            " VALUES('flood',?,?,?,?,?,1,?, 'Active',?,?, 'esp')",
            (loc, lat, lon, sev, f"{water:.1f} cm (limit {limit:.0f} cm)",
             "Water level crossed the safety limit — buzzer and LEDs triggered", time.time(), time.time()),
        )
    elif _flood_active_id is not None:
        if water < limit * 0.9:
            exec_("UPDATE alerts SET status='Cleared', resolved_at=?, last_seen=? WHERE id=?", (time.time(), time.time(), _flood_active_id))
            _flood_active_id = None
        else:
            exec_("UPDATE alerts SET last_seen=?, size=? WHERE id=?", (time.time(), f"{water:.1f} cm (limit {limit:.0f} cm)", _flood_active_id))


def demo_reading(s):
    """Simulated sensors so the dashboard is alive before the ESP is connected."""
    t = time.time()
    cycle = (t % 120) / 120  # 2 minute cycle
    rain = 0.15 < cycle < 0.65
    limit, empty = float(s["water_limit_cm"]), float(s["empty_cm"])
    base = 2.0 + 1.0 * math.sin(t / 7)
    if rain:
        base += (limit + 2.5) * math.sin(math.pi * (cycle - 0.15) / 0.6)
    water = max(0.0, min(empty, base))
    flood = water >= limit
    return {
        "distance_cm": round(empty - water, 1),
        "water_cm": round(water, 1),
        "rain": rain,
        "buzzer": flood,
        "led1": flood,
        "led2": flood,
        "mode": "FLOOD" if flood else ("ALERT" if rain else "NORMAL"),
        "limit_cm": limit,
        "rssi": -58,
    }


async def push_config(client, base, s):
    global _last_pushed_cfg
    cfg = (base, float(s["water_limit_cm"]), float(s["empty_cm"]))
    if cfg == _last_pushed_cfg:
        return
    try:
        await client.get(f"{base}/config", params={"limit": cfg[1], "empty": cfg[2]}, timeout=3)
        _last_pushed_cfg = cfg
    except Exception:
        pass


async def sensor_loop():
    async with httpx.AsyncClient() as client:
        while True:
            s = get_settings()
            base = esp_base(s["esp_ip"])
            interval = 1.0
            try:
                if base and not s["demo_mode"]:
                    await push_config(client, base, s)
                    r = await client.get(f"{base}/status", timeout=2.5)
                    d = r.json()
                    sensors.update(
                        online=True, source="esp", error=None, updated=time.time(),
                        distance_cm=d.get("distance_cm"), water_cm=d.get("water_cm"),
                        rain=bool(d.get("rain")), buzzer=bool(d.get("buzzer")),
                        led1=bool(d.get("led1")), led2=bool(d.get("led2")),
                        mode=d.get("mode", "NORMAL"), limit_cm=d.get("limit_cm", s["water_limit_cm"]),
                        rssi=d.get("rssi"),
                    )
                    track_flood(sensors["water_cm"], float(s["water_limit_cm"]), s["location_name"], s["lat"], s["lon"])
                    if sensors["rain"]:
                        interval = 0.5  # alert mode: sample faster
                elif s["demo_mode"]:
                    sensors.update(online=True, source="demo", error=None, updated=time.time(), **demo_reading(s))
                else:
                    sensors.update(online=False, source="offline", error="ESP32 IP not set" if not base else None)
            except Exception as e:
                sensors.update(online=False, source="offline", error=f"ESP32 not reachable ({type(e).__name__})")
            if sensors["online"]:
                history.append((round(time.time(), 1), sensors["water_cm"], sensors["rain"]))
            await asyncio.sleep(interval)


# ----------------------------------------------------------------------- Gemini
SEV_RANK = {"none": 0, "low": 1, "medium": 2, "high": 3}
PROMPT = """You are the vision module of SmartShield, a road-safety monitoring system.
Look at this camera frame and decide whether it shows a road POTHOLE (a hole, depression or broken patch in a road surface).
This is a prototype demo: if a pothole is shown on a phone screen, monitor or printed photo held up to the camera, COUNT IT as detected.
Do not report thin cracks, painted lines, shadows, manhole covers or puddles on flat road as potholes.

Severity by apparent size/depth:
- low: small or shallow (roughly under 15 cm across)
- medium: 15–50 cm across, or clearly deep but small
- high: over 50 cm across, very deep, water-filled, or several potholes together

Reply with JSON only:
{"pothole": true|false, "count": <int>, "severity": "none"|"low"|"medium"|"high",
 "size_estimate": "<short text like '≈30 cm wide, shallow'>", "confidence": <0..1>,
 "description": "<one short sentence>"}"""


def parse_json(text: str):
    text = text.strip()
    m = re.search(r"\{.*\}", text, re.S)
    return json.loads(m.group(0) if m else text)


async def gemini_detect(client, jpeg: bytes, s):
    key = gemini_key()
    if not key:
        raise RuntimeError("Gemini API key not set (Settings → AI Detection, or backend/.env)")
    model = s.get("gemini_model") or DEFAULTS["gemini_model"]
    body = {
        "contents": [{
            "role": "user",
            "parts": [
                {"inline_data": {"mime_type": "image/jpeg", "data": base64.b64encode(jpeg).decode()}},
                {"text": PROMPT},
            ],
        }],
        "generationConfig": {"responseMimeType": "application/json"},
    }
    r = await client.post(
        f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent",
        headers={"x-goog-api-key": key, "Content-Type": "application/json"},
        json=body, timeout=30,
    )
    if r.status_code != 200:
        try:
            msg = r.json().get("error", {}).get("message", r.text)
        except Exception:
            msg = r.text
        raise RuntimeError(f"Gemini error {r.status_code}: {msg[:200]}")
    data = r.json()
    text = "".join(p.get("text", "") for p in data["candidates"][0]["content"]["parts"] if not p.get("thought"))
    res = parse_json(text)
    res["pothole"] = bool(res.get("pothole"))
    sev = str(res.get("severity", "none")).lower()
    res["severity"] = sev if sev in SEV_RANK else ("medium" if res["pothole"] else "none")
    try:
        res["confidence"] = float(res.get("confidence", 0))
    except Exception:
        res["confidence"] = 0.0
    return res


def save_snapshot(jpeg: bytes) -> str:
    name = f"{int(time.time()*1000)}_{secrets.token_hex(3)}.jpg"
    (SNAPS / name).write_bytes(jpeg)
    return f"/snaps/{name}"


def handle_detection(res, jpeg, s, source):
    """Create / suppress a pothole alert using the 24-hour rule. Returns (alert_row|None, note)."""
    if not res["pothole"]:
        return None, "No pothole"
    if SEV_RANK[res["severity"]] < SEV_RANK.get(s["min_severity"], 1):
        return None, f"Below minimum severity ({s['min_severity']})"
    now = time.time()
    loc = s["location_name"]
    pending = q("SELECT * FROM alerts WHERE type='pothole' AND location=? AND status='Pending' ORDER BY created_at DESC LIMIT 1", (loc,), one=True)
    if pending and now - pending["created_at"] < DAY:
        new_sev = res["severity"] if SEV_RANK[res["severity"]] > SEV_RANK[pending["severity"]] else pending["severity"]
        exec_("UPDATE alerts SET last_seen=?, seen_count=seen_count+1, severity=? WHERE id=?", (now, new_sev, pending["id"]))
        camera["suppressed"] += 1
        return None, f"Already reported (alert #{pending['id']}) — next re-alert after 24 h"
    snap = save_snapshot(jpeg)
    desc = res.get("description") or "Pothole detected"
    repeat_of = None
    if pending:  # still unresolved after 24 h
        repeat_of = pending["id"]
        desc = f"Still unresolved after 24 h — reported again. {desc}"
    aid = exec_(
        "INSERT INTO alerts(type,location,lat,lon,severity,size,confidence,description,status,snapshot,created_at,last_seen,repeat_of,source)"
        " VALUES('pothole',?,?,?,?,?,?,?,'Pending',?,?,?,?,?)",
        (loc, s["lat"], s["lon"], res["severity"], res.get("size_estimate"), res["confidence"], desc, snap, now, now, repeat_of, source),
    )
    camera["potholes"] += 1
    return q("SELECT * FROM alerts WHERE id=?", (aid,), one=True), "New alert created"


async def run_detection(client, jpeg: bytes, source: str):
    s = get_settings()
    t0 = time.time()
    camera["busy"] = True
    try:
        res = await gemini_detect(client, jpeg, s)
        camera["frames"] += 1
        camera["last_latency_ms"] = int((time.time() - t0) * 1000)
        alert, note = handle_detection(res, jpeg, s, source)
        res["note"] = note
        res["alert_id"] = alert["id"] if alert else None
        res["at"] = time.time()
        camera["last_result"] = res
        camera["last_error"] = None
        return res, alert
    except Exception as e:
        camera["last_error"] = str(e)
        raise
    finally:
        camera["busy"] = False
        camera["last_run"] = time.time()


async def detect_loop():
    async with httpx.AsyncClient() as client:
        while True:
            s = get_settings()
            wait = max(2, int(s.get("detect_interval") or 5))
            base = esp_base(s["esp_ip"])
            if s["ai_enabled"] and base and gemini_key() and not camera["busy"]:
                try:
                    r = await client.get(f"{base}/capture", timeout=8)
                    if r.status_code == 200 and r.content:
                        await run_detection(client, r.content, "esp")
                    else:
                        camera["last_error"] = f"Camera capture failed (HTTP {r.status_code})"
                except Exception as e:
                    if not camera["last_error"]:
                        camera["last_error"] = f"Camera not reachable ({type(e).__name__})"
            await asyncio.sleep(wait)


# ------------------------------------------------------------------------- App
@asynccontextmanager
async def lifespan(app):
    tasks = [asyncio.create_task(sensor_loop()), asyncio.create_task(detect_loop())]
    yield
    for t in tasks:
        t.cancel()


app = FastAPI(title="SmartShield", lifespan=lifespan)
app.mount("/assets", StaticFiles(directory=WEB / "assets"), name="assets")
app.mount("/snaps", StaticFiles(directory=SNAPS), name="snaps")


def page(name):
    return FileResponse(WEB / f"{name}.html", headers={"Cache-Control": "no-store"})


@app.get("/")
def root(request: Request):
    return RedirectResponse("/intro" if current_user(request) else "/login")


@app.get("/login")
def login_page(request: Request):
    if current_user(request):
        return RedirectResponse("/intro")
    return page("login")


for _p in ("intro", "dashboard", "camera", "settings"):
    def _make(p):
        def handler(request: Request):
            if not current_user(request):
                return RedirectResponse("/login")
            return page(p)
        return handler
    app.add_api_route(f"/{_p}", _make(_p), methods=["GET"])


# --- auth
@app.get("/api/public/config")
def public_config():
    return {"google_client_id": GOOGLE_CLIENT_ID}


@app.post("/api/auth/signup")
async def signup(request: Request):
    b = await request.json()
    name = (b.get("name") or "").strip()
    email = (b.get("email") or "").strip().lower()
    pw = b.get("password") or ""
    if not name or not re.match(r"^[^@\s]+@[^@\s]+\.[^@\s]+$", email):
        raise HTTPException(400, "Enter your name and a valid email")
    if len(pw) < 6:
        raise HTTPException(400, "Password must be at least 6 characters")
    ex = q("SELECT * FROM users WHERE email=?", (email,), one=True)
    if ex:
        raise HTTPException(409, "An account with this email already exists — log in instead")
    uid = exec_("INSERT INTO users(email,name,password_hash,created_at) VALUES(?,?,?,?)", (email, name, hash_pw(pw), time.time()))
    resp = JSONResponse({"ok": True, "user": user_out(q("SELECT * FROM users WHERE id=?", (uid,), one=True))})
    start_session(resp, uid)
    return resp


@app.post("/api/auth/login")
async def login(request: Request):
    b = await request.json()
    email = (b.get("email") or "").strip().lower()
    u = q("SELECT * FROM users WHERE email=?", (email,), one=True)
    if not u or not check_pw(b.get("password") or "", u["password_hash"]):
        if u and not u["password_hash"] and u["google_sub"]:
            raise HTTPException(401, "This account uses Google sign-in — use 'Continue with Google'")
        raise HTTPException(401, "Wrong email or password")
    resp = JSONResponse({"ok": True, "user": user_out(u)})
    start_session(resp, u["id"])
    return resp


@app.post("/api/auth/google")
async def google_login(request: Request):
    if not GOOGLE_CLIENT_ID:
        raise HTTPException(400, "Google sign-in is not configured (set GOOGLE_CLIENT_ID in backend/.env)")
    b = await request.json()
    cred = b.get("credential") or ""
    async with httpx.AsyncClient() as c:
        r = await c.get("https://oauth2.googleapis.com/tokeninfo", params={"id_token": cred}, timeout=10)
    if r.status_code != 200:
        raise HTTPException(401, "Google token rejected")
    info = r.json()
    if info.get("aud") != GOOGLE_CLIENT_ID or info.get("email_verified") not in ("true", True):
        raise HTTPException(401, "Google token is not for this app")
    email = info["email"].lower()
    u = q("SELECT * FROM users WHERE email=?", (email,), one=True)
    if u:
        exec_("UPDATE users SET google_sub=?, avatar=COALESCE(avatar,?) WHERE id=?", (info["sub"], info.get("picture"), u["id"]))
        uid = u["id"]
    else:
        uid = exec_(
            "INSERT INTO users(email,name,google_sub,avatar,created_at) VALUES(?,?,?,?,?)",
            (email, info.get("name") or email.split("@")[0], info["sub"], info.get("picture"), time.time()),
        )
    resp = JSONResponse({"ok": True, "user": user_out(q("SELECT * FROM users WHERE id=?", (uid,), one=True))})
    start_session(resp, uid)
    return resp


@app.post("/api/auth/logout")
def logout(request: Request):
    tok = request.cookies.get(SESSION_COOKIE)
    if tok:
        exec_("DELETE FROM sessions WHERE token=?", (tok,))
    resp = JSONResponse({"ok": True})
    resp.delete_cookie(SESSION_COOKIE)
    return resp


@app.get("/api/auth/me")
def me(request: Request):
    return user_out(require_user(request))


# --- profile
@app.patch("/api/profile")
async def update_profile(request: Request):
    u = require_user(request)
    b = await request.json()
    if "name" in b:
        name = (b["name"] or "").strip()
        if not name:
            raise HTTPException(400, "Name can't be empty")
        exec_("UPDATE users SET name=? WHERE id=?", (name[:60], u["id"]))
    if "avatar" in b:
        av = b["avatar"]
        if av and (not str(av).startswith("data:image/") or len(av) > 400_000):
            raise HTTPException(400, "Avatar must be an image under ~300 KB")
        exec_("UPDATE users SET avatar=? WHERE id=?", (av or None, u["id"]))
    return user_out(q("SELECT * FROM users WHERE id=?", (u["id"],), one=True))


@app.post("/api/profile/password")
async def change_password(request: Request):
    u = require_user(request)
    b = await request.json()
    new = b.get("new") or ""
    if len(new) < 6:
        raise HTTPException(400, "New password must be at least 6 characters")
    if u["password_hash"] and not check_pw(b.get("current") or "", u["password_hash"]):
        raise HTTPException(400, "Current password is wrong")
    exec_("UPDATE users SET password_hash=? WHERE id=?", (hash_pw(new), u["id"]))
    return {"ok": True}


# --- settings
@app.get("/api/settings")
def read_settings(request: Request):
    require_user(request)
    return public_settings()


@app.patch("/api/settings")
async def write_settings(request: Request):
    require_user(request)
    b = await request.json()
    for k, v in b.items():
        if k not in DEFAULTS:
            continue
        if k == "gemini_key":
            v = (v or "").strip()
        elif isinstance(DEFAULTS[k], bool):
            v = bool(v)
        elif isinstance(DEFAULTS[k], (int, float)) and not isinstance(DEFAULTS[k], bool):
            try:
                v = type(DEFAULTS[k])(v)
            except Exception:
                raise HTTPException(400, f"Invalid value for {k}")
        else:
            v = str(v).strip()
        if k == "esp_ip" and v:
            v = re.sub(r"^https?://", "", v).rstrip("/")
        set_setting(k, v)
    if b.get("esp_ip"):  # a real device was entered → leave demo mode unless told otherwise
        if "demo_mode" not in b:
            set_setting("demo_mode", False)
    return public_settings()


# --- live state for dashboard
def alert_out(a):
    return dict(a)


@app.get("/api/state")
def state(request: Request):
    require_user(request)
    s = public_settings()
    now = time.time()
    pending = q("SELECT COUNT(*) c FROM alerts WHERE type='pothole' AND status='Pending'", one=True)["c"]
    high = q("SELECT COUNT(*) c FROM alerts WHERE status IN('Pending','Active') AND severity='high'", one=True)["c"]
    today = q("SELECT COUNT(*) c FROM alerts WHERE created_at>?", (now - DAY,), one=True)["c"]
    resolved = q("SELECT COUNT(*) c FROM alerts WHERE status IN('Resolved','Cleared')", one=True)["c"]
    latest = [alert_out(a) for a in q("SELECT * FROM alerts ORDER BY created_at DESC LIMIT 6")]
    return {
        "server_time": now,
        "sensors": sensors,
        "history": list(history)[-90:],
        "settings": {k: s[k] for k in ("location_name", "lat", "lon", "water_limit_cm", "empty_cm", "esp_ip", "demo_mode", "ai_enabled", "sound", "notifications")},
        "camera": {**camera, "gemini_key_set": s["gemini_key_set"]},
        "summary": {"pending": pending, "high": high, "today": today, "resolved": resolved},
        "latest_alerts": latest,
        "max_alert_id": (q("SELECT MAX(id) m FROM alerts", one=True)["m"] or 0),
    }


# --- alerts
@app.get("/api/alerts")
def list_alerts(request: Request, type: str = "", status: str = "", severity: str = "", search: str = "", limit: int = 200):
    require_user(request)
    sql, args = "SELECT * FROM alerts WHERE 1=1", []
    if type:
        sql += " AND type=?"; args.append(type)
    if status:
        sql += " AND status=?"; args.append(status)
    if severity:
        sql += " AND severity=?"; args.append(severity)
    if search:
        sql += " AND (location LIKE ? OR description LIKE ?)"; args += [f"%{search}%"] * 2
    sql += " ORDER BY created_at DESC LIMIT ?"; args.append(min(limit, 1000))
    return [alert_out(a) for a in q(sql, args)]


@app.patch("/api/alerts/{aid}")
async def update_alert(aid: int, request: Request):
    require_user(request)
    b = await request.json()
    st = b.get("status")
    if st not in ("Pending", "Resolved", "Active", "Cleared"):
        raise HTTPException(400, "Bad status")
    exec_("UPDATE alerts SET status=?, resolved_at=? WHERE id=?", (st, time.time() if st in ("Resolved", "Cleared") else None, aid))
    a = q("SELECT * FROM alerts WHERE id=?", (aid,), one=True)
    if not a:
        raise HTTPException(404, "Alert not found")
    return alert_out(a)


@app.delete("/api/alerts")
def clear_alerts(request: Request):
    global _flood_active_id
    require_user(request)
    exec_("DELETE FROM alerts")
    _flood_active_id = None
    for f in SNAPS.glob("*.jpg"):
        f.unlink(missing_ok=True)
    return {"ok": True}


@app.get("/api/alerts/export.csv")
def export_csv(request: Request):
    require_user(request)
    buf = io.StringIO()
    w = csv.writer(buf)
    cols = ["id", "type", "location", "lat", "lon", "severity", "size", "confidence", "status", "description", "created_at", "last_seen", "seen_count", "resolved_at"]
    w.writerow(cols)
    for a in q("SELECT * FROM alerts ORDER BY created_at DESC"):
        row = []
        for c in cols:
            v = a[c]
            if c in ("created_at", "last_seen", "resolved_at") and v:
                v = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(v))
            row.append(v)
        w.writerow(row)
    return StreamingResponse(iter([buf.getvalue()]), media_type="text/csv",
                             headers={"Content-Disposition": "attachment; filename=smartshield_alerts.csv"})


# --- camera / AI
@app.get("/api/camera/stats")
def camera_stats(request: Request):
    require_user(request)
    return {**camera, "gemini_key_set": bool(gemini_key()), "settings": {k: v for k, v in public_settings().items() if k in ("esp_ip", "ai_enabled", "detect_interval", "gemini_model", "min_severity")}}


@app.post("/api/camera/analyze")
async def analyze(request: Request):
    """Analyze an uploaded image (data URL) or, if none is given, grab a frame from the ESP32-CAM."""
    require_user(request)
    b = {}
    try:
        b = await request.json()
    except Exception:
        pass
    async with httpx.AsyncClient() as client:
        img = b.get("image")
        if img:
            jpeg = base64.b64decode(img.split(",", 1)[-1])
            source = "upload"
        else:
            base = esp_base(get_settings()["esp_ip"])
            if not base:
                raise HTTPException(400, "Set the ESP32-CAM IP first")
            try:
                r = await client.get(f"{base}/capture", timeout=8)
                r.raise_for_status()
            except Exception as e:
                raise HTTPException(502, f"Could not grab a frame from the ESP32-CAM ({type(e).__name__})")
            jpeg, source = r.content, "esp"
        try:
            res, alert = await run_detection(client, jpeg, source)
        except Exception as e:
            raise HTTPException(502, str(e))
    return {"result": res, "alert": alert_out(alert) if alert else None}


# --- device control
@app.post("/api/device/test")
async def device_test(request: Request):
    require_user(request)
    b = {}
    try:
        b = await request.json()
    except Exception:
        pass
    base = esp_base(b.get("ip") or get_settings()["esp_ip"])
    if not base:
        raise HTTPException(400, "Enter the ESP32 IP address")
    t0 = time.time()
    try:
        async with httpx.AsyncClient() as c:
            r = await c.get(f"{base}/status", timeout=4)
            d = r.json()
        return {"ok": True, "ms": int((time.time() - t0) * 1000), "status": d}
    except Exception as e:
        raise HTTPException(502, f"ESP32 not reachable at {base} ({type(e).__name__}). Same Wi-Fi? Correct IP?")


@app.post("/api/device/buzz")
async def device_buzz(request: Request):
    require_user(request)
    s = get_settings()
    if s["demo_mode"] or not s["esp_ip"]:
        return {"ok": True, "demo": True}
    try:
        async with httpx.AsyncClient() as c:
            await c.get(f"{esp_base(s['esp_ip'])}/buzz", params={"ms": 800}, timeout=4)
        return {"ok": True}
    except Exception as e:
        raise HTTPException(502, f"ESP32 not reachable ({type(e).__name__})")


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("app:app", host="0.0.0.0", port=int(os.getenv("PORT", "8000")), reload=False)
