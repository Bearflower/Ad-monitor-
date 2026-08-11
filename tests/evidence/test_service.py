from __future__ import annotations

import logging
import sqlite3

import pytest

from adwatch.evidence.service import EvidenceError, EvidenceService
from adwatch.storage.db import Database

PDF = b"%PDF-1.7\nminimal"
PNG = b"\x89PNG\r\n\x1a\nminimal"
JPEG = b"\xff\xd8\xff\xe0minimal"


@pytest.fixture
def evidence(tmp_path):
    database = Database(tmp_path / "adwatch.sqlite3")
    database.migrate()
    with database.transaction() as connection:
        connection.execute(
            """
            INSERT INTO financial_adjustments(
                id, adjustment_type, amount_cny, occurred_on, reason,
                status, created_by, created_at
            ) VALUES (
                'adjustment-1','income','10','2026-08-04','补记',
                'confirmed','yl','2026-08-04T00:00:00Z'
            )
            """
        )
    return database, EvidenceService(database, tmp_path / "evidence")


@pytest.mark.parametrize(
    ("name", "media_type", "content", "suffix"),
    [
        ("invoice.pdf", "application/pdf", PDF, ".pdf"),
        ("proof.png", "image/png", PNG, ".png"),
        ("receipt.jpg", "image/jpeg", JPEG, ".jpg"),
    ],
)
def test_attach_and_read_supported_evidence(
    evidence, name, media_type, content, suffix
):
    database, service = evidence

    document = service.attach(
        object_type="financial_adjustment",
        object_id="adjustment-1",
        display_name=name,
        media_type=media_type,
        content=content,
        created_by="yl",
    )
    download = service.read(document.id)
    listed = service.list_for_object(
        "financial_adjustment", "adjustment-1"
    )

    assert document.stored_name.endswith(suffix)
    assert "/" not in document.stored_name
    assert str(service.root) not in document.stored_name
    assert download.content == content
    assert listed == (document,)
    assert download.headers["Content-Type"] == media_type
    assert download.headers["X-Content-Type-Options"] == "nosniff"
    assert "attachment;" in download.headers["Content-Disposition"]
    assert "filename*=UTF-8''" in download.headers["Content-Disposition"]
    with database.connect() as connection:
        audit = connection.execute(
            """
            SELECT action, actor FROM audit_events
            WHERE object_type='evidence_document' AND object_id=?
            """,
            (document.id,),
        ).fetchone()
    assert tuple(audit) == ("attached", "yl")


@pytest.mark.parametrize(
    "display_name",
    ["../secret.pdf", "folder/secret.pdf", "folder\\secret.pdf", "bad\n.pdf"],
)
def test_rejects_path_and_control_character_names(evidence, display_name):
    _, service = evidence

    with pytest.raises(EvidenceError, match="invalid_display_name"):
        service.attach(
            object_type="financial_adjustment",
            object_id="adjustment-1",
            display_name=display_name,
            media_type="application/pdf",
            content=PDF,
            created_by="yl",
        )


@pytest.mark.parametrize(
    ("media_type", "content", "code"),
    [
        ("image/png", PDF, "media_type_mismatch"),
        ("text/plain", b"hello", "unsupported_media_type"),
        ("application/pdf", b"not a pdf", "media_type_mismatch"),
    ],
)
def test_rejects_unsupported_or_spoofed_content(
    evidence, media_type, content, code
):
    _, service = evidence

    with pytest.raises(EvidenceError, match=code):
        service.attach(
            object_type="financial_adjustment",
            object_id="adjustment-1",
            display_name="evidence.pdf",
            media_type=media_type,
            content=content,
            created_by="yl",
        )


def test_rejects_empty_and_over_five_mib(evidence):
    _, service = evidence

    for content, code in ((b"", "empty_content"), (PDF + b"x" * 5242880, "too_large")):
        with pytest.raises(EvidenceError, match=code):
            service.attach(
                object_type="financial_adjustment",
                object_id="adjustment-1",
                display_name="large.pdf",
                media_type="application/pdf",
                content=content,
                created_by="yl",
            )


def test_duplicate_content_is_idempotent(evidence):
    database, service = evidence
    arguments = {
        "object_type": "financial_adjustment",
        "object_id": "adjustment-1",
        "display_name": "invoice.pdf",
        "media_type": "application/pdf",
        "content": PDF,
        "created_by": "yl",
    }

    first = service.attach(**arguments)
    second = service.attach(**arguments)

    assert second.id == first.id
    assert len(list(service.root.iterdir())) == 1
    with database.connect() as connection:
        count = connection.execute(
            "SELECT COUNT(*) FROM evidence_documents"
        ).fetchone()[0]
    assert count == 1


@pytest.mark.parametrize(
    ("object_type", "object_id", "code"),
    [
        ("unsupported", "adjustment-1", "unsupported_object_type"),
        ("financial_adjustment", "missing", "object_not_found"),
    ],
)
def test_rejects_unknown_objects(evidence, object_type, object_id, code):
    _, service = evidence

    with pytest.raises(EvidenceError, match=code):
        service.attach(
            object_type=object_type,
            object_id=object_id,
            display_name="invoice.pdf",
            media_type="application/pdf",
            content=PDF,
            created_by="yl",
        )


def test_database_failure_removes_new_file(evidence, monkeypatch):
    _, service = evidence

    def fail_insert(*_args, **_kwargs):
        raise sqlite3.OperationalError("forced")

    monkeypatch.setattr(service, "_insert_metadata", fail_insert)

    with pytest.raises(EvidenceError, match="metadata_write_failed"):
        service.attach(
            object_type="financial_adjustment",
            object_id="adjustment-1",
            display_name="invoice.pdf",
            media_type="application/pdf",
            content=PDF,
            created_by="yl",
        )

    assert list(service.root.iterdir()) == []


def test_read_rejects_tampered_storage_name(evidence):
    database, service = evidence
    document = service.attach(
        object_type="financial_adjustment",
        object_id="adjustment-1",
        display_name="invoice.pdf",
        media_type="application/pdf",
        content=PDF,
        created_by="yl",
    )
    with database.transaction() as connection:
        connection.execute(
            "UPDATE evidence_documents SET stored_name='../outside.pdf' WHERE id=?",
            (document.id,),
        )

    with pytest.raises(EvidenceError, match="unsafe_storage_name"):
        service.read(document.id)


def test_logs_never_include_root_or_file_content(evidence, caplog):
    _, service = evidence
    caplog.set_level(logging.INFO, logger="adwatch.evidence")

    document = service.attach(
        object_type="financial_adjustment",
        object_id="adjustment-1",
        display_name="invoice.pdf",
        media_type="application/pdf",
        content=PDF,
        created_by="yl",
    )
    service.read(document.id)

    assert document.id in caplog.text
    assert str(service.root) not in caplog.text
    assert PDF.decode() not in caplog.text
