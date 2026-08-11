from __future__ import annotations

from dataclasses import dataclass
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit


@dataclass(frozen=True)
class SourceDefinition:
    key: str
    source_type: str
    name_zh: str
    entry_url: str
    allowed_hosts: tuple[str, ...]
    allowed_path_prefixes: tuple[str, ...]
    enabled: bool = False
    extractor_version: str = "v1"
    max_pages: int = 50


TRACKING_KEYS = {
    "utm_source",
    "utm_medium",
    "utm_campaign",
    "utm_term",
    "utm_content",
    "from",
}


def canonicalize_url(url: str) -> str:
    parsed = urlsplit(url)
    query = urlencode(
        sorted(
            (key, value)
            for key, value in parse_qsl(parsed.query, keep_blank_values=True)
            if key.lower() not in TRACKING_KEYS
        )
    )
    return urlunsplit(
        (
            parsed.scheme.lower(),
            parsed.netloc.lower(),
            parsed.path.rstrip("/") or "/",
            query,
            "",
        )
    )


def is_allowed_url(source: SourceDefinition, url: str) -> bool:
    parsed = urlsplit(canonicalize_url(url))
    if parsed.scheme != "https" or parsed.hostname not in source.allowed_hosts:
        return False
    lowered = parsed.path.lower()
    blocked = ("/logout", "/settings", "/edit", "/create", "/payment")
    return not any(part in lowered for part in blocked) and any(
        parsed.path.startswith(prefix)
        for prefix in source.allowed_path_prefixes
    )


# Confirmed through live, read-only Ziniao inspection on 2026-07-29.
DEFAULT_SOURCES: tuple[SourceDefinition, ...] = (
    SourceDefinition(
        key="shopee_th_seller_education",
        source_type="seller_education",
        name_zh="Shopee 泰国卖家学习中心",
        entry_url="https://seller.shopee.co.th/edu/home",
        allowed_hosts=("seller.shopee.co.th", "help.shopee.co.th"),
        allowed_path_prefixes=("/edu", "/portal"),
        enabled=True,
        max_pages=50,
    ),
    SourceDefinition(
        key="tiktok_shop_th_help",
        source_type="help_center",
        name_zh="TikTok Shop 泰国卖家帮助中心",
        entry_url="https://partner.tiktokshop.com/help",
        allowed_hosts=("partner.tiktokshop.com",),
        allowed_path_prefixes=("/help",),
        enabled=True,
        max_pages=50,
    ),
)
