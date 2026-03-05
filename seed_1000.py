import os
import random
import time
from datetime import datetime, timedelta, timezone

import requests

BACKEND_URL = os.getenv("BACKEND_URL", "https://carwash-backend-nq39.onrender.com")
INGEST_KEY = os.getenv("INGEST_KEY", "super_clave_segura_123")
DEVICE_ID = os.getenv("DEVICE_ID", "carwash-01")

# 26-28 Feb y 1-4 Mar (2026) UTC
ALLOWED_DATES = [
    (2026, 2, 26),
    (2026, 2, 27),
    (2026, 2, 28),
    (2026, 3, 1),
    (2026, 3, 2),
    (2026, 3, 3),
    (2026, 3, 4),
]

def iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")

def random_start_in_allowed_dates() -> datetime:
    y, m, d = random.choice(ALLOWED_DATES)

    # Sesgo a horas pico (opcional, puedes dejarlo)
    peak_hours = [10, 11, 12, 13, 14, 17, 18, 19, 20]
    hour = random.choice(peak_hours + list(range(0, 24)))

    return datetime(
        year=y, month=m, day=d,
        hour=hour,
        minute=random.randint(0, 59),
        second=random.randint(0, 59),
        tzinfo=timezone.utc
    )

def pick_duration_normal_with_rare_outliers() -> int:
    """
    Normal: 7-9s (promedio ~8)
    Outliers raros: 5,6,10,11,12,13 (muy pocos)
    """
    # ~2% outliers (ajusta si quieres aún menos: 0.5% - 1%)
    if random.random() < 0.02:
        return random.choice([5, 6, 10, 11, 12, 13])
    return random.randint(7, 9)

def make_session(session_id: int, scenario: str):
    start_at = random_start_in_allowed_dates()

    # Precio estable (puedes dejar 10 fijo si quieres)
    plan_price = random.choice([10.0, 15.0, 20.0])

    if scenario == "normal":
        duration = pick_duration_normal_with_rare_outliers()
        end_at = start_at + timedelta(seconds=duration)
        price = plan_price

    elif scenario == "extreme":
        # EXTREMOS pero coherentes con tu escala (segundos):
        # muy corto: 1-3s, o muy largo: 20-60s
        duration = random.choice([random.randint(1, 3), random.randint(20, 60)])
        end_at = start_at + timedelta(seconds=duration)

        # a veces precio incoherente para que ML lo detecte por "price"
        price = random.choice([5.0, plan_price, 50.0])

    elif scenario == "error":
        # errores para probar validación del backend
        if random.random() < 0.5:
            duration = random.randint(1, 10)
            end_at = start_at - timedelta(seconds=duration)  # endAt antes
            price = plan_price
        else:
            duration = random.randint(7, 9)
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

def run(batch_size=500, start_session_id=1, p_extreme=0.01, p_error=0.005, sleep_sec=0.0):
    """
    p_extreme y p_error bajos para que la mayoría sea normal 7-9s
    y pocos casos raros (para que ML lo note, pero no sea “ruidoso”).
    """
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
    # IMPORTANTE: usa start_session_id mayor a tu max actual para evitar colisiones.
    run(batch_size=500, start_session_id=1, p_extreme=0.01, p_error=0.005, sleep_sec=0.0)