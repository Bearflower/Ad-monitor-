from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import sqlite3
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import quote

from adwatch.storage.db import Database

LOGGER = logging.getLogger("adwatch.evidence")
MAX_CONTENT_BYTES = 5 * 1024 * 1024

_MEDIA_EXTENSIONS = {
    "application/pdf": ".pdf",
    "image/jpeg": ".jpg",
    "image/png": ".png",
}
_OBJECT_TABLES = {
    "expense": "expense_entries",
    "capital": "capital_entries",
    "withdrawal": "withdrawal_entries",
    "ad_funding": "ad_funding_entries",
    "review_order_cost": "review_order_costs",
    "purchase_receipt": "purchase_receipts",
    "inventory_movement": "inventory_movements",
    "profit_period": "profit_periods",
    "profit_payment": "profit_payments",
    "balance_snapshot": "balance_snapshots",
    "financial_adjustment": "financial_adjustments",
    "balance_reconciliation": "balance_reconciliations",
}
_STORED_NAME = re.compile(r"^[0-9a-f]{32}\.(?:pdf|jpg|png)$")


class EvidenceError(ValueError):
    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


@dataclass(frozen=True)
class EvidenceDocument:
    id: str
    object_type: str
    object_id: str
    display_name: str
    stored_name: str
    media_type: str
    size_bytes: int
    sha256: str
    note: str
    created_by: str
    created_at: str


@dataclass(frozen=True)
class EvidenceDownload:
    document: EvidenceDocument
    content: bytes

    @property
    def headers(self) -> dict[str, str]:
        suffix = _MEDIA_EXTENSIONS[self.document.media_type]
        fallback = f"evidence-{self.document.id[:12]}{suffix}"
        encoded_name = quote(self.document.display_name, safe="")
        return {
            "Content-Type": self.document.media_type,
            "X-Content-Type-Options": "nosniff",
            "Content-Disposition": (
                f'attachment; filename="{fallback}"; '
                f"filename*=UTF-8''{encoded_name}"
            ),
        }


class EvidenceService:
    def __init__(self, database: Database, root: Path) -> None:
        self.database = database
        self.root = root.expanduser().resolve()

    def attach(
        self,
        *,
        object_type: str,
        object_id: str,
        display_name: str,
        media_type: str,
        content: bytes,
        created_by: str,
        note: str = "",
    ) -> EvidenceDocument:
        object_id = object_id.strip()
        created_by = created_by.strip()
        note = note.strip()
        self._validate_reference(object_type, object_id)
        self._validate_name(display_name)
        self._validate_actor(created_by)
        if len(note) > 2000:
            raise EvidenceError("note_too_long")
        self._validate_content(media_type, content)

        digest = hashlib.sha256(content).hexdigest()
        duplicate = self._find_duplicate(object_type, object_id, digest)
        if duplicate is not None:
            return duplicate

        evidence_id = uuid.uuid4().hex
        stored_name = evidence_id + _MEDIA_EXTENSIONS[media_type]
        created_at = datetime.now(UTC).isoformat()
        document = EvidenceDocument(
            id=evidence_id,
            object_type=object_type,
            object_id=object_id,
            display_name=display_name,
            stored_name=stored_name,
            media_type=media_type,
            size_bytes=len(content),
            sha256=digest,
            note=note,
            created_by=created_by,
            created_at=created_at,
        )

        self.root.mkdir(parents=True, exist_ok=True)
        final_path = self._managed_path(stored_name)
        temporary_path = self._managed_path(f"{stored_name}.tmp", temporary=True)
        try:
            with temporary_path.open("xb") as file:
                os.chmod(temporary_path, 0o600)
                file.write(content)
                file.flush()
                os.fsync(file.fileno())
            os.replace(temporary_path, final_path)
            try:
                with self.database.transaction() as connection:
                    self._insert_metadata(connection, document)
            except sqlite3.IntegrityError:
                final_path.unlink(missing_ok=True)
                duplicate = self._find_duplicate(object_type, object_id, digest)
                if duplicate is not None:
                    return duplicate
                LOGGER.warning(
                    "evidence_attach_failed id=%s code=metadata_write_failed",
                    evidence_id,
                )
                raise EvidenceError("metadata_write_failed") from None
            except Exception:  # noqa: BLE001 - clean up any metadata failure
                final_path.unlink(missing_ok=True)
                LOGGER.warning(
                    "evidence_attach_failed id=%s code=metadata_write_failed",
                    evidence_id,
                )
                raise EvidenceError("metadata_write_failed") from None
        finally:
            temporary_path.unlink(missing_ok=True)

        LOGGER.info("evidence_attached id=%s", document.id)
        return document

    def read(self, evidence_id: str) -> EvidenceDownload:
        document = self.get(evidence_id)
        path = self._managed_path(document.stored_name)
        try:
            content = path.read_bytes()
        except OSError:
            LOGGER.warning(
                "evidence_read_failed id=%s code=content_unavailable",
                evidence_id,
            )
            raise EvidenceError("content_unavailable") from None
        if (
            len(content) != document.size_bytes
            or hashlib.sha256(content).hexdigest() != document.sha256
        ):
            LOGGER.warning(
                "evidence_read_failed id=%s code=content_integrity_failed",
                evidence_id,
            )
            raise EvidenceError("content_integrity_failed")
        self._validate_content(document.media_type, content)
        LOGGER.info("evidence_read id=%s", document.id)
        return EvidenceDownload(document=document, content=content)

    def get(self, evidence_id: str) -> EvidenceDocument:
        with self.database.connect() as connection:
            row = connection.execute(
                "SELECT * FROM evidence_documents WHERE id=?",
                (evidence_id,),
            ).fetchone()
        if row is None:
            raise EvidenceError("evidence_not_found")
        return self._from_row(row)

    def list_for_object(
        self, object_type: str, object_id: str
    ) -> tuple[EvidenceDocument, ...]:
        with self.database.connect() as connection:
            rows = connection.execute(
                """
                SELECT * FROM evidence_documents
                WHERE object_type=? AND object_id=?
                ORDER BY created_at, id
                """,
                (object_type, object_id),
            ).fetchall()
        return tuple(self._from_row(row) for row in rows)

    def _validate_reference(self, object_type: str, object_id: str) -> None:
        table = _OBJECT_TABLES.get(object_type)
        if table is None:
            raise EvidenceError("unsupported_object_type")
        if not object_id:
            raise EvidenceError("object_not_found")
        with self.database.connect() as connection:
            row = connection.execute(
                f"SELECT 1 FROM {table} WHERE id=?",
                (object_id,),
            ).fetchone()
        if row is None:
            raise EvidenceError("object_not_found")

    @staticmethod
    def _validate_name(display_name: str) -> None:
        if (
            not display_name
            or len(display_name) > 200
            or display_name in {".", ".."}
            or "/" in display_name
            or "\\" in display_name
            or any(ord(character) < 32 or ord(character) == 127 for character in display_name)
        ):
            raise EvidenceError("invalid_display_name")

    @staticmethod
    def _validate_actor(created_by: str) -> None:
        if not created_by or len(created_by) > 200:
            raise EvidenceError("invalid_actor")

    @staticmethod
    def _validate_content(media_type: str, content: bytes) -> None:
        if media_type not in _MEDIA_EXTENSIONS:
            raise EvidenceError("unsupported_media_type")
        if not content:
            raise EvidenceError("empty_content")
        if len(content) > MAX_CONTENT_BYTES:
            raise EvidenceError("too_large")
        matches = {
            "application/pdf": content.startswith(b"%PDF-"),
            "image/jpeg": content.startswith(b"\xff\xd8\xff"),
            "image/png": content.startswith(b"\x89PNG\r\n\x1a\n"),
        }
        if not matches[media_type]:
            raise EvidenceError("media_type_mismatch")

    def _managed_path(self, stored_name: str, *, temporary: bool = False) -> Path:
        expected = (
            stored_name[:-4]
            if temporary and stored_name.endswith(".tmp")
            else stored_name
        )
        if not _STORED_NAME.fullmatch(expected):
            raise EvidenceError("unsafe_storage_name")
        path = (self.root / stored_name).resolve()
        if path.parent != self.root:
            raise EvidenceError("unsafe_storage_name")
        return path

    def _find_duplicate(
        self, object_type: str, object_id: str, digest: str
    ) -> EvidenceDocument | None:
        with self.database.connect() as connection:
            row = connection.execute(
                """
                SELECT * FROM evidence_documents
                WHERE object_type=? AND object_id=? AND sha256=?
                """,
                (object_type, object_id, digest),
            ).fetchone()
        return None if row is None else self._from_row(row)

    @staticmethod
    def _insert_metadata(
        connection: sqlite3.Connection, document: EvidenceDocument
    ) -> None:
        connection.execute(
            """
            INSERT INTO evidence_documents(
                id, object_type, object_id, display_name, stored_name,
                media_type, size_bytes, sha256, note, created_by, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                document.id,
                document.object_type,
                document.object_id,
                document.display_name,
                document.stored_name,
                document.media_type,
                document.size_bytes,
                document.sha256,
                document.note,
                document.created_by,
                document.created_at,
            ),
        )
        after = json.dumps(
            {
                "object_type": document.object_type,
                "object_id": document.object_id,
                "display_name": document.display_name,
                "media_type": document.media_type,
                "size_bytes": document.size_bytes,
                "sha256": document.sha256,
            },
            ensure_ascii=False,
            sort_keys=True,
        )
        connection.execute(
            """
            INSERT INTO audit_events(
                id, object_type, object_id, action, before_json,
                after_json, actor, reason, created_at
            ) VALUES (?, 'evidence_document', ?, 'attached', NULL, ?, ?, ?, ?)
            """,
            (
                uuid.uuid4().hex,
                document.id,
                after,
                document.created_by,
                document.note or None,
                document.created_at,
            ),
        )

    @staticmethod
    def _from_row(row: Any) -> EvidenceDocument:
        return EvidenceDocument(
            id=str(row["id"]),
            object_type=str(row["object_type"]),
            object_id=str(row["object_id"]),
            display_name=str(row["display_name"]),
            stored_name=str(row["stored_name"]),
            media_type=str(row["media_type"]),
            size_bytes=int(row["size_bytes"]),
            sha256=str(row["sha256"]),
            note=str(row["note"]),
            created_by=str(row["created_by"]),
            created_at=str(row["created_at"]),
        )
