import logging
import os
import socket
import time
from datetime import datetime, timezone

import redis
import requests
from prometheus_client import Counter, Histogram, start_http_server
from sqlalchemy import select

from common.db import Alert, Contact, Database, Delivery, User

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("notifier")

REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379/0")
DATABASE_URL = os.getenv("DATABASE_URL", "")
STREAM = os.getenv("ALERT_STREAM", "sos_alerts")
DEAD_STREAM = os.getenv("DEAD_STREAM", "sos_alerts_dead")
GROUP = os.getenv("CONSUMER_GROUP", "notifiers")
CONSUMER = os.getenv("HOSTNAME", socket.gethostname())
# Optional extra channel: also ping one fixed Telegram chat. Off unless both are set.
TELEGRAM_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")

DELIVERED = Counter("sos_alerts_delivered_total", "Alerts processed and delivered to contacts")
DELIVERY_FAILED = Counter("sos_alerts_delivery_failed_total", "Alert deliveries that failed and will be retried")
DEAD_LETTERED = Counter("sos_alerts_dead_lettered_total", "Malformed alerts moved to the dead-letter stream")
DELIVERY_LATENCY = Histogram(
    "sos_alert_delivery_seconds",
    "Seconds from API acceptance to delivery",
    buckets=(0.5, 1, 2, 3, 5, 10, 30, 60),
)


class BadMessage(Exception):
    """The message can never be processed (retrying would not help)."""


def store_alert(db: Database, stream_id: str, fields: dict) -> dict:
    """Save the alert and one delivery row per accepted contact. Safe to run twice for the
    same message: the unique stream id and (alert, recipient) pair stop duplicates."""
    try:
        sender_id = int(fields["sender_id"])
        latitude = float(fields["latitude"])
        longitude = float(fields["longitude"])
        created_at = float(fields["created_at"])
        message = str(fields.get("message", ""))[:280]
    except (KeyError, ValueError) as exc:
        raise BadMessage(f"malformed alert: {exc!r}")

    with db.Session() as session, session.begin():
        sender = session.get(User, sender_id)
        if sender is None:
            raise BadMessage(f"unknown sender {sender_id}")
        alert = session.scalar(select(Alert).where(Alert.stream_id == stream_id))
        if alert is None:
            alert = Alert(
                stream_id=stream_id,
                sender_id=sender_id,
                latitude=latitude,
                longitude=longitude,
                message=message,
                created_at=datetime.fromtimestamp(created_at, timezone.utc),
            )
            session.add(alert)
            session.flush()
        recipients = session.scalars(
            select(Contact.addressee_id).where(Contact.requester_id == sender_id, Contact.status == "accepted")
        ).all()
        already = set(session.scalars(select(Delivery.recipient_id).where(Delivery.alert_id == alert.id)).all())
        for recipient_id in recipients:
            if recipient_id not in already:
                session.add(Delivery(alert_id=alert.id, recipient_id=recipient_id))
        return {
            "sender": sender.name,
            "message": message,
            "latitude": latitude,
            "longitude": longitude,
            "recipients": len(recipients),
            "created_at": created_at,
        }


def telegram_ping(info: dict) -> None:
    if not (TELEGRAM_TOKEN and TELEGRAM_CHAT_ID):
        return
    text = f"SOS from {info['sender']}: {info['message']}\nhttps://maps.google.com/?q={info['latitude']},{info['longitude']}"
    try:
        requests.post(
            f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage",
            json={"chat_id": TELEGRAM_CHAT_ID, "text": text},
            timeout=5,
        ).raise_for_status()
    except Exception:
        log.exception("telegram ping failed (alert is still saved)")


def handle(r: redis.Redis, db: Database, msg_id: str, fields: dict) -> None:
    try:
        info = store_alert(db, msg_id, fields)
    except BadMessage as exc:
        log.error("dead-lettering %s: %s", msg_id, exc)
        r.xadd(DEAD_STREAM, {**fields, "error": str(exc), "original_id": msg_id}, maxlen=1000)
        r.xack(STREAM, GROUP, msg_id)
        DEAD_LETTERED.inc()
        return
    except Exception:
        DELIVERY_FAILED.inc()
        log.exception("could not store %s, it stays pending and will be retried", msg_id)
        return  # not acknowledged: another attempt will pick it up

    if info["recipients"] == 0:
        log.warning("alert %s from %s: no accepted contacts to notify", msg_id, info["sender"])
    else:
        log.info("alert %s from %s delivered to %d contact(s)", msg_id, info["sender"], info["recipients"])
    telegram_ping(info)
    r.xack(STREAM, GROUP, msg_id)
    DELIVERED.inc()
    DELIVERY_LATENCY.observe(max(0.0, time.time() - info["created_at"]))


def ensure_group(r: redis.Redis) -> None:
    try:
        r.xgroup_create(STREAM, GROUP, id="0", mkstream=True)
    except redis.ResponseError as exc:
        if "BUSYGROUP" not in str(exc):
            raise


def main() -> None:
    if not DATABASE_URL:
        raise SystemExit("DATABASE_URL is required")
    start_http_server(8001)  # Prometheus metrics
    db = Database(DATABASE_URL)
    db.wait_until_ready()
    r = redis.Redis.from_url(REDIS_URL, decode_responses=True)
    ensure_group(r)
    log.info("notifier %s started", CONSUMER)

    while True:
        try:
            batches = r.xreadgroup(GROUP, CONSUMER, {STREAM: ">"}, count=10, block=5000)
            if not batches:
                # take over alerts a crashed notifier left unfinished for more than 10 seconds
                _, claimed, _ = r.xautoclaim(STREAM, GROUP, CONSUMER, min_idle_time=10000, count=10)
                batches = [(STREAM, claimed)] if claimed else []
            for _, messages in batches:
                for msg_id, fields in messages:
                    handle(r, db, msg_id, fields)
        except redis.RedisError:
            log.exception("redis error, retrying in 2s")
            time.sleep(2)


if __name__ == "__main__":
    main()
