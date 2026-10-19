import notifier
from helpers import befriend, drain, signup


def gauge(client, name):
    for line in client.get("/metrics").text.splitlines():
        if line.startswith(name + " "):
            return float(line.split()[1])
    return None


def test_queue_backlog_is_visible_from_the_api_while_notifiers_are_down(client, fake_redis, db):
    alice, _ = signup(client, "Alice", "alice@example.com")
    bob, _ = signup(client, "Bob", "bob@example.com")
    befriend(client, alice, bob, "bob@example.com")
    for _ in range(3):
        client.post("/sos", json={"latitude": 1, "longitude": 2}, headers=alice)

    notifier.ensure_group(fake_redis)  # a notifier existed once, but nobody is processing now
    assert gauge(client, "sos_queue_lag") == 3.0  # the dashboard can show 3 alerts waiting

    drain(fake_redis, db)
    assert gauge(client, "sos_queue_lag") == 0.0
    assert gauge(client, "sos_queue_pending") == 0.0
