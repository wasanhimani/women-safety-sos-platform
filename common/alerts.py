"""Saving an alert and delivering it to the sender's accepted contacts. Shared by the notifier and the API."""
from datetime import datetime, timezone

from sqlalchemy import select

from common.db import Alert, Contact, Database, Delivery, User


class BadMessage(Exception):
    """The message can never be processed (retrying would not help)."""


def store_alert(db: Database, stream_id: str, fields: dict) -> dict:
    """Save the alert and one delivery row per accepted contact. Safe to run twice for the
    same message: the unique stream id and (alert, recipient) pair stop duplicates."""
    try:
        sender_id = int(fields["sender_id"])
        latitude = float(fields["latitude"])
        longitude = float(fields["longitude"])
        created_at = float(fields["created_at"])
        message = str(fields.get("message", ""))[:280]
        kind = str(fields.get("kind", "sos"))
    except (KeyError, ValueError) as exc:
        raise BadMessage(f"malformed alert: {exc!r}")

    with db.Session() as session, session.begin():
        sender = session.get(User, sender_id)
        if sender is None:
            raise BadMessage(f"unknown sender {sender_id}")
        alert = session.scalar(select(Alert).where(Alert.stream_id == stream_id))
        if alert is None:
            alert = Alert(
                stream_id=stream_id,
                sender_id=sender_id,
                latitude=latitude,
                longitude=longitude,
                message=message,
                kind=kind,
                created_at=datetime.fromtimestamp(created_at, timezone.utc),
            )
            session.add(alert)
            session.flush()
        recipients = session.scalars(
            select(Contact.addressee_id).where(Contact.requester_id == sender_id, Contact.status == "accepted")
        ).all()
        already = set(session.scalars(select(Delivery.recipient_id).where(Delivery.alert_id == alert.id)).all())
        for recipient_id in recipients:
            if recipient_id not in already:
                session.add(Delivery(alert_id=alert.id, recipient_id=recipient_id))
        return {
            "sender": sender.name,
            "message": message,
            "latitude": latitude,
            "longitude": longitude,
            "recipients": len(recipients),
            "created_at": created_at,
            "kind": kind,
        }
