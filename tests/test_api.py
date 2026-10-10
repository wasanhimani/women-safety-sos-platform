import sys
from pathlib import Path

import fakeredis
import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "services" / "sos-api"))
import app as sos  # noqa: E402


@pytest.fixture()
def client(monkeypatch):
    monkeypatch.setattr(sos, "r", fakeredis.FakeRedis(decode_responses=True))
    return TestClient(sos.app)


def test_health(client):
    assert client.get("/healthz").json() == {"status": "ok"}


def test_ready(client):
    assert client.get("/readyz").status_code == 200


def test_sos_is_queued(client):
    resp = client.post("/sos", json={"user_id": "u1", "latitude": 28.67, "longitude": 77.45})
    assert resp.status_code == 202
    assert sos.r.xlen("sos_alerts") == 1


def test_invalid_coordinates_rejected(client):
    resp = client.post("/sos", json={"user_id": "u1", "latitude": 999, "longitude": 0})
    assert resp.status_code == 422
