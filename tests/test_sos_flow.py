import notifier
from common.db import Database
import app as sos
from helpers import befriend, drain, signup

SOS = {"latitude": 28.67, "longitude": 77.45, "message": "Help me"}


def test_sos_needs_login(client):
    assert client.post("/sos", json=SOS).status_code == 401


def test_sos_validates_location(client):
    alice, _ = signup(client, "Alice", "alice@example.com")
    assert client.post("/sos", json={**SOS, "latitude": 999}, headers=alice).status_code == 422


def test_alert_reaches_accepted_contact_end_to_end(client, fake_redis, db):
    alice, _ = signup(client, "Alice", "alice@example.com")
    bob, _ = signup(client, "Bob", "bob@example.com")
    befriend(client, alice, bob, "bob@example.com")

    resp = client.post("/sos", json=SOS, headers=alice)
    assert resp.status_code == 202 and resp.json()["status"] == "queued"
    drain(fake_redis, db)

    inbox = client.get("/inbox", headers=bob).json()
    assert len(inbox) == 1
    assert inbox[0]["from"] == "Alice" and inbox[0]["message"] == "Help me" and not inbox[0]["seen"]
    assert "28.67,77.45" in inbox[0]["maps_url"]

    sent = client.get("/alerts/sent", headers=alice).json()
    assert sent[0]["delivered_to"] == 1 and sent[0]["seen_by"] == 0

    assert client.post(f"/inbox/{inbox[0]['delivery_id']}/seen", headers=bob).status_code == 200
    assert client.get("/alerts/sent", headers=alice).json()[0]["seen_by"] == 1


def test_pending_contact_gets_nothing_until_accepted(client, fake_redis, db):
    alice, _ = signup(client, "Alice", "alice@example.com")
    bob, _ = signup(client, "Bob", "bob@example.com")
    client.post("/contacts", json={"email": "bob@example.com"}, headers=alice)  # not accepted
    client.post("/sos", json=SOS, headers=alice)
    drain(fake_redis, db)
    assert client.get("/inbox", headers=bob).json() == []
    assert client.get("/alerts/sent", headers=alice).json()[0]["delivered_to"] == 0  # still saved in her history


def test_alerts_only_go_to_the_senders_contacts(client, fake_redis, db):
    alice, _ = signup(client, "Alice", "alice@example.com")
    bob, _ = signup(client, "Bob", "bob@example.com")
    carol, _ = signup(client, "Carol", "carol@example.com")
    befriend(client, alice, bob, "bob@example.com")
    client.post("/sos", json=SOS, headers=alice)
    drain(fake_redis, db)
    assert len(client.get("/inbox", headers=bob).json()) == 1
    assert client.get("/inbox", headers=carol).json() == []


def test_people_cannot_mark_someone_elses_alert_as_seen(client, fake_redis, db):
    alice, _ = signup(client, "Alice", "alice@example.com")
    bob, _ = signup(client, "Bob", "bob@example.com")
    carol, _ = signup(client, "Carol", "carol@example.com")
    befriend(client, alice, bob, "bob@example.com")
    client.post("/sos", json=SOS, headers=alice)
    drain(fake_redis, db)
    delivery_id = client.get("/inbox", headers=bob).json()[0]["delivery_id"]
    assert client.post(f"/inbox/{delivery_id}/seen", headers=carol).status_code == 404


def test_sos_still_works_when_the_database_is_down(client, monkeypatch, fake_redis):
    alice, _ = signup(client, "Alice", "alice@example.com")
    monkeypatch.setattr(sos, "db", Database("sqlite+pysqlite://"))  # empty database: every query fails
    assert client.get("/contacts", headers=alice).status_code == 503
    assert client.post("/sos", json=SOS, headers=alice).status_code == 202
    assert fake_redis.xlen("sos_alerts") == 1  # the alert is safely queued


def test_processing_the_same_alert_twice_does_not_duplicate(client, fake_redis, db):
    alice, _ = signup(client, "Alice", "alice@example.com")
    bob, _ = signup(client, "Bob", "bob@example.com")
    befriend(client, alice, bob, "bob@example.com")
    client.post("/sos", json=SOS, headers=alice)
    msg_id, fields = fake_redis.xrange("sos_alerts")[0]
    notifier.ensure_group(fake_redis)
    notifier.handle(fake_redis, db, msg_id, fields)
    notifier.handle(fake_redis, db, msg_id, fields)  # e.g. retried after a crash
    assert len(client.get("/inbox", headers=bob).json()) == 1


def test_failed_processing_stays_pending_and_is_retried(client, fake_redis, db, monkeypatch):
    alice, _ = signup(client, "Alice", "alice@example.com")
    bob, _ = signup(client, "Bob", "bob@example.com")
    befriend(client, alice, bob, "bob@example.com")
    client.post("/sos", json=SOS, headers=alice)

    real = notifier.store_alert
    monkeypatch.setattr(notifier, "store_alert", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("db down")))
    drain(fake_redis, db)
    assert fake_redis.xpending(notifier.STREAM, notifier.GROUP)["pending"] == 1  # not lost
    assert client.get("/inbox", headers=bob).json() == []

    monkeypatch.setattr(notifier, "store_alert", real)  # database is back
    msg_id, fields = fake_redis.xrange("sos_alerts")[0]
    notifier.handle(fake_redis, db, msg_id, fields)
    assert fake_redis.xpending(notifier.STREAM, notifier.GROUP)["pending"] == 0
    assert len(client.get("/inbox", headers=bob).json()) == 1


def test_malformed_alert_goes_to_dead_letter_not_an_endless_retry(fake_redis, db):
    fake_redis.xadd("sos_alerts", {"user_id": "old-format", "latitude": "1", "longitude": "2"})
    drain(fake_redis, db)
    assert fake_redis.xlen(notifier.DEAD_STREAM) == 1
    assert fake_redis.xpending(notifier.STREAM, notifier.GROUP)["pending"] == 0


def test_optional_telegram_ping(client, fake_redis, db, monkeypatch):
    calls = []

    class Ok:
        def raise_for_status(self):
            pass

    monkeypatch.setattr(notifier, "TELEGRAM_TOKEN", "t")
    monkeypatch.setattr(notifier, "TELEGRAM_CHAT_ID", "c")
    monkeypatch.setattr(notifier.requests, "post", lambda url, **kw: calls.append((url, kw)) or Ok())
    alice, _ = signup(client, "Alice", "alice@example.com")
    client.post("/sos", json=SOS, headers=alice)
    drain(fake_redis, db)
    assert len(calls) == 1 and "Alice" in calls[0][1]["json"]["text"]
