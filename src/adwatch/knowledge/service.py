from __future__ import annotations

import time
from collections.abc import Callable

from adwatch.collectors.ziniao_client import ZiniaoApiError
from adwatch.knowledge.extractors import ExtractionError, extract_structured_page
from adwatch.knowledge.models import CollectionSummary
from adwatch.knowledge.repository import KnowledgeRepository
from adwatch.knowledge.sources import SourceDefinition, is_allowed_url


class KnowledgeService:
    def __init__(
        self,
        repository: KnowledgeRepository,
        client: object,
        *,
        store_id: str,
        store_ids: dict[str, str] | None = None,
        sources: tuple[SourceDefinition, ...],
        sleeper: Callable[[float], None] = time.sleep,
        request_delay_ms: int = 1500,
        max_pages: int = 50,
    ) -> None:
        self.repository = repository
        self.client = client
        self.store_id = store_id
        self.store_ids = store_ids or {}
        self.sources = sources
        self.sleeper = sleeper
        self.request_delay_ms = request_delay_ms
        self.max_pages = max_pages

    def bootstrap(self) -> CollectionSummary:
        return self._collect()

    def update(self) -> CollectionSummary:
        return self._collect()

    def _collect(self) -> CollectionSummary:
        discovered = fetched = new = updated = unchanged = 0
        failed = 0
        errors: list[str] = []
        for source in self.sources:
            if not source.enabled:
                continue
            self.repository.upsert_source(
                source_key=source.key,
                source_type=source.source_type,
                name_zh=source.name_zh,
                entry_url=source.entry_url,
                allowed_hosts=source.allowed_hosts,
                extractor_version=source.extractor_version,
                enabled=True,
            )
            # 第一层：从 entry_url 发现所有链接
            source_store_id = self.store_ids.get(
                source.key, self.store_id
            )
            listing = self.client.page_content(
                source_store_id,
                url=source.entry_url,
                content_format="structured",
            )
            links = listing.get("links", [])
            if not isinstance(links, list):
                links = []
            all_links: set[str] = set()
            category_links: list[str] = []
            for url in links:
                url_str = str(url)
                if not is_allowed_url(source, url_str):
                    continue
                if "/article/" in url_str or "/courseDetail/" in url_str:
                    all_links.add(url_str)
                elif "/category" in url_str or "/edu/course" in url_str:
                    category_links.append(url_str)

            # 第二层：爬取分类页面，发现更多文章链接
            for cat_url in category_links[:10]:  # 最多爬 10 个分类页
                try:
                    cat_page = self.client.page_content(
                        source_store_id,
                        url=cat_url,
                        content_format="structured",
                    )
                    cat_links = cat_page.get("links", [])
                    if isinstance(cat_links, list):
                        for cl in cat_links:
                            cl_str = str(cl)
                            if is_allowed_url(source, cl_str) and (
                                "/article/" in cl_str or "/courseDetail/" in cl_str
                            ):
                                all_links.add(cl_str)
                    self.sleeper(self.request_delay_ms / 1000 / 2)
                except (ZiniaoApiError, OSError):
                    continue

            # 第三层：排序并限制数量
            safe_links = sorted(
                all_links,
                key=lambda item: (
                    0 if "/article/" in item or "/courseDetail/" in item else 1,
                    item,
                ),
            )[: min(self.max_pages, source.max_pages)]
            discovered += len(safe_links)
            for index, url in enumerate(safe_links):
                try:
                    payload = self.client.page_content(
                        source_store_id,
                        url=url,
                        content_format="structured",
                    )
                    extracted = extract_structured_page(payload)
                    stored = self.repository.store_extracted(
                        source.key, extracted
                    )
                    fetched += 1
                    if stored.change == "new":
                        new += 1
                    elif stored.change == "updated":
                        updated += 1
                    else:
                        unchanged += 1
                except (
                    ExtractionError,
                    ZiniaoApiError,
                    ValueError,
                ) as error:
                    failed += 1
                    errors.append(type(error).__name__)
                    continue
                if index + 1 < len(safe_links):
                    self.sleeper(self.request_delay_ms / 1000)
        return CollectionSummary(
            discovered=discovered,
            fetched=fetched,
            new=new,
            updated=updated,
            unchanged=unchanged,
            failed=failed,
            errors=tuple(errors[:10]),
            status="partial" if failed else "success",
        )
