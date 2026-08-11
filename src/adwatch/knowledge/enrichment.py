from __future__ import annotations

import json
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Protocol

from adwatch.knowledge.models import ExtractedDocument


class EnrichmentError(RuntimeError):
    pass


@dataclass(frozen=True)
class EnrichmentResult:
    status: str
    title_zh: str = ""
    summary_zh: str = ""
    category: str = ""
    tags: tuple[str, ...] = ()
    key_rules: tuple[dict[str, str], ...] = ()
    model: str = ""
    response_id: str = ""


class ResponseTransport(Protocol):
    def create_response(
        self, api_key: str, payload: dict[str, object]
    ) -> dict[str, object]: ...


class UrllibResponseTransport:
    def create_response(
        self, api_key: str, payload: dict[str, object]
    ) -> dict[str, object]:
        request = urllib.request.Request(
            "https://api.openai.com/v1/responses",
            data=json.dumps(payload).encode("utf-8"),
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=60) as response:
                return json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as error:
            raise EnrichmentError(
                f"OpenAI request failed with HTTP {error.code}"
            ) from error


class OpenAIEnricher:
    def __init__(
        self,
        *,
        api_key: str,
        model: str,
        enabled: bool = True,
        transport: ResponseTransport | None = None,
    ) -> None:
        self.api_key = api_key
        self.model = model
        self.enabled = enabled
        self.transport = transport or UrllibResponseTransport()

    def enrich(self, document: ExtractedDocument) -> EnrichmentResult:
        if not self.enabled or not self.api_key:
            return EnrichmentResult(status="skipped", model=self.model)
        schema = {
            "type": "object",
            "additionalProperties": False,
            "required": [
                "title_zh",
                "summary_zh",
                "category",
                "tags",
                "key_rules",
            ],
            "properties": {
                "title_zh": {"type": "string"},
                "summary_zh": {"type": "string"},
                "category": {"type": "string"},
                "tags": {"type": "array", "items": {"type": "string"}},
                "key_rules": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "additionalProperties": False,
                        "required": [
                            "statement_zh",
                            "source_quote",
                            "source_locator",
                        ],
                        "properties": {
                            "statement_zh": {"type": "string"},
                            "source_quote": {"type": "string"},
                            "source_locator": {"type": "string"},
                        },
                    },
                },
            },
        }
        response = self.transport.create_response(
            self.api_key,
            {
                "model": self.model,
                "input": [
                    {
                        "role": "system",
                        "content": (
                            "Translate and summarize faithfully in Chinese. "
                            "Never invent dates, fees, thresholds, or penalties."
                        ),
                    },
                    {
                        "role": "user",
                        "content": (
                            f"Title: {document.title_original}\n"
                            f"Language: {document.language}\n"
                            f"Content:\n{document.content_original}"
                        ),
                    },
                ],
                "text": {
                    "format": {
                        "type": "json_schema",
                        "name": "knowledge_enrichment",
                        "strict": True,
                        "schema": schema,
                    }
                },
            },
        )
        raw = response.get("output_text")
        if not isinstance(raw, str):
            raise EnrichmentError("OpenAI response has no structured output")
        try:
            parsed = json.loads(raw)
            rules = tuple(parsed["key_rules"])
            tags = tuple(str(tag) for tag in parsed["tags"])
        except (KeyError, TypeError, json.JSONDecodeError) as error:
            raise EnrichmentError("OpenAI structured output is invalid") from error
        for rule in rules:
            quote = str(rule.get("source_quote", ""))
            if not quote or quote not in document.content_original:
                raise EnrichmentError(
                    "OpenAI evidence quote is absent from original content"
                )
        return EnrichmentResult(
            status="completed",
            title_zh=str(parsed["title_zh"]),
            summary_zh=str(parsed["summary_zh"]),
            category=str(parsed["category"]),
            tags=tags,
            key_rules=rules,
            model=self.model,
            response_id=str(response.get("id", "")),
        )
