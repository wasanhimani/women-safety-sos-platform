import logging
import os
import socket
import time

import redis
import requests
from prometheus_client import Counter, Histogram, start_http_server

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("notifier")

REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379/0")
STREAM = os.getenv("ALERT_STREAM", "sos_alerts")
GROUP = os.getenv("CONSUMER_GROUP", "notifiers")
CONSUMER = os.getenv("HOSTNAME", socket.gethostname())
TELEGRAM_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")

DELIVERED = Counter("sos_alerts_delivered_total", "Alerts delivered to contacts")
DELIVERY_FAILED = Counter("sos_alerts_delivery_failed_total", "Alert deliveries that failed")
DELIVERY_LATENCY = Histogram(
    "sos_alert_delivery_seconds",
    "Seconds from API acceptance to delivery",
    buckets=(0.5, 1, 2, 3, 5, 10, 30, 60),
)


def send_alert(fields: dict) -> None:
    maps = f"https://maps.google.com/?q={fields['latitude']},{fields['longitude']}"
    text = f"SOS from {fields['user_id']}: {fields['message']}\nLocation: {maps}"
    if not (TELEGRAM_TOKEN and TELEGRAM_CHAT_ID):
        log.info("[dry-run] would send: %s", text)  # no credentials: log instead
        return
    resp = requests.post(
        f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage",
        json={"chat_id": TELEGRAM_CHAT_ID, "text": text},
        timeout=5,
    )
    resp.raise_for_status()


def ensure_group(r: redis.Redis) -> None:
    try:
        r.xgroup_create(STREAM, GROUP, id="0", mkstream=True)
    except redis.ResponseError as e:
        if "BUSYGROUP" not in str(e):
            raise


def main() -> None:
    start_http_server(8001)  # Prometheus metrics
    r = redis.Redis.from_url(REDIS_URL, decode_responses=True)
    ensure_group(r)
    log.info("notifier %s started", CONSUMER)

    while True:
        try:
            # ">" = new messages; pending (unacked) ones are retried below
            batches = r.xreadgroup(GROUP, CONSUMER, {STREAM: ">"}, count=10, block=5000)
            if not batches:
                # reclaim alerts another (crashed) consumer left unacked for >10s
                _, claimed, _ = r.xautoclaim(STREAM, GROUP, CONSUMER, min_idle_time=10000, count=10)
                batches = [(STREAM, claimed)] if claimed else []
            for _, messages in batches:
                for msg_id, fields in messages:
                    try:
                        send_alert(fields)
                    except Exception:
                        DELIVERY_FAILED.inc()
                        log.exception("delivery failed for %s, will retry", msg_id)
                        continue  # not acked -> stays pending -> retried
                    r.xack(STREAM, GROUP, msg_id)
                    DELIVERED.inc()
                    DELIVERY_LATENCY.observe(time.time() - float(fields["created_at"]))
        except redis.RedisError:
            log.exception("redis error, retrying in 2s")
            time.sleep(2)


if __name__ == "__main__":
    main()
