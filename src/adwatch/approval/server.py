from __future__ import annotations

import hashlib
import hmac
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from adwatch.approval.feishu import handle_approval_action
from adwatch.approval.service import ApprovalService
from adwatch.storage.db import Database


class CallbackError(RuntimeError):
    pass


def process_callback(
    database: Database,
    body: bytes,
    headers: dict[str, str],
    *,
    secret: str,
    now_timestamp: int,
) -> dict[str, object]:
    try:
        timestamp = int(headers["X-Adwatch-Timestamp"])
        nonce = headers["X-Adwatch-Nonce"]
        supplied = headers["X-Adwatch-Signature"]
    except (KeyError, ValueError) as error:
        raise CallbackError("missing callback signature headers") from error
    if abs(now_timestamp - timestamp) > 300:
        raise CallbackError("callback timestamp expired")
    message = f"{timestamp}.{nonce}.".encode() + body
    expected = hmac.new(
        secret.encode(), message, hashlib.sha256
    ).hexdigest()
    if not hmac.compare_digest(expected, supplied):
        raise CallbackError("invalid callback signature")
    try:
        payload = json.loads(body)
    except json.JSONDecodeError as error:
        raise CallbackError("invalid callback JSON") from error
    if not isinstance(payload, dict):
        raise CallbackError("invalid callback payload")
    event_id = str(
        payload.get("event_id")
        or payload.get("header", {}).get("event_id", "")
    )
    if event_id:
        try:
            with database.transaction() as connection:
                connection.execute(
                    "INSERT INTO callback_events(event_id) VALUES (?)",
                    (event_id,),
                )
        except Exception as error:
            if "UNIQUE constraint failed" in str(error):
                raise CallbackError("callback event replayed") from error
            raise
    if "challenge" in payload:
        return {"challenge": str(payload["challenge"])}
    action_payload = payload.get("event", payload)
    if not isinstance(action_payload, dict):
        raise CallbackError("invalid callback event")
    decision = handle_approval_action(
        ApprovalService(database), action_payload
    )
    return {
        "status": decision.status,
        "approval_id": decision.approval_id,
    }


def serve_callback(
    database: Database,
    *,
    secret: str,
    host: str,
    port: int,
) -> None:
    class Handler(BaseHTTPRequestHandler):
        def do_POST(self) -> None:
            length = int(self.headers.get("Content-Length", "0"))
            body = self.rfile.read(length)
            from time import time

            try:
                response = process_callback(
                    database,
                    body,
                    {key: value for key, value in self.headers.items()},
                    secret=secret,
                    now_timestamp=int(time()),
                )
                status = 200
            except CallbackError as error:
                response = {"error": str(error)}
                status = 400
            encoded = json.dumps(response).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(encoded)))
            self.end_headers()
            self.wfile.write(encoded)

        def log_message(self, format: str, *args: object) -> None:
            return

    ThreadingHTTPServer((host, port), Handler).serve_forever()
