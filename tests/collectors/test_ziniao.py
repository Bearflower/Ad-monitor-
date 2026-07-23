from datetime import date

import pytest

from adwatch.collectors.ziniao import ZiniaoCollector, ZiniaoNotConfigured
from adwatch.config import Settings
from adwatch.domain import Platform


def test_ziniao_collector_fails_explicitly_when_unconfigured(tmp_path):
    settings = Settings(data_dir=tmp_path)
    with pytest.raises(ZiniaoNotConfigured):
        ZiniaoCollector(settings, Platform.SHOPEE).collect(date(2026, 7, 22))
