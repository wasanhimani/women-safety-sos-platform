import logging
import os
import signal
import socket
import threading
import time
from datetime import timedelta

import redis
import requests
from prometheus_client import Counter, Histogram, start_http_server
from sqlalchemy import select, update

from common.alerts import BadMessage, store_alert
from common.db import Database, Walk, utcnow

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


WALK_GRACE_SECONDS = int(os.getenv("WALK_GRACE_SECONDS", "60"))
WALKS_MISSED = Counter("sos_walks_missed_total", "Walk-home timers that ran out without a check-in")
STOP = False


def request_stop(signum, frame) -> None:
    """Kubernetes asks nicely with SIGTERM. A Python process running as the container's main process
    ignores it unless it has a handler, so without this the pod would keep working until it is force-killed."""
    global STOP
    STOP = True
    log.info("signal %s received: finishing the current alerts, then shutting down", signum)


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


def check_walks(db: Database, now=None) -> int:
    """Alert the contacts of everyone whose walk timer ran out (and whose grace period has passed).
    Several notifier pods run this at once. The alert is saved under a fixed id, so doing it twice never
    duplicates it, and the status change only succeeds for one of them."""
    now = now or utcnow()
    with db.Session() as session:
        overdue = session.scalars(
            select(Walk).where(Walk.status == "active", Walk.due_at <= now - timedelta(seconds=WALK_GRACE_SECONDS))
        ).all()
    handled = 0
    for walk in overdue:
        note = f" ({walk.note})" if walk.note else ""
        fields = {
            "sender_id": str(walk.user_id),
            "latitude": str(walk.last_latitude),
            "longitude": str(walk.last_longitude),
            "message": f"Missed their walk check-in{note}. Please call or check on them.",
            "created_at": str(time.time()),
            "kind": "walk_missed",
        }
        try:
            store_alert(db, f"walk-{walk.id}", fields)
        except BadMessage:
            log.exception("walk %s cannot be alerted, closing it", walk.id)
            with db.Session() as session, session.begin():
                session.execute(update(Walk).where(Walk.id == walk.id, Walk.status == "active").values(status="cancelled", ended_at=now))
            continue
        with db.Session() as session, session.begin():
            changed = session.execute(
                update(Walk).where(Walk.id == walk.id, Walk.status == "active").values(status="alerted", alerted_at=now)
            ).rowcount
        if changed:
            WALKS_MISSED.inc()
            log.warning("walk %s: no check-in, contacts alerted", walk.id)
            handled += 1
    return handled


def watch_walks(db: Database) -> None:
    while not STOP:
        try:
            check_walks(db)
        except Exception:
            log.exception("walk check failed, will try again")
        time.sleep(5)


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
    threading.Thread(target=watch_walks, args=(db,), daemon=True).start()
    signal.signal(signal.SIGTERM, request_stop)
    signal.signal(signal.SIGINT, request_stop)
    log.info("notifier %s started", CONSUMER)

    while not STOP:
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
    log.info("notifier %s stopped cleanly", CONSUMER)


if __name__ == "__main__":
    main()
