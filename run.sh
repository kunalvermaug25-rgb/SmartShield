#!/usr/bin/env bash
cd "$(dirname "$0")/backend"
[ -d .venv ] || python3 -m venv .venv
source .venv/bin/activate
pip install -q -r requirements.txt
[ -f .env ] || cp .env.example .env
echo "SmartShield running at http://localhost:8000"
python -m uvicorn app:app --host 0.0.0.0 --port 8000
