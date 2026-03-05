import os
from typing import Optional
from fastapi import HTTPException
from datetime import datetime
from typing import Optional, List
import pandas as pd
from sklearn.ensemble import IsolationForest
from fastapi import FastAPI, Header, HTTPException, Query
from pydantic import BaseModel, Field
import psycopg
from fastapi.middleware.cors import CORSMiddleware
from dotenv import load_dotenv
load_dotenv()
DATABASE_URL = os.getenv("DATABASE_URL")
INGEST_KEY = os.getenv("INGEST_KEY")

if not DATABASE_URL:
    raise RuntimeError("Falta DATABASE_URL en .env")
if not INGEST_KEY:
    raise RuntimeError("Falta INGEST_KEY en .env")

app = FastAPI(title="Carwash Backend", version="1.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # luego lo restringes a tu dominio
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)
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
@app.post("/api/ml/run")
def ml_run(
    deviceId: Optional[str] = Query(None),
    limit: int = Query(5000, ge=50, le=50000),
    contamination: float = Query(0.05, ge=0.001, le=0.2),
):
    """
    Entrena IsolationForest con features simples:
    - duration_sec
    - price
    - hour_of_day
    - day_of_week
    Luego marca is_anomaly y anomaly_score en la tabla.
    """
    where = []
    params = []
    if deviceId:
        where.append("device_id = %s")
        params.append(deviceId)

    where_sql = ("where " + " and ".join(where)) if where else ""

    sql = f"""
    select id, device_id, session_id, start_at, duration_sec, price
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

    if len(rows) < 50:
        raise HTTPException(status_code=400, detail=f"Insuficientes datos para ML: {len(rows)}")

    df = pd.DataFrame(rows, columns=["id", "device_id", "session_id", "start_at", "duration_sec", "price"])
    df["start_at"] = pd.to_datetime(df["start_at"], utc=True)
    df["hour_of_day"] = df["start_at"].dt.hour.astype(int)
    df["day_of_week"] = df["start_at"].dt.dayofweek.astype(int)

    # Features
    X = df[["duration_sec", "price", "hour_of_day", "day_of_week"]].astype(float)

    model = IsolationForest(
        n_estimators=200,
        random_state=42,
        contamination=contamination
    )
    model.fit(X)

    # IsolationForest: score_samples => mayor = más normal. Menor = más anómalo.
    normality = model.score_samples(X)           # típico rango negativo
    anomaly_score = (-normality)                 # mayor = más anómalo (más intuitivo)

    preds = model.predict(X)  # -1 anomalía, 1 normal
    is_anomaly = (preds == -1)

    df["is_anomaly"] = is_anomaly
    df["anomaly_score"] = anomaly_score

    # Guardar en BD (update por id)
    updates = [(bool(r.is_anomaly), float(r.anomaly_score), str(r.id)) for r in df.itertuples(index=False)]

    with psycopg.connect(DATABASE_URL) as conn:
        with conn.cursor() as cur:
            cur.executemany(
                "update public.sessions set is_anomaly = %s, anomaly_score = %s where id = %s;",
                updates
            )
        conn.commit()

    total = len(df)
    anom = int(df["is_anomaly"].sum())

    # Top 10 anomalías (para ver rápido)
    top = df.sort_values("anomaly_score", ascending=False).head(10)[
        ["device_id", "session_id", "start_at", "duration_sec", "price", "anomaly_score"]
    ].to_dict(orient="records")

    return {
        "status": "ok",
        "deviceId": deviceId,
        "rowsUsed": total,
        "contamination": contamination,
        "anomaliesFound": anom,
        "topAnomalies": top
    }

@app.get("/api/ml/anomalies")
def ml_anomalies(
    deviceId: Optional[str] = Query(None),
    limit: int = Query(50, ge=1, le=1000),
):
    where = ["is_anomaly = true"]
    params = []

    if deviceId:
        where.append("device_id = %s")
        params.append(deviceId)

    where_sql = "where " + " and ".join(where)

    sql = f"""
    select device_id, session_id, start_at, end_at, duration_sec, price, anomaly_score
    from public.sessions
    {where_sql}
    order by anomaly_score desc nulls last
    limit %s;
    """
    params.append(limit)

    with psycopg.connect(DATABASE_URL) as conn:
        with conn.cursor() as cur:
            cur.execute(sql, params)
            rows = cur.fetchall()

    return [
        {
            "deviceId": r[0],
            "sessionId": r[1],
            "startAt": r[2],
            "endAt": r[3],
            "durationSec": r[4],
            "price": float(r[5]),
            "anomalyScore": r[6],
        }
        for r in rows
    ]
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

@app.get("/api/sessions/next-id")
def next_session_id(deviceId: str = Query(...)):
    sql = """
    select coalesce(max(session_id), 0) + 1
    from public.sessions
    where device_id = %s;
    """
    with psycopg.connect(DATABASE_URL) as conn:
        with conn.cursor() as cur:
            cur.execute(sql, (deviceId,))
            next_id = cur.fetchone()[0]
    return {"nextSessionId": int(next_id)}

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