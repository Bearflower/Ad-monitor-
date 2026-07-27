from pathlib import Path

import pytest

from adwatch.collectors.ziniao_client import (
    ZiniaoApiError,
    ZiniaoCliClient,
    ZiniaoClient,
)
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


class FakeCommandRunner:
    def __init__(self, responses):
        self.responses = iter(responses)
        self.commands = []

    def __call__(self, command, **kwargs):
        self.commands.append((command, kwargs))
        return next(self.responses)


class CommandResult:
    def __init__(self, stdout="", stderr="", returncode=0):
        self.stdout = stdout
        self.stderr = stderr
        self.returncode = returncode


def test_cli_store_list_uses_official_cli_and_parses_stores():
    runner = FakeCommandRunner(
        [CommandResult('{"ok":true,"data":[{"storeId":"123","name":"店铺"}]}')]
    )

    stores = ZiniaoCliClient(runner=runner).get_store_list()

    assert stores == [{"storeId": "123", "name": "店铺"}]
    assert runner.commands[0][0] == [
        "ziniao-cli",
        "store",
        "list",
        "--format",
        "json",
    ]
    assert runner.commands[0][1]["timeout"] == 120


def test_cli_page_exec_unwraps_bridge_result():
    runner = FakeCommandRunner(
        [
            CommandResult(
                '{"ok":true,"data":{"data":{"result":"[{\\"spend\\":37}]"}}}'
            )
        ]
    )

    result = ZiniaoCliClient(runner=runner).page_exec("456", "JSON.stringify([])")

    assert result == [{"spend": 37}]
    assert runner.commands[0][0][:5] == [
        "ziniao-cli",
        "page",
        "exec",
        "--store-id",
        "456",
    ]


def test_cli_failure_uses_stderr_message():
    runner = FakeCommandRunner(
        [CommandResult(stderr="Bridge unavailable", returncode=1)]
    )

    with pytest.raises(ZiniaoApiError, match="Bridge unavailable"):
        ZiniaoCliClient(runner=runner).get_store_list()


def test_cli_navigates_and_waits_for_expected_url_before_extracting():
    runner = FakeCommandRunner(
        [
            CommandResult('{"ok":true,"data":{"data":{"result":"navigating"}}}'),
            CommandResult(
                '{"ok":true,"data":{"data":{"result":"https://seller.shopee.co.th/"}}}'
            ),
            CommandResult(
                '{"ok":true,"data":{"data":{"result":"https://seller.shopee.co.th/portal/marketing/pas/index?from=1&to=2"}}}'
            ),
            CommandResult(
                '{"ok":true,"data":{"data":{"result":"[{\\"sku\\":\\"1\\"}]"}}}'
            ),
        ]
    )
    sleeps = []
    client = ZiniaoCliClient(runner=runner, sleeper=sleeps.append)

    result = client.navigate_and_exec(
        "456",
        "https://seller.shopee.co.th/portal/marketing/pas/index?from=1&to=2",
        "JSON.stringify([{sku:'1'}])",
        expected_url="from=1&to=2",
    )

    assert result == [{"sku": "1"}]
    assert sleeps == [1]


def test_cli_navigation_timeout_is_explicit():
    runner = FakeCommandRunner(
        [
            CommandResult('{"ok":true,"data":{"data":{"result":"navigating"}}}'),
            CommandResult(
                '{"ok":true,"data":{"data":{"result":"https://seller.shopee.co.th/"}}}'
            ),
        ]
    )
    client = ZiniaoCliClient(runner=runner, sleeper=lambda _: None)

    with pytest.raises(ZiniaoApiError, match="did not reach expected URL"):
        client.navigate_and_exec(
            "456",
            "https://seller.shopee.co.th/portal/marketing/pas/index?from=1",
            "JSON.stringify([])",
            expected_url="from=1",
            attempts=1,
        )


def test_cli_can_wait_for_nonempty_async_page_result():
    runner = FakeCommandRunner(
        [
            CommandResult('{"ok":true,"data":{"data":{"result":"navigating"}}}'),
            CommandResult(
                '{"ok":true,"data":{"data":{"result":"https://seller/pas?day=1"}}}'
            ),
            CommandResult('{"ok":true,"data":{"data":{"result":"[]"}}}'),
            CommandResult(
                '{"ok":true,"data":{"data":{"result":"https://seller/pas?day=1"}}}'
            ),
            CommandResult(
                '{"ok":true,"data":{"data":{"result":"[{\\"sku\\":\\"1\\"}]"}}}'
            ),
        ]
    )
    sleeps = []
    client = ZiniaoCliClient(runner=runner, sleeper=sleeps.append)

    result = client.navigate_and_exec(
        "456",
        "https://seller/pas?day=1",
        "JSON.stringify(rows)",
        expected_url="day=1",
        require_nonempty=True,
        attempts=2,
    )

    assert result == [{"sku": "1"}]
    assert sleeps == [1]


def test_cli_page_exec_until_waits_for_matching_result():
    runner = FakeCommandRunner(
        [
            CommandResult('{"ok":true,"data":{"data":{"result":"null"}}}'),
            CommandResult(
                '{"ok":true,"data":{"data":{"result":"{\\"page\\":2,\\"rows\\":[1]}"}}}'
            ),
        ]
    )
    sleeps = []
    client = ZiniaoCliClient(runner=runner, sleeper=sleeps.append)

    result = client.page_exec_until(
        "456",
        "JSON.stringify(state)",
        ready=lambda value: isinstance(value, dict) and value.get("page") == 2,
        attempts=2,
    )

    assert result == {"page": 2, "rows": [1]}
    assert sleeps == [1]


def test_cli_page_exec_until_stops_after_three_attempts_by_default():
    runner = FakeCommandRunner(
        [
            CommandResult('{"ok":true,"data":{"data":{"result":"null"}}}'),
            CommandResult('{"ok":true,"data":{"data":{"result":"null"}}}'),
            CommandResult('{"ok":true,"data":{"data":{"result":"null"}}}'),
        ]
    )
    client = ZiniaoCliClient(runner=runner, sleeper=lambda _: None)

    with pytest.raises(ZiniaoApiError, match="after 3 attempts"):
        client.page_exec_until(
            "456",
            "JSON.stringify(state)",
            ready=lambda value: value is not None,
        )

    assert len(runner.commands) == 3


def test_cli_page_actions_use_fixed_shortcuts(tmp_path):
    runner = FakeCommandRunner(
        [
            CommandResult('{"ok":true,"data":{"value":"100"}}'),
            CommandResult('{"ok":true,"data":{}}'),
            CommandResult('{"ok":true,"data":{}}'),
            CommandResult('{"ok":true,"data":{"path":"shot.png"}}'),
        ]
    )
    client = ZiniaoCliClient(runner=runner)
    screenshot = tmp_path / "shot.png"

    assert client.page_query("store", "#budget") == {"value": "100"}
    client.page_input("store", "#budget", "70", clear=True)
    client.page_click("store", "#submit")
    assert client.page_screenshot("store", screenshot) == str(
        screenshot.resolve()
    )

    commands = [call[0] for call in runner.commands]
    assert commands[0][1:3] == ["page", "query"]
    assert commands[1][1:3] == ["page", "input"]
    assert "--clear" in commands[1]
    assert commands[2][1:3] == ["page", "click"]
    assert commands[3][1:3] == ["page", "screenshot"]
    assert str(Path(screenshot).resolve()) in commands[3]
