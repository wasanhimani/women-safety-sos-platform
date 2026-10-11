import os
import sys
from pathlib import Path

# Must be set before the app is imported
os.environ.setdefault("JWT_SECRET", "test-secret-for-unit-tests-only-0123456789")
os.environ["DATABASE_URL"] = "sqlite+pysqlite://"

ROOT = Path(__file__).resolve().parents[1]
for p in (ROOT, ROOT / "services" / "sos-api", ROOT / "services" / "notifier"):
    sys.path.insert(0, str(p))

import fakeredis  # noqa: E402
import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

import app as sos  # noqa: E402
from common.db import Database  # noqa: E402


@pytest.fixture()
def fake_redis():
    return fakeredis.FakeRedis(server=fakeredis.FakeServer(), decode_responses=True)


@pytest.fixture()
def db():
    database = Database("sqlite+pysqlite://")
    database.init_schema()
    return database


@pytest.fixture()
def client(monkeypatch, fake_redis, db):
    monkeypatch.setattr(sos, "r", fake_redis)
    monkeypatch.setattr(sos, "db", db)
    return TestClient(sos.app)
