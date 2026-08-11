from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ExtractedDocument:
    canonical_url: str
    document_type: str
    title_original: str
    language: str
    content_original: str
    published_at: str | None
    remote_updated_at: str | None
    metadata: dict[str, object]
    external_id: str | None = None
    content_format: str = "text"


@dataclass(frozen=True)
class StoredVersion:
    document_id: str
    version_id: str
    version_number: int
    content_hash: str
    change: str


@dataclass(frozen=True)
class CollectionSummary:
    discovered: int = 0
    fetched: int = 0
    new: int = 0
    updated: int = 0
    unchanged: int = 0
    inactive: int = 0
    enriched: int = 0
    failed: int = 0
    errors: tuple[str, ...] = ()
    status: str = "success"
