from datetime import timedelta

from sqlalchemy import select, update

import notifier
from common.db import Walk, utcnow
from helpers import befriend, signup

START = {"minutes": 30, "note": "campus to home", "latitude": 28.67, "longitude": 77.45}


def setup(client):
    alice, _ = signup(client, "Alice", "alice@example.com")
    bob, _ = signup(client, "Bob", "bob@example.com")
    befriend(client, alice, bob, "bob@example.com")
    return alice, bob


def make_due(db, minutes_ago):
    """Pretend the timer ran out some minutes ago."""
    with db.Session() as s, s.begin():
        s.execute(update(Walk).values(due_at=utcnow() - timedelta(minutes=minutes_ago)))


def test_walk_needs_login_and_an_accepted_contact(client):
    assert client.post("/walks", json=START).status_code == 401
    alice, _ = signup(client, "Alice", "alice@example.com")
    assert client.post("/walks", json=START, headers=alice).status_code == 400  # nobody to alert


def test_start_and_read_a_walk(client):
    alice, _ = setup(client)
    walk = client.post("/walks", json=START, headers=alice)
    assert walk.status_code == 201
    body = walk.json()
    assert body["status"] == "active" and body["note"] == "campus to home"
    assert 29 * 60 <= body["seconds_left"] <= 30 * 60
    assert body["seconds_to_alert"] == body["seconds_left"] + notifier.WALK_GRACE_SECONDS
    assert client.get("/walks/active", headers=alice).json()["id"] == body["id"]


def test_only_one_walk_at_a_time_and_input_is_checked(client):
    alice, _ = setup(client)
    assert client.post("/walks", json=START, headers=alice).status_code == 201
    assert client.post("/walks", json=START, headers=alice).status_code == 409
    assert client.post("/walks", json={**START, "minutes": 0}, headers=alice).status_code == 422
    assert client.post("/walks", json={**START, "minutes": 999}, headers=alice).status_code == 422


def test_no_alert_before_the_deadline_or_during_the_grace_period(client, db):
    alice, bob = setup(client)
    client.post("/walks", json=START, headers=alice)
    assert notifier.check_walks(db) == 0  # not due yet
    with db.Session() as s, s.begin():  # due 20 seconds ago: inside the 60 second grace period
        s.execute(update(Walk).values(due_at=utcnow() - timedelta(seconds=20)))
    assert notifier.check_walks(db) == 0
    assert client.get("/inbox", headers=bob).json() == []


def test_missed_check_in_alerts_contacts_once_with_last_known_location(client, db):
    alice, bob = setup(client)
    walk_id = client.post("/walks", json=START, headers=alice).json()["id"]
    client.post(f"/walks/{walk_id}/ping", json={"latitude": 28.7, "longitude": 77.5}, headers=alice)  # she moved
    make_due(db, minutes_ago=5)

    assert notifier.check_walks(db) == 1
    assert notifier.check_walks(db) == 0  # running again must not alert twice

    inbox = client.get("/inbox", headers=bob).json()
    assert len(inbox) == 1
    assert inbox[0]["kind"] == "walk_missed" and inbox[0]["from"] == "Alice"
    assert "campus to home" in inbox[0]["message"]
    assert "28.7,77.5" in inbox[0]["maps_url"]  # the LAST known location, not the starting one
    assert client.get("/walks/active", headers=alice).json()["status"] == "alerted"


def test_checking_in_safe_before_the_deadline_sends_nothing(client, db):
    alice, bob = setup(client)
    walk_id = client.post("/walks", json=START, headers=alice).json()["id"]
    assert client.post(f"/walks/{walk_id}/safe", headers=alice).json() == {"status": "safe", "contacts_told": False}
    make_due(db, minutes_ago=5)
    assert notifier.check_walks(db) == 0
    assert client.get("/inbox", headers=bob).json() == []
    assert client.get("/walks/active", headers=alice).json() is None


def test_safe_after_the_alert_tells_the_contacts(client, db):
    alice, bob = setup(client)
    walk_id = client.post("/walks", json=START, headers=alice).json()["id"]
    make_due(db, minutes_ago=5)
    notifier.check_walks(db)
    done = client.post(f"/walks/{walk_id}/safe", headers=alice).json()
    assert done["contacts_told"] is True
    kinds = [a["kind"] for a in client.get("/inbox", headers=bob).json()]
    assert sorted(kinds) == ["safe", "walk_missed"]


def test_extend_and_cancel(client, db):
    alice, bob = setup(client)
    walk_id = client.post("/walks", json={**START, "minutes": 10}, headers=alice).json()["id"]
    longer = client.post(f"/walks/{walk_id}/extend", json={"minutes": 10}, headers=alice).json()
    assert 19 * 60 <= longer["seconds_left"] <= 20 * 60
    assert client.delete(f"/walks/{walk_id}", headers=alice).status_code == 204
    make_due(db, minutes_ago=5)
    assert notifier.check_walks(db) == 0  # cancelled walks never alert
    assert client.get("/walks/active", headers=alice).json() is None


def test_nobody_else_can_touch_my_walk(client):
    alice, bob = setup(client)
    walk_id = client.post("/walks", json=START, headers=alice).json()["id"]
    assert client.post(f"/walks/{walk_id}/safe", headers=bob).status_code == 404
    assert client.delete(f"/walks/{walk_id}", headers=bob).status_code == 404
    assert client.post(f"/walks/{walk_id}/ping", json={"latitude": 1, "longitude": 2}, headers=bob).status_code == 404


def test_walk_deadline_survives_without_the_phone(client, db):
    """The whole point: nothing from the phone is needed for the alert to go out."""
    alice, bob = setup(client)
    client.post("/walks", json=START, headers=alice)
    make_due(db, minutes_ago=5)  # the phone never calls the server again
    assert notifier.check_walks(db) == 1
    assert len(client.get("/inbox", headers=bob).json()) == 1
