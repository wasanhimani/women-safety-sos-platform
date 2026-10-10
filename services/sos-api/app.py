import os
import time

import redis
from fastapi import FastAPI, HTTPException
from prometheus_client import Counter, make_asgi_app
from pydantic import BaseModel, Field

REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379/0")
STREAM = os.getenv("ALERT_STREAM", "sos_alerts")

r = redis.Redis.from_url(REDIS_URL, decode_responses=True)

app = FastAPI(title="SOS API", version="0.1.0")
app.mount("/metrics", make_asgi_app())

ALERTS_RECEIVED = Counter("sos_alerts_received_total", "SOS alerts accepted by the API")
ALERTS_FAILED = Counter("sos_alerts_enqueue_failed_total", "SOS alerts that failed to enqueue")


class SOSRequest(BaseModel):
    user_id: str = Field(min_length=1, max_length=64)
    latitude: float = Field(ge=-90, le=90)
    longitude: float = Field(ge=-180, le=180)
    message: str = Field(default="I need help", max_length=280)


@app.get("/healthz")
def healthz():
    """Liveness: the process is up."""
    return {"status": "ok"}


@app.get("/readyz")
def readyz():
    """Readiness: dependencies are reachable."""
    try:
        r.ping()
    except redis.RedisError:
        raise HTTPException(status_code=503, detail="queue unavailable")
    return {"status": "ready"}


@app.post("/sos", status_code=202)
def trigger_sos(req: SOSRequest):
    payload = {
        "user_id": req.user_id,
        "latitude": str(req.latitude),
        "longitude": str(req.longitude),
        "message": req.message,
        "created_at": str(time.time()),
    }
    try:
        alert_id = r.xadd(STREAM, payload)
    except redis.RedisError:
        ALERTS_FAILED.inc()
        raise HTTPException(status_code=503, detail="could not queue alert, retry")
    ALERTS_RECEIVED.inc()
    return {"alert_id": alert_id, "status": "queued"}
