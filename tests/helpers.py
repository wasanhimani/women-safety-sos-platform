import notifier


def signup(client, name, email, password="correct-horse-1"):
    resp = client.post("/auth/signup", json={"name": name, "email": email, "password": password})
    assert resp.status_code == 201, resp.text
    body = resp.json()
    return {"Authorization": f"Bearer {body['access_token']}"}, body["user"]


def befriend(client, owner_headers, contact_headers, contact_email):
    """owner names contact as trusted contact, contact accepts."""
    added = client.post("/contacts", json={"email": contact_email}, headers=owner_headers)
    assert added.status_code == 201, added.text
    accepted = client.post(f"/contacts/{added.json()['id']}/accept", headers=contact_headers)
    assert accepted.status_code == 200, accepted.text


def drain(r, db):
    """Run the notifier over everything currently in the queue."""
    notifier.ensure_group(r)
    batches = r.xreadgroup(notifier.GROUP, "test-consumer", {notifier.STREAM: ">"}, count=100)
    for _, messages in batches or []:
        for msg_id, fields in messages:
            notifier.handle(r, db, msg_id, fields)
