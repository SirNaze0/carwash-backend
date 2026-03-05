import os
from datetime import datetime
from typing import Optional, List

from fastapi import FastAPI, Header, HTTPException, Query
from pydantic import BaseModel, Field
import psycopg

from dotenv import load_dotenv
load_dotenv()

DATABASE_URL = os.getenv("DATABASE_URL")
INGEST_KEY = os.getenv("INGEST_KEY")

if not DATABASE_URL:
    raise RuntimeError("Falta DATABASE_URL en .env")
if not INGEST_KEY:
    raise RuntimeError("Falta INGEST_KEY en .env")

app = FastAPI(title="Carwash Backend", version="1.0.0")


# ---------- Schemas ----------
class SessionIn(BaseModel):
    deviceId: str = Field(..., example="carwash-01")
    sessionId: int = Field(..., example=15)
    startAt: datetime
    endAt: datetime
    durationSec: int = Field(..., ge=0)
    price: float = Field(..., ge=0)


class SessionOut(BaseModel):
    deviceId: str
    sessionId: int
    startAt: datetime
    endAt: datetime
    durationSec: int
    price: float
    createdAt: datetime
    isAnomaly: bool = False
    anomalyScore: Optional[float] = None


# ---------- Helpers ----------
def auth_ingest(x_ingest_key: Optional[str]):
    if x_ingest_key != INGEST_KEY:
        raise HTTPException(status_code=401, detail="Invalid ingest key")


# ---------- Endpoints ----------
@app.get("/health")
def health():
    return {"ok": True}


@app.post("/api/sessions", status_code=201)
def create_session(payload: SessionIn, x_ingest_key: Optional[str] = Header(None)):
    auth_ingest(x_ingest_key)

    # Validaciones extra
    if payload.endAt <= payload.startAt:
        raise HTTPException(status_code=400, detail="endAt must be > startAt")

    # (Opcional) recalcular duration desde timestamps si quieres:
    # duration_calc = int((payload.endAt - payload.startAt).total_seconds())
    # if abs(duration_calc - payload.durationSec) > 5: ...

    sql = """
    insert into public.sessions
    (device_id, session_id, start_at, end_at, duration_sec, price)
    values (%s, %s, %s, %s, %s, %s)
    on conflict (device_id, session_id)
    do update set
      start_at = excluded.start_at,
      end_at = excluded.end_at,
      duration_sec = excluded.duration_sec,
      price = excluded.price
    returning created_at;
    """

    try:
        with psycopg.connect(DATABASE_URL) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    sql,
                    (
                        payload.deviceId,
                        payload.sessionId,
                        payload.startAt,
                        payload.endAt,
                        payload.durationSec,
                        payload.price,
                    ),
                )
                created_at = cur.fetchone()[0]
        return {"status": "saved", "createdAt": created_at}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"DB error: {str(e)}")


@app.get("/api/sessions", response_model=List[SessionOut])
def list_sessions(
    deviceId: Optional[str] = Query(None),
    dateFrom: Optional[datetime] = Query(None),
    dateTo: Optional[datetime] = Query(None),
    limit: int = Query(200, ge=1, le=5000),
):
    where = []
    params = []

    if deviceId:
        where.append("device_id = %s")
        params.append(deviceId)
    if dateFrom:
        where.append("start_at >= %s")
        params.append(dateFrom)
    if dateTo:
        where.append("start_at < %s")
        params.append(dateTo)

    where_sql = ("where " + " and ".join(where)) if where else ""

    sql = f"""
    select device_id, session_id, start_at, end_at, duration_sec, price, created_at, is_anomaly, anomaly_score
    from public.sessions
    {where_sql}
    order by start_at desc
    limit %s;
    """

    params.append(limit)

    with psycopg.connect(DATABASE_URL) as conn:
        with conn.cursor() as cur:
            cur.execute(sql, params)
            rows = cur.fetchall()

    out = []
    for r in rows:
        out.append(
            SessionOut(
                deviceId=r[0],
                sessionId=r[1],
                startAt=r[2],
                endAt=r[3],
                durationSec=r[4],
                price=float(r[5]),
                createdAt=r[6],
                isAnomaly=bool(r[7]),
                anomalyScore=r[8],
            )
        )
    return out


@app.get("/api/metrics/daily")
def metrics_daily(
    deviceId: Optional[str] = Query(None),
    dateFrom: Optional[datetime] = Query(None),
    dateTo: Optional[datetime] = Query(None),
):
    where = []
    params = []

    if deviceId:
        where.append("device_id = %s")
        params.append(deviceId)
    if dateFrom:
        where.append("start_at >= %s")
        params.append(dateFrom)
    if dateTo:
        where.append("start_at < %s")
        params.append(dateTo)

    where_sql = ("where " + " and ".join(where)) if where else ""

    sql = f"""
    select
      date_trunc('day', start_at) as day,
      count(*) as sessions_count,
      sum(price) as revenue_sum,
      avg(duration_sec) as avg_duration_sec
    from public.sessions
    {where_sql}
    group by 1
    order by 1;
    """

    with psycopg.connect(DATABASE_URL) as conn:
        with conn.cursor() as cur:
            cur.execute(sql, params)
            rows = cur.fetchall()

    return [
        {
            "day": r[0],
            "sessionsCount": r[1],
            "revenueSum": float(r[2] or 0),
            "avgDurationSec": float(r[3] or 0),
        }
        for r in rows
    ]
@app.get("/api/metrics/hourly")
def metrics_hourly(
    deviceId: Optional[str] = Query(None),
    dateFrom: Optional[datetime] = Query(None),
    dateTo: Optional[datetime] = Query(None),
):
    where = []
    params = []

    if deviceId:
        where.append("device_id = %s")
        params.append(deviceId)
    if dateFrom:
        where.append("start_at >= %s")
        params.append(dateFrom)
    if dateTo:
        where.append("start_at < %s")
        params.append(dateTo)

    where_sql = ("where " + " and ".join(where)) if where else ""

    sql = f"""
    select
      extract(hour from start_at)::int as hour_of_day,
      count(*) as sessions_count
    from public.sessions
    {where_sql}
    group by 1
    order by 1;
    """

    with psycopg.connect(DATABASE_URL) as conn:
        with conn.cursor() as cur:
            cur.execute(sql, params)
            rows = cur.fetchall()

    return [{"hour": r[0], "sessionsCount": r[1]} for r in rows]