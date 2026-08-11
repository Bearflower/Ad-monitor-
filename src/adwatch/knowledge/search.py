from __future__ import annotations

from adwatch.storage.db import Database


class KnowledgeReadModel:
    def __init__(self, database: Database) -> None:
        self.database = database

    def search(
        self,
        query: str,
        *,
        limit: int = 20,
        offset: int = 0,
        category: str = "",
        status: str = "active",
    ) -> list[dict[str, object]]:
        query = query.strip()
        if not query:
            return []
        if len(query) > 200:
            raise ValueError("knowledge query is limited to 200 characters")
        limit = max(1, min(limit, 100))
        with self.database.connect() as connection:
            rows = connection.execute(
                """
                SELECT d.id AS document_id, d.canonical_url, d.status,
                       d.published_at, v.id AS version_id,
                       v.version_number, v.title_original,
                       e.title_zh, e.summary_zh, e.category,
                       e.tags_json, e.status AS enrichment_status,
                       snippet(knowledge_fts, 5, '[', ']', ' … ', 24)
                           AS snippet,
                       bm25(knowledge_fts, 8.0, 8.0, 2.0, 2.0, 3.0, 4.0)
                           AS score
                FROM knowledge_fts
                JOIN knowledge_documents d
                  ON d.id=knowledge_fts.document_id
                JOIN knowledge_document_versions v
                  ON v.id=knowledge_fts.version_id
                LEFT JOIN knowledge_enrichments e ON e.version_id=v.id
                WHERE knowledge_fts MATCH ?
                  AND d.current_version_id=v.id
                  AND (?='' OR d.status=?)
                  AND (?='' OR e.category=?)
                ORDER BY score, d.published_at DESC
                LIMIT ? OFFSET ?
                """,
                (
                    query,
                    status,
                    status,
                    category,
                    category,
                    limit,
                    max(0, offset),
                ),
            ).fetchall()
        return [dict(row) for row in rows]

    def document(self, document_id: str) -> dict[str, object]:
        with self.database.connect() as connection:
            current = connection.execute(
                """
                SELECT d.*, v.version_number, v.title_original, v.language,
                       v.content_original, v.retrieved_at,
                       e.title_zh, e.summary_zh, e.category, e.tags_json,
                       e.key_rules_json, e.status AS enrichment_status
                FROM knowledge_documents d
                JOIN knowledge_document_versions v
                  ON v.id=d.current_version_id
                LEFT JOIN knowledge_enrichments e ON e.version_id=v.id
                WHERE d.id=?
                """,
                (document_id,),
            ).fetchone()
            if current is None:
                raise KeyError(document_id)
            versions = connection.execute(
                """
                SELECT id, version_number, content_hash, retrieved_at
                FROM knowledge_document_versions
                WHERE document_id=? ORDER BY version_number DESC
                """,
                (document_id,),
            ).fetchall()
        result = dict(current)
        result["versions"] = [dict(row) for row in versions]
        return result

    def status(self) -> dict[str, object]:
        with self.database.connect() as connection:
            documents = connection.execute(
                "SELECT COUNT(*) FROM knowledge_documents"
            ).fetchone()[0]
            pending = connection.execute(
                "SELECT COUNT(*) FROM knowledge_enrichments "
                "WHERE status='pending'"
            ).fetchone()[0]
            run = connection.execute(
                """
                SELECT * FROM knowledge_collection_runs
                ORDER BY started_at DESC LIMIT 1
                """
            ).fetchone()
        return {
            "documents": documents,
            "pending_enrichments": pending,
            "last_run": dict(run) if run else None,
        }
