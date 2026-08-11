from __future__ import annotations

import hashlib
import json
import uuid
from datetime import UTC, datetime

from adwatch.knowledge.models import ExtractedDocument, StoredVersion
from adwatch.storage.db import Database


def _now() -> str:
    return datetime.now(UTC).isoformat()


class KnowledgeRepository:
    def __init__(self, database: Database) -> None:
        self.database = database

    def upsert_source(
        self,
        *,
        source_key: str,
        source_type: str,
        name_zh: str,
        entry_url: str,
        allowed_hosts: tuple[str, ...],
        extractor_version: str,
        enabled: bool,
    ) -> str:
        source_id = str(uuid.uuid5(uuid.NAMESPACE_URL, source_key))
        now = _now()
        with self.database.transaction() as connection:
            connection.execute(
                """
                INSERT INTO knowledge_sources(
                    id, source_key, source_type, name_zh, entry_url,
                    allowed_hosts_json, enabled, extractor_version,
                    created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(source_key) DO UPDATE SET
                    source_type=excluded.source_type,
                    name_zh=excluded.name_zh,
                    entry_url=excluded.entry_url,
                    allowed_hosts_json=excluded.allowed_hosts_json,
                    enabled=excluded.enabled,
                    extractor_version=excluded.extractor_version,
                    updated_at=excluded.updated_at
                """,
                (
                    source_id,
                    source_key,
                    source_type,
                    name_zh,
                    entry_url,
                    json.dumps(allowed_hosts),
                    int(enabled),
                    extractor_version,
                    now,
                    now,
                ),
            )
        return source_id

    def store_extracted(
        self, source_key: str, extracted: ExtractedDocument
    ) -> StoredVersion:
        normalized = "\n".join(
            line.strip()
            for line in extracted.content_original.splitlines()
            if line.strip()
        )
        content_hash = hashlib.sha256(
            normalized.encode("utf-8")
        ).hexdigest()
        now = _now()
        with self.database.transaction() as connection:
            source = connection.execute(
                "SELECT id, extractor_version FROM knowledge_sources "
                "WHERE source_key=?",
                (source_key,),
            ).fetchone()
            if source is None:
                raise ValueError(f"unknown knowledge source: {source_key}")
            document = connection.execute(
                """
                SELECT id, current_version_id
                FROM knowledge_documents
                WHERE source_id=? AND canonical_url=?
                """,
                (source["id"], extracted.canonical_url),
            ).fetchone()
            if document is None:
                document_id = str(uuid.uuid4())
                connection.execute(
                    """
                    INSERT INTO knowledge_documents(
                        id, source_id, external_id, canonical_url,
                        document_type, site, status, first_seen_at,
                        last_seen_at, last_checked_at, published_at,
                        remote_updated_at
                    ) VALUES (?, ?, ?, ?, ?, 'shopee_th', 'active',
                              ?, ?, ?, ?, ?)
                    """,
                    (
                        document_id,
                        source["id"],
                        extracted.external_id,
                        extracted.canonical_url,
                        extracted.document_type,
                        now,
                        now,
                        now,
                        extracted.published_at,
                        extracted.remote_updated_at,
                    ),
                )
                previous_id = None
                version_number = 1
                change = "new"
            else:
                document_id = document["id"]
                previous = connection.execute(
                    """
                    SELECT id, version_number, content_hash
                    FROM knowledge_document_versions WHERE id=?
                    """,
                    (document["current_version_id"],),
                ).fetchone()
                connection.execute(
                    """
                    UPDATE knowledge_documents
                    SET status='active', last_seen_at=?, last_checked_at=?,
                        missing_successful_runs=0
                    WHERE id=?
                    """,
                    (now, now, document_id),
                )
                if previous and previous["content_hash"] == content_hash:
                    return StoredVersion(
                        document_id,
                        previous["id"],
                        previous["version_number"],
                        content_hash,
                        "unchanged",
                    )
                historical = connection.execute(
                    """
                    SELECT id, version_number
                    FROM knowledge_document_versions
                    WHERE document_id=? AND content_hash=?
                    """,
                    (document_id, content_hash),
                ).fetchone()
                if historical:
                    connection.execute(
                        "DELETE FROM knowledge_fts WHERE document_id=?",
                        (document_id,),
                    )
                    connection.execute(
                        """
                        INSERT INTO knowledge_fts(
                            chunk_id, document_id, version_id,
                            title_original, title_zh,
                            content_original, content_zh,
                            category, tags
                        )
                        SELECT
                            chunk.id, version.document_id, version.id,
                            version.title_original,
                            COALESCE(enrichment.title_zh, ''),
                            chunk.content_original, chunk.content_zh,
                            COALESCE(enrichment.category, ''),
                            COALESCE(enrichment.tags_json, '')
                        FROM knowledge_document_versions AS version
                        JOIN knowledge_chunks AS chunk
                          ON chunk.version_id=version.id
                        LEFT JOIN knowledge_enrichments AS enrichment
                          ON enrichment.version_id=version.id
                        WHERE version.id=?
                        """,
                        (historical["id"],),
                    )
                    connection.execute(
                        """
                        UPDATE knowledge_documents
                        SET current_version_id=?, last_seen_at=?,
                            last_checked_at=?, status='active',
                            missing_successful_runs=0, remote_updated_at=?
                        WHERE id=?
                        """,
                        (
                            historical["id"],
                            now,
                            now,
                            extracted.remote_updated_at,
                            document_id,
                        ),
                    )
                    return StoredVersion(
                        document_id,
                        historical["id"],
                        historical["version_number"],
                        content_hash,
                        "updated",
                    )
                previous_id = previous["id"] if previous else None
                version_number = int(
                    connection.execute(
                        """
                        SELECT COALESCE(MAX(version_number), 0) + 1
                        FROM knowledge_document_versions
                        WHERE document_id=?
                        """,
                        (document_id,),
                    ).fetchone()[0]
                )
                change = "updated"
            version_id = str(uuid.uuid4())
            chunk_id = str(uuid.uuid4())
            connection.execute(
                """
                INSERT INTO knowledge_document_versions(
                    id, document_id, version_number, content_hash,
                    title_original, language, content_original,
                    content_format, metadata_json, retrieved_at,
                    extractor_version, previous_version_id
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    version_id,
                    document_id,
                    version_number,
                    content_hash,
                    extracted.title_original,
                    extracted.language,
                    normalized,
                    extracted.content_format,
                    json.dumps(extracted.metadata, ensure_ascii=False),
                    now,
                    source["extractor_version"],
                    previous_id,
                ),
            )
            connection.execute(
                """
                INSERT INTO knowledge_enrichments(
                    version_id, status, created_at, updated_at
                ) VALUES (?, 'pending', ?, ?)
                """,
                (version_id, now, now),
            )
            connection.execute(
                """
                INSERT INTO knowledge_chunks(
                    id, version_id, chunk_index, content_original,
                    char_start, char_end
                ) VALUES (?, ?, 0, ?, 0, ?)
                """,
                (chunk_id, version_id, normalized, len(normalized)),
            )
            connection.execute(
                "DELETE FROM knowledge_fts WHERE document_id=?",
                (document_id,),
            )
            connection.execute(
                """
                INSERT INTO knowledge_fts(
                    chunk_id, document_id, version_id,
                    title_original, content_original
                ) VALUES (?, ?, ?, ?, ?)
                """,
                (
                    chunk_id,
                    document_id,
                    version_id,
                    extracted.title_original,
                    normalized,
                ),
            )
            connection.execute(
                """
                UPDATE knowledge_documents
                SET current_version_id=?, last_seen_at=?,
                    last_checked_at=?, status='active',
                    missing_successful_runs=0, remote_updated_at=?
                WHERE id=?
                """,
                (
                    version_id,
                    now,
                    now,
                    extracted.remote_updated_at,
                    document_id,
                ),
            )
        return StoredVersion(
            document_id, version_id, version_number, content_hash, change
        )

    def start_run(
        self,
        run_id: str,
        *,
        mode: str,
        source_id: str | None,
        started_at: datetime,
    ) -> None:
        with self.database.transaction() as connection:
            connection.execute(
                """
                INSERT INTO knowledge_collection_runs(
                    id, mode, source_id, started_at, status
                ) VALUES (?, ?, ?, ?, 'running')
                """,
                (run_id, mode, source_id, started_at.isoformat()),
            )

    def recover_interrupted_runs(self) -> int:
        with self.database.transaction() as connection:
            cursor = connection.execute(
                """
                UPDATE knowledge_collection_runs
                SET status='interrupted', finished_at=?
                WHERE status='running'
                """,
                (_now(),),
            )
            return cursor.rowcount
