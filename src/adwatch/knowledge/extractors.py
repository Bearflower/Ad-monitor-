from __future__ import annotations

from collections.abc import Iterable

from adwatch.knowledge.models import ExtractedDocument
from adwatch.knowledge.sources import canonicalize_url


class ExtractionError(ValueError):
    pass


def _clean_lines(values: Iterable[object], noise: set[str]) -> list[str]:
    lines = []
    for value in values:
        line = " ".join(str(value).split())
        if line and line not in noise:
            lines.append(line)
    return lines


def extract_structured_page(
    payload: dict[str, object], *, noise: tuple[str, ...] = ()
) -> ExtractedDocument:
    paragraphs = payload.get("paragraphs", ())
    tables = payload.get("tables", ())
    lines = _clean_lines(
        paragraphs if isinstance(paragraphs, list) else (), set(noise)
    )
    if isinstance(tables, list):
        for row in tables:
            if isinstance(row, list):
                cleaned = _clean_lines(row, set())
                if cleaned:
                    lines.append(" | ".join(cleaned))
    if not lines:
        raise ExtractionError("knowledge page content is empty")
    normalized_content = "\n".join(lines)
    if "window.HTML_LOADED" in normalized_content:
        raise ExtractionError("knowledge page is not rendered")
    url = str(payload.get("url", ""))
    title = " ".join(str(payload.get("title", "")).split())
    if not url or not title:
        raise ExtractionError("knowledge page URL and title are required")
    return ExtractedDocument(
        canonical_url=canonicalize_url(url),
        document_type=str(payload.get("document_type", "article")),
        title_original=title,
        language=str(payload.get("language", "unknown")),
        content_original=normalized_content,
        published_at=(
            str(payload["published_at"]) if payload.get("published_at") else None
        ),
        remote_updated_at=(
            str(payload["remote_updated_at"])
            if payload.get("remote_updated_at")
            else None
        ),
        metadata={
            key: value
            for key, value in payload.items()
            if key
            not in {
                "url",
                "title",
                "paragraphs",
                "tables",
                "published_at",
                "remote_updated_at",
            }
        },
    )
