import pytest

from adwatch.collectors.ziniao_client import ZiniaoApiError, ZiniaoClient
from adwatch.config import Settings


class FakeTransport:
    def __init__(self, response):
        self.response = response
        self.requests = []

    def post(self, endpoint, payload, timeout):
        self.requests.append((endpoint, payload, timeout))
        return self.response


def test_get_browser_list_sends_credentials_and_request_id(tmp_path):
    transport = FakeTransport({"statusCode": 0, "browserList": [{"id": "1"}]})
    settings = Settings(
        data_dir=tmp_path,
        ziniao_company="company",
        ziniao_username="robot",
        ziniao_password="secret",
        ziniao_endpoint="http://127.0.0.1:1886",
    )
    result = ZiniaoClient(settings, transport=transport).get_browser_list()
    payload = transport.requests[0][1]
    assert result == [{"id": "1"}]
    assert payload["action"] == "getBrowserList"
    assert "secret" in payload["userInfo"]
    assert payload["requestId"]
    assert transport.requests[0][2] == 120


def test_api_prefers_returned_error_message(tmp_path):
    settings = Settings(
        data_dir=tmp_path,
        ziniao_company="c",
        ziniao_username="u",
        ziniao_password="p",
        ziniao_endpoint="http://127.0.0.1:1886",
    )
    with pytest.raises(ZiniaoApiError, match="权限未开通"):
        ZiniaoClient(
            settings,
            transport=FakeTransport(
                {"statusCode": -1, "err": "权限未开通"}
            ),
        ).get_browser_list()
