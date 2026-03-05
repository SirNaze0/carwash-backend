import os
import random
import time
from datetime import datetime, timedelta, timezone

import requests

BACKEND_URL = os.getenv("BACKEND_URL", "https://carwash-backend-nq39.onrender.com")
INGEST_KEY = os.getenv("INGEST_KEY", "super_clave_segura_123")
DEVICE_ID = os.getenv("DEVICE_ID", "carwash-01")

def iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")

def make_session(session_id: int, scenario: str):
    """
    scenario:
      - normal: duración y precio coherentes
      - extreme: duraciones muy altas o muy bajas
      - error: endAt < startAt o price negativo para probar validación
    """
    now = datetime.now(timezone.utc)

    # Simula patrón de horas pico: sesgo a horas 10-14 y 17-20
    peak_hours = [10, 11, 12, 13, 14, 17, 18, 19, 20]
    hour = random.choice(peak_hours + list(range(0, 24)))
    start_at = now.replace(hour=hour, minute=random.randint(0, 59), second=random.randint(0, 59), microsecond=0)

    # Planes de precio
    plan_price = random.choice([10.0, 15.0, 20.0])

    if scenario == "normal":
        duration = random.randint(180, 600)  # 3 a 10 min
        end_at = start_at + timedelta(seconds=duration)
        price = plan_price

    elif scenario == "extreme":
        # extremos: muy corto o muy largo
        duration = random.choice([random.randint(10, 40), random.randint(1200, 3600)])
        end_at = start_at + timedelta(seconds=duration)
        # a veces precio incoherente (para que ML lo detecte luego)
        price = random.choice([5.0, plan_price, 50.0])

    elif scenario == "error":
        # errores para probar que el backend rechaza o valida
        if random.random() < 0.5:
            duration = random.randint(100, 400)
            end_at = start_at - timedelta(seconds=duration)  # endAt antes
            price = plan_price
        else:
            duration = random.randint(180, 600)
            end_at = start_at + timedelta(seconds=duration)
            price = -10.0  # inválido
    else:
        raise ValueError("Unknown scenario")

    payload = {
        "deviceId": DEVICE_ID,
        "sessionId": session_id,
        "startAt": iso(start_at),
        "endAt": iso(end_at),
        "durationSec": int((end_at - start_at).total_seconds()),
        "price": float(price),
    }
    return payload

def post_session(payload):
    url = f"{BACKEND_URL}/api/sessions"
    r = requests.post(
        url,
        json=payload,
        headers={
            "Content-Type": "application/json",
            "X-INGEST-KEY": INGEST_KEY,
        },
        timeout=20,
    )
    return r.status_code, r.text

def run(batch_size=200, start_session_id=1000, p_extreme=0.03, p_error=0.02, sleep_sec=0.0):
    ok = 0
    fail = 0

    for i in range(batch_size):
        sid = start_session_id + i

        x = random.random()
        if x < p_error:
            scenario = "error"
        elif x < p_error + p_extreme:
            scenario = "extreme"
        else:
            scenario = "normal"

        payload = make_session(sid, scenario)
        status, text = post_session(payload)

        if status in (200, 201):
            ok += 1
        else:
            fail += 1

        print(f"[{scenario}] sessionId={sid} -> {status} {text[:120]}")

        if sleep_sec > 0:
            time.sleep(sleep_sec)

    print(f"\nDONE. ok={ok} fail={fail}")

if __name__ == "__main__":
    run(batch_size=500, start_session_id=1, p_extreme=0.05, p_error=0.03, sleep_sec=0.0)