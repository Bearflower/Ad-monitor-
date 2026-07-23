from datetime import date

from adwatch.collectors.mock import MockCollector
from adwatch.domain import Platform


def test_mock_collectors_are_deterministic():
    first = MockCollector(Platform.TIKTOK).collect(date(2026, 7, 22))
    second = MockCollector(Platform.TIKTOK).collect(date(2026, 7, 22))
    assert first == second
    assert first
    assert all(item.platform is Platform.TIKTOK for item in first)
