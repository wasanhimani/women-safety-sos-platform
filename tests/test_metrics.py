import time

import notifier
from helpers import befriend, drain, signup


def gauge(client, name):
    for line in client.get("/metrics").text.splitlines():
        if line.startswith(name + " "):
            return float(line.split()[1])
    return None


def test_queue_state_is_visible_from_the_api_while_notifiers_are_down(client, fake_redis, db):
    alice, _ = signup(client, "Alice", "alice@example.com")
    bob, _ = signup(client, "Bob", "bob@example.com")
    befriend(client, alice, bob, "bob@example.com")
    for _ in range(3):
        client.post("/sos", json={"latitude": 1, "longitude": 2}, headers=alice)
    time.sleep(0.05)

    notifier.ensure_group(fake_redis)  # a notifier existed once, but nobody is processing now
    assert gauge(client, "sos_queue_lag") == 3.0  # three alerts waiting
    assert gauge(client, "sos_queue_oldest_waiting_seconds") > 0  # and the clock is ticking on them
    assert gauge(client, "sos_alerts_processed_total") == 0.0

    drain(fake_redis, db)
    assert gauge(client, "sos_queue_lag") == 0.0
    assert gauge(client, "sos_queue_pending") == 0.0
    assert gauge(client, "sos_queue_oldest_waiting_seconds") == 0.0
    assert gauge(client, "sos_alerts_processed_total") == 3.0  # survives pod restarts: it is read from the queue


def test_metrics_work_before_any_alert_or_notifier_exists(client):
    assert gauge(client, "sos_queue_lag") == 0.0
    assert gauge(client, "sos_queue_oldest_waiting_seconds") == 0.0
