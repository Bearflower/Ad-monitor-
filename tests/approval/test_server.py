import hashlib
import hmac
import json
from datetime import datetime, timezone

import pytest

from adwatch.approval.server import CallbackError, process_callback
from adwatch.storage.db import Database


def _headers(body: bytes, secret: str, timestamp: int, nonce: str = "n1"):
    message = f"{timestamp}.{nonce}.".encode() + body
    signature = hmac.new(
        secret.encode(), message, hashlib.sha256
    ).hexdigest()
    return {
        "X-Adwatch-Timestamp": str(timestamp),
        "X-Adwatch-Nonce": nonce,
        "X-Adwatch-Signature": signature,
    }


def test_callback_challenge_requires_valid_signature(tmp_path):
    database = Database(tmp_path / "test.sqlite3")
    database.migrate()
    body = json.dumps({"challenge": "abc"}).encode()
    now = int(datetime.now(timezone.utc).timestamp())

    response = process_callback(
        database,
        body,
        _headers(body, "secret", now),
        secret="secret",
        now_timestamp=now,
    )

    assert response == {"challenge": "abc"}


def test_callback_rejects_expired_timestamp(tmp_path):
    database = Database(tmp_path / "test.sqlite3")
    database.migrate()
    body = json.dumps({"challenge": "abc"}).encode()
    now = int(datetime.now(timezone.utc).timestamp())

    with pytest.raises(CallbackError, match="expired"):
        process_callback(
            database,
            body,
            _headers(body, "secret", now - 601),
            secret="secret",
            now_timestamp=now,
        )


def test_callback_rejects_replayed_event(tmp_path):
    database = Database(tmp_path / "test.sqlite3")
    database.migrate()
    payload = {"event_id": "event-1", "challenge": "abc"}
    body = json.dumps(payload).encode()
    now = int(datetime.now(timezone.utc).timestamp())
    headers = _headers(body, "secret", now)

    process_callback(
        database, body, headers, secret="secret", now_timestamp=now
    )
    with pytest.raises(CallbackError, match="replayed"):
        process_callback(
            database, body, headers, secret="secret", now_timestamp=now
        )
