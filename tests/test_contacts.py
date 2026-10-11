from helpers import signup


def test_contact_request_and_accept(client):
    alice, _ = signup(client, "Alice", "alice@example.com")
    bob, _ = signup(client, "Bob", "bob@example.com")

    added = client.post("/contacts", json={"email": "bob@example.com"}, headers=alice)
    assert added.status_code == 201 and added.json()["status"] == "pending"
    contact_id = added.json()["id"]

    assert client.get("/contacts", headers=alice).json()["my_contacts"][0]["status"] == "pending"
    requests_to_bob = client.get("/contacts", headers=bob).json()["requests_to_me"]
    assert [c["name"] for c in requests_to_bob] == ["Alice"]

    assert client.post(f"/contacts/{contact_id}/accept", headers=bob).status_code == 200
    assert client.get("/contacts", headers=alice).json()["my_contacts"][0]["status"] == "accepted"
    assert client.get("/contacts", headers=bob).json()["protecting"][0]["name"] == "Alice"


def test_cannot_add_yourself_or_unknown_or_twice(client):
    alice, _ = signup(client, "Alice", "alice@example.com")
    signup(client, "Bob", "bob@example.com")
    assert client.post("/contacts", json={"email": "alice@example.com"}, headers=alice).status_code == 400
    assert client.post("/contacts", json={"email": "ghost@example.com"}, headers=alice).status_code == 404
    assert client.post("/contacts", json={"email": "bob@example.com"}, headers=alice).status_code == 201
    assert client.post("/contacts", json={"email": "bob@example.com"}, headers=alice).status_code == 409


def test_only_the_invited_person_can_accept(client):
    alice, _ = signup(client, "Alice", "alice@example.com")
    signup(client, "Bob", "bob@example.com")
    carol, _ = signup(client, "Carol", "carol@example.com")
    contact_id = client.post("/contacts", json={"email": "bob@example.com"}, headers=alice).json()["id"]
    assert client.post(f"/contacts/{contact_id}/accept", headers=alice).status_code == 404  # requester can't self-accept
    assert client.post(f"/contacts/{contact_id}/accept", headers=carol).status_code == 404  # stranger can't either


def test_either_side_can_remove_but_not_strangers(client):
    alice, _ = signup(client, "Alice", "alice@example.com")
    bob, _ = signup(client, "Bob", "bob@example.com")
    carol, _ = signup(client, "Carol", "carol@example.com")
    contact_id = client.post("/contacts", json={"email": "bob@example.com"}, headers=alice).json()["id"]
    assert client.delete(f"/contacts/{contact_id}", headers=carol).status_code == 404
    assert client.delete(f"/contacts/{contact_id}", headers=bob).status_code == 204
    assert client.get("/contacts", headers=alice).json()["my_contacts"] == []
