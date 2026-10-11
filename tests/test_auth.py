import time

import jwt

import security
from helpers import signup


def test_signup_returns_token_and_user(client):
    headers, user = signup(client, "Asha", "Asha@Example.com")
    assert user["email"] == "asha@example.com"  # emails are stored in lower case
    assert client.get("/me", headers=headers).json()["name"] == "Asha"


def test_duplicate_email_rejected(client):
    signup(client, "Asha", "asha@example.com")
    resp = client.post("/auth/signup", json={"name": "Other", "email": "ASHA@example.com", "password": "another-pass-1"})
    assert resp.status_code == 409


def test_bad_signup_input_rejected(client):
    base = {"name": "A", "email": "a@example.com", "password": "long-enough-1"}
    assert client.post("/auth/signup", json={**base, "email": "not-an-email"}).status_code == 422
    assert client.post("/auth/signup", json={**base, "password": "short"}).status_code == 422
    assert client.post("/auth/signup", json={**base, "name": ""}).status_code == 422


def test_login_success_and_failures(client):
    signup(client, "Asha", "asha@example.com", "correct-horse-1")
    ok = client.post("/auth/login", json={"email": "asha@example.com", "password": "correct-horse-1"})
    assert ok.status_code == 200 and ok.json()["access_token"]
    bad = client.post("/auth/login", json={"email": "asha@example.com", "password": "wrong-password"})
    unknown = client.post("/auth/login", json={"email": "nobody@example.com", "password": "whatever-1"})
    assert bad.status_code == 401 and unknown.status_code == 401
    assert bad.json() == unknown.json()  # same answer, so attackers can't tell which emails exist


def test_login_is_rate_limited(client):
    signup(client, "Asha", "asha@example.com")
    codes = [
        client.post("/auth/login", json={"email": "asha@example.com", "password": "wrong-password"}).status_code
        for _ in range(12)
    ]
    assert codes[:10] == [401] * 10
    assert codes[10] == 429


def test_password_is_not_stored_in_plain_text(client, db):
    signup(client, "Asha", "asha@example.com", "correct-horse-1")
    from common.db import User
    from sqlalchemy import select

    with db.Session() as s:
        stored = s.scalar(select(User.password_hash))
    assert "correct-horse-1" not in stored and stored.startswith("$2")


def test_protected_routes_need_a_valid_token(client):
    assert client.get("/me").status_code == 401
    assert client.get("/me", headers={"Authorization": "Bearer garbage"}).status_code == 401
    forged = jwt.encode({"sub": "1", "name": "x", "exp": int(time.time()) + 600}, "wrong-secret-" * 4, algorithm="HS256")
    assert client.get("/me", headers={"Authorization": f"Bearer {forged}"}).status_code == 401
    expired = jwt.encode({"sub": "1", "name": "x", "exp": int(time.time()) - 10}, security.jwt_secret(), algorithm="HS256")
    assert client.get("/me", headers={"Authorization": f"Bearer {expired}"}).status_code == 401
