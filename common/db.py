"""Database tables and connection helper shared by the API and the notifier."""
import logging
import time
from datetime import datetime, timezone

from sqlalchemy import (
    DateTime,
    Index,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
    create_engine,
    text,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, sessionmaker
from sqlalchemy.pool import StaticPool

log = logging.getLogger("db")


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Base(DeclarativeBase):
    pass


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(80))
    email: Mapped[str] = mapped_column(String(254), unique=True, index=True)
    password_hash: Mapped[str] = mapped_column(String(100))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class Contact(Base):
    """requester names addressee as a trusted contact. Alerts flow requester -> addressee
    once the addressee has accepted."""

    __tablename__ = "contacts"
    __table_args__ = (UniqueConstraint("requester_id", "addressee_id"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    requester_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    addressee_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    status: Mapped[str] = mapped_column(String(10), default="pending")  # pending | accepted
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class Alert(Base):
    __tablename__ = "alerts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    stream_id: Mapped[str] = mapped_column(String(40), unique=True)  # Redis stream id: makes retries safe
    sender_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    latitude: Mapped[float] = mapped_column(Float)
    longitude: Mapped[float] = mapped_column(Float)
    message: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    kind: Mapped[str] = mapped_column(String(20), default="sos", server_default="sos")  # sos | walk_missed | safe


class Delivery(Base):
    __tablename__ = "deliveries"
    __table_args__ = (UniqueConstraint("alert_id", "recipient_id"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    alert_id: Mapped[int] = mapped_column(ForeignKey("alerts.id", ondelete="CASCADE"), index=True)
    recipient_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    delivered_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    seen_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class Walk(Base):
    """A walk-home timer. The server watches the deadline, so it still fires if the phone dies."""

    __tablename__ = "walks"
    __table_args__ = (
        # at most one unfinished walk per person
        Index("one_open_walk_per_user", "user_id", unique=True,
              postgresql_where=text("status IN ('active','alerted')"), sqlite_where=text("status IN ('active','alerted')")),
        Index("walks_status_due", "status", "due_at"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    note: Mapped[str] = mapped_column(String(100), default="")
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    due_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    status: Mapped[str] = mapped_column(String(10), default="active")  # active | alerted | safe | cancelled
    last_latitude: Mapped[float] = mapped_column(Float)
    last_longitude: Mapped[float] = mapped_column(Float)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    alerted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class Database:
    def __init__(self, url: str):
        kwargs = {"pool_pre_ping": True}
        if url.startswith("sqlite"):
            kwargs = {"connect_args": {"check_same_thread": False}, "poolclass": StaticPool}
        self.engine = create_engine(url, **kwargs)
        self.Session = sessionmaker(self.engine, expire_on_commit=False)

    def init_schema(self) -> None:
        """Create tables if missing. Safe to call from several pods at once."""
        with self.engine.begin() as conn:
            if self.engine.dialect.name == "postgresql":
                conn.execute(text("SELECT pg_advisory_xact_lock(727001)"))
            Base.metadata.create_all(conn)
            if self.engine.dialect.name == "postgresql":
                # create_all never changes a table that already exists, so new columns need an explicit upgrade step
                conn.execute(text("ALTER TABLE alerts ADD COLUMN IF NOT EXISTS kind VARCHAR(20) NOT NULL DEFAULT 'sos'"))

    def wait_until_ready(self, delay: float = 2.0) -> None:
        """Block until the database is reachable and the tables exist."""
        while True:
            try:
                self.init_schema()
                return
            except Exception as exc:  # database not up yet
                log.warning("database not ready (%s), retrying in %.0fs", type(exc).__name__, delay)
                time.sleep(delay)
