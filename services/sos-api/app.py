import os
import re
import threading
import time
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone

import jwt
import redis
from fastapi import Depends, FastAPI, HTTPException, Request, Response
from fastapi.responses import JSONResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from prometheus_client import CONTENT_TYPE_LATEST, REGISTRY, Counter, generate_latest
from prometheus_client.core import CounterMetricFamily, GaugeMetricFamily
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError, SQLAlchemyError

import security
from common.alerts import store_alert
from common.db import Alert, Contact, Database, Delivery, User, Walk, utcnow

REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379/0")
STREAM = os.getenv("ALERT_STREAM", "sos_alerts")
GROUP = os.getenv("CONSUMER_GROUP", "notifiers")
WALK_GRACE_SECONDS = int(os.getenv("WALK_GRACE_SECONDS", "60"))
DATABASE_URL = os.getenv("DATABASE_URL")

security.jwt_secret()  # fail fast at startup if the signing secret is missing

r = redis.Redis.from_url(REDIS_URL, decode_responses=True)
db = Database(DATABASE_URL) if DATABASE_URL else None

ALERTS_RECEIVED = Counter("sos_alerts_received_total", "SOS alerts accepted by the API")
ALERTS_FAILED = Counter("sos_alerts_enqueue_failed_total", "SOS alerts that failed to enqueue")


class QueueCollector:
    """Reads the alert queue's state from Redis every time Prometheus asks.

    The numbers come from the queue itself, not from the notifier pods, and are published by the API pods.
    So they survive a notifier crash: when every notifier dies, the dashboard still shows alerts waiting
    and for how long. Numbers kept inside a pod would vanish together with the pod."""

    def collect(self):
        lag = pending = processed = oldest = 0
        try:
            for group in r.xinfo_groups(STREAM):
                if group.get("name") != GROUP:
                    continue
                lag = group.get("lag") or 0
                pending = group.get("pending") or 0
                processed = max(0, (group.get("entries-read") or 0) - pending)
                waiting = []  # ids of alerts nobody has finished yet; an id starts with its creation time in ms
                if pending:
                    first_pending = r.xpending(STREAM, GROUP).get("min")
                    if first_pending:
                        waiting.append(first_pending)
                if lag:
                    unread = r.xrange(STREAM, min=f"({group['last-delivered-id']}", max="+", count=1)
                    if unread:
                        waiting.append(unread[0][0])
                if waiting:
                    oldest = max(0.0, (time.time() * 1000 - min(int(i.split("-")[0]) for i in waiting)) / 1000)
        except redis.RedisError:
            pass  # no queue or no notifier group yet
        yield GaugeMetricFamily("sos_queue_lag", "Alerts in the queue that no notifier has picked up yet", value=lag)
        yield GaugeMetricFamily("sos_queue_pending", "Alerts picked up by a notifier but not yet finished", value=pending)
        yield GaugeMetricFamily("sos_queue_oldest_waiting_seconds", "How long the oldest unfinished alert has been waiting", value=oldest)
        yield CounterMetricFamily("sos_alerts_processed", "Alerts a notifier has finished (saved and delivered)", value=processed)


REGISTRY.register(QueueCollector())


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Create the tables in the background, so the API (and the SOS button) starts
    # even if the database is still booting.
    if db is not None:
        threading.Thread(target=db.wait_until_ready, daemon=True).start()
    yield


app = FastAPI(title="SOS API", version="0.2.0", lifespan=lifespan)
bearer = HTTPBearer(auto_error=False)
EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


@app.exception_handler(SQLAlchemyError)
async def database_unavailable(request: Request, exc: SQLAlchemyError):
    return JSONResponse(status_code=503, content={"detail": "database unavailable, please retry"})


# ---------- helpers ----------

def get_session():
    if db is None:
        raise HTTPException(status_code=503, detail="database not configured")
    with db.Session() as session:
        yield session


def current_user(creds: HTTPAuthorizationCredentials | None = Depends(bearer)) -> security.Claims:
    if creds is None:
        raise HTTPException(status_code=401, detail="not authenticated", headers={"WWW-Authenticate": "Bearer"})
    try:
        return security.decode_token(creds.credentials)
    except (jwt.PyJWTError, ValueError):
        raise HTTPException(status_code=401, detail="invalid or expired token", headers={"WWW-Authenticate": "Bearer"})


def client_ip(request: Request) -> str:
    return request.client.host if request.client else "unknown"


def iso(dt: datetime | None) -> str | None:
    if dt is None:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.isoformat()


def person(u: User) -> dict:
    return {"name": u.name, "email": u.email}


def token_response(user: User) -> dict:
    return {
        "access_token": security.make_token(user.id, user.name),
        "token_type": "bearer",
        "user": {"id": user.id, "name": user.name, "email": user.email},
    }


# ---------- request bodies ----------

class SignupRequest(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    email: str = Field(max_length=254)
    password: str = Field(min_length=8, max_length=72)

    @field_validator("email")
    @classmethod
    def valid_email(cls, v: str) -> str:
        v = v.strip().lower()
        if not EMAIL_RE.match(v):
            raise ValueError("not a valid email address")
        return v

    @field_validator("password")
    @classmethod
    def password_fits_bcrypt(cls, v: str) -> str:
        if len(v.encode()) > 72:
            raise ValueError("password is too long (72 bytes max)")
        return v


class LoginRequest(BaseModel):
    email: str = Field(max_length=254)
    password: str = Field(min_length=1, max_length=72)

    @field_validator("email")
    @classmethod
    def normalise_email(cls, v: str) -> str:
        return v.strip().lower()


class ContactRequest(BaseModel):
    email: str = Field(max_length=254)

    @field_validator("email")
    @classmethod
    def normalise_email(cls, v: str) -> str:
        return v.strip().lower()


class SOSRequest(BaseModel):
    latitude: float = Field(ge=-90, le=90)
    longitude: float = Field(ge=-180, le=180)
    message: str = Field(default="I need help", max_length=280)


# ---------- health and metrics ----------

@app.get("/healthz")
def healthz():
    """Liveness: the process is up."""
    return {"status": "ok"}


@app.get("/readyz")
def readyz():
    """Readiness: the alert queue is reachable. The database is deliberately NOT checked,
    because the SOS button must keep working even if the database is down."""
    try:
        r.ping()
    except redis.RedisError:
        raise HTTPException(status_code=503, detail="queue unavailable")
    return {"status": "ready"}


@app.get("/metrics", include_in_schema=False)
def metrics():
    return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)


# ---------- accounts ----------

@app.post("/auth/signup", status_code=201)
def signup(req: SignupRequest, request: Request, session=Depends(get_session)):
    if not security.rate_limit(r, f"signup:{client_ip(request)}", 20, 3600):
        raise HTTPException(status_code=429, detail="too many sign-ups, try again later")
    user = User(name=req.name.strip(), email=req.email, password_hash=security.hash_password(req.password))
    session.add(user)
    try:
        session.commit()
    except IntegrityError:
        session.rollback()
        raise HTTPException(status_code=409, detail="email already registered")
    return token_response(user)


@app.post("/auth/login")
def login(req: LoginRequest, request: Request, session=Depends(get_session)):
    if not security.rate_limit(r, f"login:{client_ip(request)}:{req.email}", 10, 300):
        raise HTTPException(status_code=429, detail="too many attempts, try again in a few minutes")
    user = session.scalar(select(User).where(User.email == req.email))
    if not security.verify_password(req.password, user.password_hash if user else None):
        raise HTTPException(status_code=401, detail="invalid email or password")
    return token_response(user)


@app.get("/me")
def me(claims: security.Claims = Depends(current_user)):
    return {"id": claims.user_id, "name": claims.name}


# ---------- trusted contacts ----------

@app.post("/contacts", status_code=201)
def add_contact(req: ContactRequest, claims: security.Claims = Depends(current_user), session=Depends(get_session)):
    if session.get(User, claims.user_id) is None:
        raise HTTPException(status_code=401, detail="account no longer exists")
    target = session.scalar(select(User).where(User.email == req.email))
    if target is None:
        raise HTTPException(status_code=404, detail="no user with that email")
    if target.id == claims.user_id:
        raise HTTPException(status_code=400, detail="you cannot add yourself")
    contact = Contact(requester_id=claims.user_id, addressee_id=target.id)
    session.add(contact)
    try:
        session.commit()
    except IntegrityError:
        session.rollback()
        raise HTTPException(status_code=409, detail="already added")
    return {"id": contact.id, "status": contact.status, **person(target)}


@app.get("/contacts")
def list_contacts(claims: security.Claims = Depends(current_user), session=Depends(get_session)):
    me_id = claims.user_id
    mine = session.execute(
        select(Contact, User).join(User, User.id == Contact.addressee_id).where(Contact.requester_id == me_id)
    ).all()
    incoming = session.execute(
        select(Contact, User).join(User, User.id == Contact.requester_id).where(Contact.addressee_id == me_id)
    ).all()
    return {
        "my_contacts": [{"id": c.id, "status": c.status, **person(u)} for c, u in mine],
        "requests_to_me": [{"id": c.id, **person(u)} for c, u in incoming if c.status == "pending"],
        "protecting": [{"id": c.id, **person(u)} for c, u in incoming if c.status == "accepted"],
    }


@app.post("/contacts/{contact_id}/accept")
def accept_contact(contact_id: int, claims: security.Claims = Depends(current_user), session=Depends(get_session)):
    contact = session.get(Contact, contact_id)
    if contact is None or contact.addressee_id != claims.user_id or contact.status != "pending":
        raise HTTPException(status_code=404, detail="request not found")
    contact.status = "accepted"
    session.commit()
    return {"id": contact.id, "status": contact.status}


@app.delete("/contacts/{contact_id}", status_code=204)
def remove_contact(contact_id: int, claims: security.Claims = Depends(current_user), session=Depends(get_session)):
    contact = session.get(Contact, contact_id)
    if contact is None or claims.user_id not in (contact.requester_id, contact.addressee_id):
        raise HTTPException(status_code=404, detail="contact not found")
    session.delete(contact)
    session.commit()
    return Response(status_code=204)


# ---------- the SOS button ----------

@app.post("/sos", status_code=202)
def trigger_sos(req: SOSRequest, claims: security.Claims = Depends(current_user)):
    """Needs only a valid token (checked without the database) and the queue."""
    if not security.rate_limit(r, f"sos:{claims.user_id}", 30, 60):
        raise HTTPException(status_code=429, detail="too many alerts, please wait a moment")
    payload = {
        "sender_id": str(claims.user_id),
        "sender_name": claims.name,
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


# ---------- alerts: what contacts receive, and what I sent ----------

@app.get("/inbox")
def inbox(claims: security.Claims = Depends(current_user), session=Depends(get_session)):
    rows = session.execute(
        select(Delivery, Alert, User)
        .join(Alert, Alert.id == Delivery.alert_id)
        .join(User, User.id == Alert.sender_id)
        .where(Delivery.recipient_id == claims.user_id)
        .order_by(Alert.created_at.desc(), Delivery.id.desc())
        .limit(50)
    ).all()
    return [
        {
            "delivery_id": d.id,
            "kind": a.kind,
            "from": u.name,
            "message": a.message,
            "latitude": a.latitude,
            "longitude": a.longitude,
            "maps_url": f"https://maps.google.com/?q={a.latitude},{a.longitude}",
            "sent_at": iso(a.created_at),
            "seen": d.seen_at is not None,
        }
        for d, a, u in rows
    ]


@app.post("/inbox/{delivery_id}/seen")
def mark_seen(delivery_id: int, claims: security.Claims = Depends(current_user), session=Depends(get_session)):
    delivery = session.get(Delivery, delivery_id)
    if delivery is None or delivery.recipient_id != claims.user_id:
        raise HTTPException(status_code=404, detail="alert not found")
    if delivery.seen_at is None:
        delivery.seen_at = utcnow()
        session.commit()
    return {"delivery_id": delivery.id, "seen": True}


@app.get("/alerts/sent")
def sent_alerts(claims: security.Claims = Depends(current_user), session=Depends(get_session)):
    alerts = session.scalars(
        select(Alert).where(Alert.sender_id == claims.user_id).order_by(Alert.created_at.desc()).limit(50)
    ).all()
    counts = {}
    if alerts:
        rows = session.execute(
            select(Delivery.alert_id, func.count(Delivery.id), func.count(Delivery.seen_at))
            .where(Delivery.alert_id.in_([a.id for a in alerts]))
            .group_by(Delivery.alert_id)
        ).all()
        counts = {alert_id: (total, seen) for alert_id, total, seen in rows}
    return [
        {
            "id": a.id,
            "kind": a.kind,
            "message": a.message,
            "latitude": a.latitude,
            "longitude": a.longitude,
            "sent_at": iso(a.created_at),
            "delivered_to": counts.get(a.id, (0, 0))[0],
            "seen_by": counts.get(a.id, (0, 0))[1],
        }
        for a in alerts
    ]


# ---------- walk-home timer ----------
# The deadline is watched by the server (see the notifier), so the alert still goes out
# if the phone dies, loses signal or is taken.

OPEN_WALK = ("active", "alerted")


class WalkStart(BaseModel):
    minutes: int = Field(ge=1, le=240)
    note: str = Field(default="", max_length=100)
    latitude: float = Field(ge=-90, le=90)
    longitude: float = Field(ge=-180, le=180)


class WalkPing(BaseModel):
    latitude: float = Field(ge=-90, le=90)
    longitude: float = Field(ge=-180, le=180)


class WalkExtend(BaseModel):
    minutes: int = Field(ge=1, le=60)


def aware(dt: datetime) -> datetime:
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def walk_json(w: Walk) -> dict:
    now = utcnow()
    due = aware(w.due_at)
    return {
        "id": w.id,
        "status": w.status,
        "note": w.note,
        "started_at": iso(w.started_at),
        "due_at": iso(due),
        "alert_at": iso(due + timedelta(seconds=WALK_GRACE_SECONDS)),
        "seconds_left": int((due - now).total_seconds()),
        "seconds_to_alert": int((due + timedelta(seconds=WALK_GRACE_SECONDS) - now).total_seconds()),
        "alerted_at": iso(w.alerted_at),
        "last_latitude": w.last_latitude,
        "last_longitude": w.last_longitude,
    }


def own_open_walk(session, walk_id: int, user_id: int, statuses=OPEN_WALK) -> Walk:
    walk = session.get(Walk, walk_id)
    if walk is None or walk.user_id != user_id or walk.status not in statuses:
        raise HTTPException(status_code=404, detail="walk not found")
    return walk


@app.post("/walks", status_code=201)
def start_walk(req: WalkStart, claims: security.Claims = Depends(current_user), session=Depends(get_session)):
    accepted = session.scalar(
        select(func.count(Contact.id)).where(Contact.requester_id == claims.user_id, Contact.status == "accepted")
    )
    if not accepted:
        raise HTTPException(status_code=400, detail="Add a trusted contact who has accepted before starting a walk.")
    now = utcnow()
    walk = Walk(
        user_id=claims.user_id,
        note=req.note.strip(),
        started_at=now,
        due_at=now + timedelta(minutes=req.minutes),
        last_latitude=req.latitude,
        last_longitude=req.longitude,
        last_seen_at=now,
    )
    session.add(walk)
    try:
        session.commit()
    except IntegrityError:
        session.rollback()
        raise HTTPException(status_code=409, detail="You already have a walk in progress.")
    return walk_json(walk)


@app.get("/walks/active")
def active_walk(claims: security.Claims = Depends(current_user), session=Depends(get_session)):
    walk = session.scalar(
        select(Walk).where(Walk.user_id == claims.user_id, Walk.status.in_(OPEN_WALK)).order_by(Walk.id.desc())
    )
    return walk_json(walk) if walk else None


@app.post("/walks/{walk_id}/ping")
def ping_walk(walk_id: int, req: WalkPing, claims: security.Claims = Depends(current_user), session=Depends(get_session)):
    walk = own_open_walk(session, walk_id, claims.user_id)
    walk.last_latitude, walk.last_longitude, walk.last_seen_at = req.latitude, req.longitude, utcnow()
    session.commit()
    return {"ok": True}


@app.post("/walks/{walk_id}/extend")
def extend_walk(walk_id: int, req: WalkExtend, claims: security.Claims = Depends(current_user), session=Depends(get_session)):
    walk = own_open_walk(session, walk_id, claims.user_id, statuses=("active",))
    new_due = aware(walk.due_at) + timedelta(minutes=req.minutes)
    if new_due > aware(walk.started_at) + timedelta(hours=4):
        raise HTTPException(status_code=400, detail="A walk can last at most 4 hours.")
    walk.due_at = new_due
    session.commit()
    return walk_json(walk)


@app.post("/walks/{walk_id}/safe")
def walk_safe(walk_id: int, claims: security.Claims = Depends(current_user), session=Depends(get_session)):
    """The person is safe. If the alert already went out, the contacts are told it is over."""
    walk = own_open_walk(session, walk_id, claims.user_id)
    tell_contacts = walk.status == "alerted"
    walk.status, walk.ended_at = "safe", utcnow()
    session.commit()
    if tell_contacts:
        store_alert(db, f"safe-{walk.id}", {
            "sender_id": str(claims.user_id),
            "latitude": str(walk.last_latitude),
            "longitude": str(walk.last_longitude),
            "message": "I'm safe now. Sorry for the scare.",
            "created_at": str(time.time()),
            "kind": "safe",
        })
    return {"status": "safe", "contacts_told": tell_contacts}


@app.delete("/walks/{walk_id}", status_code=204)
def cancel_walk(walk_id: int, claims: security.Claims = Depends(current_user), session=Depends(get_session)):
    walk = own_open_walk(session, walk_id, claims.user_id, statuses=("active",))
    walk.status, walk.ended_at = "cancelled", utcnow()
    session.commit()
    return Response(status_code=204)
