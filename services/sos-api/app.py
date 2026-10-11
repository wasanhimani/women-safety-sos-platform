import os
import re
import threading
import time
from contextlib import asynccontextmanager
from datetime import datetime, timezone

import jwt
import redis
from fastapi import Depends, FastAPI, HTTPException, Request, Response
from fastapi.responses import JSONResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from prometheus_client import CONTENT_TYPE_LATEST, Counter, generate_latest
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError, SQLAlchemyError

import security
from common.db import Alert, Contact, Database, Delivery, User, utcnow

REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379/0")
STREAM = os.getenv("ALERT_STREAM", "sos_alerts")
DATABASE_URL = os.getenv("DATABASE_URL")

security.jwt_secret()  # fail fast at startup if the signing secret is missing

r = redis.Redis.from_url(REDIS_URL, decode_responses=True)
db = Database(DATABASE_URL) if DATABASE_URL else None

ALERTS_RECEIVED = Counter("sos_alerts_received_total", "SOS alerts accepted by the API")
ALERTS_FAILED = Counter("sos_alerts_enqueue_failed_total", "SOS alerts that failed to enqueue")


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
            "message": a.message,
            "latitude": a.latitude,
            "longitude": a.longitude,
            "sent_at": iso(a.created_at),
            "delivered_to": counts.get(a.id, (0, 0))[0],
            "seen_by": counts.get(a.id, (0, 0))[1],
        }
        for a in alerts
    ]
