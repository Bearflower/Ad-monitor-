import json
import subprocess
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path

import pytest

from adwatch.config import Settings
from adwatch.optimizer.models import CampaignSnapshot, CampaignStatus
from adwatch.optimizer.ziniao import (
    BridgeUnavailableError,
    PageTimeoutError,
    ShopeeLoginExpiredError,
    ShopeeSession,
    StoreNameMismatchError,
    StoreNotFoundError,
    StorePlatformMismatchError,
    ZiniaoAuthenticationError,
    ZiniaoCliClient,
    ZiniaoCliError,
    parse_shopee_ads_page,
)

FIXTURE_PATH = Path(__file__).with_name("fixtures") / "shopee_ads_page.json"
SYNTHETIC_CONTRACT_PATH = (
    Path(__file__).with_name("fixtures") / "shopee_ads_contract_synthetic.json"
)
EMPTY_SYNTHETIC_CONTRACT_PATH = (
    Path(__file__).with_name("fixtures") / "shopee_ads_contract_empty_synthetic.json"
)
MISSING_STATUS_SYNTHETIC_CONTRACT_PATH = (
    Path(__file__).with_name("fixtures")
    / "shopee_ads_contract_missing_status_synthetic.json"
)


class CommandResult:
    def __init__(self, stdout="", stderr="", returncode=0):
        self.stdout = stdout
        self.stderr = stderr
        self.returncode = returncode


class FakeRunner:
    def __init__(self, responses):
        self.responses = iter(responses)
        self.calls = []

    def __call__(self, command, **kwargs):
        self.calls.append((command, kwargs))
        response = next(self.responses)
        if isinstance(response, BaseException):
            raise response
        return response


def _ok(data):
    return CommandResult(json.dumps({"ok": True, "data": data}))


def _store(*, running=True, platform="Shopee-泰国-本土", name="虾皮泰国"):
    return {
        "storeId": "store-1",
        "storeName": name,
        "platformName": platform,
        "running": running,
    }


def _store_unknown():
    value = _store()
    value.pop("running")
    return value


def _settings(tmp_path):
    return Settings(
        data_dir=tmp_path,
        ziniao_shopee_store_id="store-1",
        ziniao_shopee_store_name="虾皮泰国",
        bridge_retries=3,
        bridge_retry_delay_seconds=2,
    )


def _page_result(value):
    return _ok({"data": {"result": json.dumps(value)}})


def test_session_reuses_a_running_configured_shopee_store(tmp_path):
    runner = FakeRunner(
        [
            _ok({"items": [_store()]}),
            _page_result(
                {
                    "url": "https://seller.shopee.co.th/portal/marketing/pas/index",
                    "text": "Shopee Ads",
                }
            ),
        ]
    )

    ready = ShopeeSession(
        _settings(tmp_path), ZiniaoCliClient(runner=runner)
    ).ensure_ready()

    assert ready.store_id == "store-1"
    assert ready.store_name == "虾皮泰国"
    assert ready.reused is True
    assert ready.observed_at.tzinfo is UTC
    assert [call[0][1:3] for call in runner.calls] == [
        ["store", "list"],
        ["page", "exec"],
    ]


def test_session_opens_closed_store_then_retries_session_probe(tmp_path):
    runner = FakeRunner(
        [
            _ok({"items": [_store(running=False)]}),
            _ok({"storeId": "store-1"}),
            _page_result({}),
            _page_result(
                {
                    "url": "https://seller.shopee.co.th/portal/marketing/pas/index",
                    "text": "Shopee",
                }
            ),
        ]
    )
    sleeps = []

    ready = ShopeeSession(
        _settings(tmp_path), ZiniaoCliClient(runner=runner), sleeper=sleeps.append
    ).ensure_ready()

    assert ready.reused is False
    assert sleeps == [2]
    assert runner.calls[1][0] == [
        "ziniao-cli",
        "store",
        "open",
        "--id",
        "store-1",
        "--url",
        "https://seller.shopee.co.th/portal/marketing/pas/index",
    ]
    assert [call[0][1:3] for call in runner.calls[2:]] == [
        ["page", "exec"],
        ["page", "exec"],
    ]


def test_session_opens_unknown_store_and_retries_when_startup_list_is_temporarily_missing(
    tmp_path,
):
    runner = FakeRunner(
        [
            _ok({"items": [_store_unknown()]}),
            _ok({"storeId": "store-1"}),
            _page_result(
                {
                    "url": "https://seller.shopee.co.th/portal/marketing/pas/index",
                    "text": "Shopee",
                }
            ),
        ]
    )
    sleeps = []

    ready = ShopeeSession(
        _settings(tmp_path), ZiniaoCliClient(runner=runner), sleeper=sleeps.append
    ).ensure_ready()

    assert ready.reused is False
    assert sleeps == []
    assert "--all" in runner.calls[0][0]
    assert runner.calls[1][0][1:3] == ["store", "open"]


def test_session_retries_initially_missing_store_list_before_reusing_running_store(
    tmp_path,
):
    runner = FakeRunner(
        [
            _ok({"items": []}),
            _ok({"items": [_store(running=True)]}),
            _page_result(
                {
                    "url": "https://seller.shopee.co.th/portal/marketing/pas/index",
                    "text": "Shopee",
                }
            ),
        ]
    )
    sleeps = []

    ready = ShopeeSession(
        _settings(tmp_path), ZiniaoCliClient(runner=runner), sleeper=sleeps.append
    ).ensure_ready()

    assert ready.reused is True
    assert sleeps == [2]
    assert [call[0][1:3] for call in runner.calls] == [
        ["store", "list"],
        ["store", "list"],
        ["page", "exec"],
    ]


def test_session_reports_page_timeout_when_started_store_never_becomes_ready(tmp_path):
    runner = FakeRunner(
        [
            _ok({"items": [_store(running=False)]}),
            _ok({"storeId": "store-1"}),
            _page_result({}),
            _page_result({}),
            _page_result({}),
        ]
    )

    with pytest.raises(PageTimeoutError, match="重试"):
        ShopeeSession(
            _settings(tmp_path), ZiniaoCliClient(runner=runner), sleeper=lambda _: None
        ).ensure_ready()


def test_session_retries_recoverable_page_timeout_until_target_page_is_ready(tmp_path):
    runner = FakeRunner(
        [
            _ok({"items": [_store_unknown()]}),
            _ok({"storeId": "store-1"}),
            subprocess.TimeoutExpired(["ziniao-cli", "page", "exec"], 1),
            _page_result(
                {"url": "https://seller.shopee.co.th/portal/marketing/pas/index"}
            ),
        ]
    )
    sleeps = []

    ready = ShopeeSession(
        _settings(tmp_path), ZiniaoCliClient(runner=runner), sleeper=sleeps.append
    ).ensure_ready()

    assert ready.reused is False
    assert sleeps == [2]


def test_session_rejects_blank_or_unrelated_page_until_target_seller_domain_is_ready(
    tmp_path,
):
    runner = FakeRunner(
        [
            _ok({"items": [_store_unknown()]}),
            _ok({"storeId": "store-1"}),
            _page_result({"url": "about:blank", "title": "Shopee Seller Centre"}),
            _page_result(
                {"url": "https://example.com/", "title": "Shopee Seller Centre"}
            ),
            _page_result(
                {"url": "https://seller.shopee.co.th/portal/marketing/pas/index"}
            ),
        ]
    )
    sleeps = []

    ready = ShopeeSession(
        _settings(tmp_path), ZiniaoCliClient(runner=runner), sleeper=sleeps.append
    ).ensure_ready()

    assert ready.reused is False
    assert sleeps == [2, 2]


def test_session_retries_root_and_account_pages_until_ads_path_is_ready(tmp_path):
    runner = FakeRunner(
        [
            _ok({"items": [_store_unknown()]}),
            _ok({"storeId": "store-1"}),
            _page_result({"url": "https://seller.shopee.co.th/"}),
            _page_result({"url": "https://seller.shopee.co.th/account/profile"}),
            _page_result(
                {"url": "https://seller.shopee.co.th/portal/marketing/pas/index"}
            ),
        ]
    )
    sleeps = []

    ShopeeSession(
        _settings(tmp_path), ZiniaoCliClient(runner=runner), sleeper=sleeps.append
    ).ensure_ready()

    assert sleeps == [2, 2]


def test_session_accepts_ads_subpaths_after_required_prefix(tmp_path):
    runner = FakeRunner(
        [
            _ok({"items": [_store_unknown()]}),
            _ok({"storeId": "store-1"}),
            _page_result(
                {
                    "url": "https://seller.shopee.co.th/portal/marketing/pas/index/details"
                }
            ),
        ]
    )

    ShopeeSession(_settings(tmp_path), ZiniaoCliClient(runner=runner)).ensure_ready()


def test_session_reports_bridge_timeout_without_leaking_command_details(tmp_path):
    runner = FakeRunner(
        [
            subprocess.TimeoutExpired(["ziniao-cli", "store", "list"], 1),
            subprocess.TimeoutExpired(["ziniao-cli", "store", "list"], 1),
            subprocess.TimeoutExpired(["ziniao-cli", "store", "list"], 1),
        ]
    )

    with pytest.raises(BridgeUnavailableError, match="Bridge"):
        ShopeeSession(
            _settings(tmp_path), ZiniaoCliClient(runner=runner)
        ).ensure_ready()


def test_session_rejects_missing_or_non_shopee_configured_store(tmp_path):
    with pytest.raises(StoreNotFoundError, match="not found"):
        ShopeeSession(
            _settings(tmp_path),
            ZiniaoCliClient(runner=FakeRunner([_ok({"items": []})] * 3)),
            sleeper=lambda _: None,
        ).ensure_ready()

    with pytest.raises(StorePlatformMismatchError, match="Shopee"):
        ShopeeSession(
            _settings(tmp_path),
            ZiniaoCliClient(
                runner=FakeRunner(
                    [_ok({"items": [_store(platform="TikTok Shop-泰国-本土")]})]
                )
            ),
        ).ensure_ready()


def test_session_rejects_unexpected_store_name(tmp_path):
    with pytest.raises(StoreNameMismatchError, match="name"):
        ShopeeSession(
            _settings(tmp_path),
            ZiniaoCliClient(
                runner=FakeRunner([_ok({"items": [_store(name="别的店")]})])
            ),
        ).ensure_ready()


def test_session_reports_expired_shopee_login(tmp_path):
    runner = FakeRunner(
        [
            _ok({"items": [_store()]}),
            _page_result(
                {
                    "url": "https://seller.shopee.co.th/portal/marketing/pas/index",
                    "text": "Log In",
                }
            ),
        ]
    )

    with pytest.raises(ShopeeLoginExpiredError, match="登录"):
        ShopeeSession(
            _settings(tmp_path), ZiniaoCliClient(runner=runner)
        ).ensure_ready()


def test_client_uses_only_whitelisted_argument_list_commands_and_never_shells():
    runner = FakeRunner(
        [
            _ok({"items": []}),
            _ok({"storeId": "store-1"}),
            _page_result({"ok": True}),
        ]
    )
    client = ZiniaoCliClient(runner=runner)

    client.store_list()
    client.store_open("store-1")
    client.page_exec("store-1", "session_status")

    assert [call[0][1:3] for call in runner.calls] == [
        ["store", "list"],
        ["store", "open"],
        ["page", "exec"],
    ]
    assert all(isinstance(call[0], list) for call in runner.calls)
    assert all(call[1]["shell"] is False for call in runner.calls)
    assert all(call[1]["timeout"] > 0 for call in runner.calls)
    assert runner.calls[0][0] == [
        "ziniao-cli",
        "store",
        "list",
        "--all",
        "--format",
        "json",
    ]


def test_client_uses_timeout_from_injected_settings(tmp_path):
    settings = Settings(data_dir=tmp_path, ziniao_cli_timeout_seconds=17)
    runner = FakeRunner([_ok({"items": []})])

    ZiniaoCliClient(settings=settings, runner=runner).store_list()

    assert runner.calls[0][1]["timeout"] == 17


@pytest.mark.parametrize(
    "script",
    [
        "document.querySelector('button').click()",
        "fetch('/campaign', {method: 'POST'})",
        "document.forms[0].requestSubmit()",
        "element['click']()",
    ],
)
def test_client_rejects_any_unapproved_page_script_before_calling_bridge(script):
    runner = FakeRunner([])

    with pytest.raises(ValueError, match="approved"):
        ZiniaoCliClient(runner=runner).page_exec("store-1", script)

    assert runner.calls == []


def test_client_rejects_invalid_json_and_reports_cli_errors_without_sensitive_output():
    with pytest.raises(ZiniaoCliError, match="invalid JSON") as error:
        ZiniaoCliClient(runner=FakeRunner([CommandResult("not-json")])).store_list()
    assert error.value.__cause__ is None

    with pytest.raises(ZiniaoCliError, match="request failed"):
        ZiniaoCliClient(
            runner=FakeRunner(
                [CommandResult('{"ok":false,"error":{"message":"api_key=secret"}}')]
            )
        ).store_list()


@pytest.mark.parametrize(
    ("error_type", "code", "exception"),
    [
        ("network", "bridge_unavailable", BridgeUnavailableError),
        ("timeout", "page_timeout", PageTimeoutError),
        ("auth", "login_required", ZiniaoAuthenticationError),
    ],
)
def test_client_maps_only_structured_error_type_and_code_without_message(
    error_type, code, exception
):
    response = CommandResult(
        json.dumps(
            {
                "ok": False,
                "error": {
                    "type": error_type,
                    "code": code,
                    "message": "secret=should-not-leak",
                },
            }
        )
    )

    with pytest.raises(exception) as error:
        ZiniaoCliClient(runner=FakeRunner([response])).page_exec(
            "store-1", "session_status"
        )

    assert "secret" not in str(error.value)
    assert error.value.__cause__ is None


def test_client_maps_structured_error_from_nonzero_cli_stderr_without_message_leak():
    response = CommandResult(
        stdout="secret=stdout-must-not-leak",
        stderr=json.dumps(
            {
                "ok": False,
                "error": {
                    "type": "network",
                    "code": "bridge_unavailable",
                    "message": "secret=should-not-leak",
                },
            }
        ),
        returncode=1,
    )

    with pytest.raises(BridgeUnavailableError) as error:
        ZiniaoCliClient(runner=FakeRunner([response])).store_list()

    assert "secret" not in str(error.value)
    assert error.value.__cause__ is None


def test_client_maps_nonzero_page_timeout_and_generic_auth_by_operation():
    page_timeout = CommandResult(
        stderr=json.dumps(
            {"ok": False, "error": {"type": "timeout", "code": "timeout"}}
        ),
        returncode=1,
    )
    auth = CommandResult(
        stderr=json.dumps(
            {"ok": False, "error": {"type": "auth", "code": "login_required"}}
        ),
        returncode=1,
    )

    with pytest.raises(PageTimeoutError):
        ZiniaoCliClient(runner=FakeRunner([page_timeout])).visit_shopee_ads(
            "store-1", date(2026, 9, 9)
        )
    with pytest.raises(ZiniaoAuthenticationError):
        ZiniaoCliClient(runner=FakeRunner([auth])).page_exec(
            "store-1", "session_status"
        )


@pytest.mark.parametrize(
    ("operation", "exception"),
    [("store_list", BridgeUnavailableError), ("store_open", BridgeUnavailableError)],
)
def test_client_maps_page_timeout_to_bridge_error_for_store_operations(
    operation, exception
):
    response = CommandResult(
        json.dumps(
            {
                "ok": False,
                "error": {"type": "timeout", "code": "page_timeout"},
            }
        )
    )
    client = ZiniaoCliClient(runner=FakeRunner([response]))

    with pytest.raises(exception):
        client.store_list() if operation == "store_list" else client.store_open(
            "store-1"
        )


@pytest.mark.parametrize("operation", ["store_list", "page_exec"])
def test_client_hides_subprocess_os_error_details(operation):
    error = FileNotFoundError("ziniao-cli: no such file or directory")
    client = ZiniaoCliClient(runner=FakeRunner([error]))

    with pytest.raises(BridgeUnavailableError) as raised:
        client.store_list() if operation == "store_list" else client.page_exec(
            "store-1", "session_status"
        )

    assert raised.value.__cause__ is None
    assert "ziniao-cli" not in str(raised.value)
    assert "no such file" not in str(raised.value)


def test_session_preserves_last_bridge_error_after_session_probe_retries(tmp_path):
    bridge_timeout = CommandResult(
        json.dumps(
            {
                "ok": False,
                "error": {"type": "network", "code": "bridge_unavailable"},
            }
        )
    )
    runner = FakeRunner(
        [
            _ok({"items": [_store_unknown()]}),
            _ok({"storeId": "store-1"}),
            bridge_timeout,
            bridge_timeout,
            bridge_timeout,
        ]
    )

    with pytest.raises(BridgeUnavailableError):
        ShopeeSession(
            _settings(tmp_path), ZiniaoCliClient(runner=runner), sleeper=lambda _: None
        ).ensure_ready()


@pytest.mark.parametrize(
    ("operation", "error_type", "error_code", "exception"),
    [
        ("store_list", "network", "network", BridgeUnavailableError),
        ("store_open", "timeout", "timeout", BridgeUnavailableError),
        ("page_exec", "network", "network", BridgeUnavailableError),
        ("page_visit", "timeout", "timeout", PageTimeoutError),
    ],
)
def test_client_classifies_network_and_timeout_by_operation(
    operation, error_type, error_code, exception
):
    response = CommandResult(
        json.dumps(
            {
                "ok": False,
                "error": {"type": error_type, "code": error_code},
            }
        )
    )
    client = ZiniaoCliClient(runner=FakeRunner([response]))

    with pytest.raises(exception):
        if operation == "store_list":
            client.store_list()
        elif operation == "store_open":
            client.store_open("store-1")
        elif operation == "page_exec":
            client.page_exec("store-1", "session_status")
        else:
            client.visit_shopee_ads("store-1", date(2026, 9, 9))


def test_client_hides_timeout_cause_and_store_lookup_hides_configured_identifier(
    tmp_path,
):
    timeout = subprocess.TimeoutExpired(["ziniao-cli", "store", "list", "store-1"], 1)
    with pytest.raises(BridgeUnavailableError) as error:
        ZiniaoCliClient(runner=FakeRunner([timeout])).store_list()

    assert error.value.__cause__ is None
    assert "store-1" not in str(error.value)

    with pytest.raises(StoreNotFoundError) as error:
        ShopeeSession(
            _settings(tmp_path),
            ZiniaoCliClient(runner=FakeRunner([_ok({"items": []})] * 3)),
            sleeper=lambda _: None,
        ).ensure_ready()

    assert "store-1" not in str(error.value)


def test_fixed_ads_navigation_rejects_page_timeout_and_keeps_date_filter_in_url():
    runner = FakeRunner([subprocess.TimeoutExpired(["ziniao-cli", "page", "visit"], 1)])

    with pytest.raises(PageTimeoutError, match="页面"):
        ZiniaoCliClient(runner=runner).visit_shopee_ads("store-1", date(2026, 9, 9))

    assert "seller.shopee.co.th/portal/marketing/pas/index" in " ".join(
        runner.calls[0][0]
    )
    assert "from=1788886800" in " ".join(runner.calls[0][0])


def test_page_visit_accepts_cli_plain_text_success_without_parsing_content():
    runner = FakeRunner([CommandResult("✓ 页面已导航")])

    url = ZiniaoCliClient(runner=runner).visit_shopee_ads(
        "store-1", date(2026, 9, 9)
    )

    assert url.startswith("https://seller.shopee.co.th/")
    assert runner.calls[0][0][1:3] == ["page", "visit"]
    assert runner.calls[0][0][-2:] == ["--wait-until", "networkidle"]


def test_real_redacted_fixture_locks_missing_field_and_identifier_evidence():
    fixture = json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))

    page = parse_shopee_ads_page(fixture, date(2026, 9, 9))

    assert page.campaigns == ()
    assert page.missing_fields == tuple(fixture["missing_fields"])
    assert page.observed_at.tzinfo is UTC
    assert "campaign_id" in page.missing_fields
    assert "display name" in page.stable_id_evidence["observed"]


def test_parser_uses_product_stable_fallback_not_display_name_when_page_has_no_campaign_id():
    fixture = json.loads(SYNTHETIC_CONTRACT_PATH.read_text(encoding="utf-8"))
    fixture["campaign_rows"] = [
        {
            "name": "随时可改的展示名",
            "type": "product_ads",
            "status": "Ongoing",
            "current_budget": "฿100.00",
            "target_roas": "3.2",
            "item_id": "123456789",
            "currency": "THB",
        }
    ]
    fixture["missing_fields"] = []

    page = parse_shopee_ads_page(
        fixture, data_date=date(2026, 9, 9), store_id="store-1"
    )

    assert page.campaigns[0].campaign_id == "store-1:product_ads:123456789"
    assert page.campaigns[0].current_budget == Decimal("100.00")
    assert page.campaigns[0].target_roas == Decimal("3.2")
    assert page.campaigns[0].status is CampaignStatus.ACTIVE


@pytest.mark.parametrize("name", ["", "   ", "...", "…", "—", "-"])
def test_parser_marks_placeholder_campaign_names_as_missing(name):
    fixture = json.loads(SYNTHETIC_CONTRACT_PATH.read_text(encoding="utf-8"))
    fixture["campaign_rows"][0]["campaign_name"] = name
    fixture["campaign_rows"][0]["name"] = name
    fixture["missing_fields"] = []

    page = parse_shopee_ads_page(
        fixture, data_date=date(2026, 9, 9), store_id="store-1"
    )

    assert page.campaigns[0].campaign_name == ""
    assert "campaign_name" in page.missing_fields


def test_parser_preserves_a_real_campaign_name():
    fixture = json.loads(SYNTHETIC_CONTRACT_PATH.read_text(encoding="utf-8"))
    fixture["campaign_rows"][0]["campaign_name"] = "แคมเปญจริง"
    fixture["campaign_rows"][0]["name"] = "แคมเปญจริง"
    fixture["missing_fields"] = []

    page = parse_shopee_ads_page(
        fixture, data_date=date(2026, 9, 9), store_id="store-1"
    )

    assert page.campaigns[0].campaign_name == "แคมเปญจริง"
    assert "campaign_name" not in page.missing_fields


def test_parser_treats_python_none_page_ids_as_missing_and_uses_item_fallback():
    contract = json.loads(SYNTHETIC_CONTRACT_PATH.read_text(encoding="utf-8"))
    contract["campaign_rows"] = [
        {
            "name": "页面空值",
            "type": "product_ads",
            "status": "Ongoing",
            "page_campaign_id": None,
            "detail_href": None,
            "item_id": "item-real",
        }
    ]

    page = parse_shopee_ads_page(contract, date(2026, 9, 9), store_id="store-1")

    assert page.campaigns[0].campaign_id == "store-1:product_ads:item-real"


def test_parser_treats_string_none_as_regular_text_not_python_missing():
    contract = json.loads(SYNTHETIC_CONTRACT_PATH.read_text(encoding="utf-8"))
    contract["campaign_rows"] = [
        {
            "name": "页面文本",
            "type": "product_ads",
            "status": "Ongoing",
            "page_campaign_id": "None",
            "detail_href": None,
            "item_id": "item-real",
        }
    ]

    page = parse_shopee_ads_page(contract, date(2026, 9, 9), store_id="store-1")

    assert page.campaigns[0].campaign_id == "None"


def test_cli_page_exec_result_adapts_to_dto_and_campaign_snapshot():
    contract = json.loads(SYNTHETIC_CONTRACT_PATH.read_text(encoding="utf-8"))
    result = ZiniaoCliClient(runner=FakeRunner([_page_result(contract)])).page_exec(
        "store-1", "shopee_ads_contract"
    )

    page = parse_shopee_ads_page(result, date(2026, 9, 9), store_id="store-1")

    assert page.campaigns == (
        CampaignSnapshot(
            "store-1",
            "page-campaign-1",
            "可变展示名",
            "product_ads",
            CampaignStatus.ACTIVE,
            Decimal("100.00"),
            Decimal("3.2"),
            "THB",
            datetime(2026, 9, 9, 1, 2, 3, tzinfo=UTC),
            date(2026, 9, 9),
        ),
    )


def test_read_only_contract_supports_shopee_split_fixed_column_tables():
    script = ZiniaoCliClient._READ_ONLY_SCRIPTS["shopee_ads_contract"]

    assert "infoRows" in script
    assert "metricRows" in script
    assert ".metric-item" in script
    assert "detailId" in script
    assert "fetch(" not in script
    assert ".click(" not in script


@pytest.mark.parametrize(
    ("row", "expected_id"),
    [
        (
            {
                "page_campaign_id": "page-id",
                "detail_href": "/campaign/linked",
                "item_id": "item",
            },
            "page-id",
        ),
        (
            {"detail_href": "https://seller/campaign/detail-id", "item_id": "item"},
            "detail-id",
        ),
        ({"item_id": "item"}, "store-1:product_ads:item"),
    ],
)
def test_parser_stable_id_priority_and_missing_controls(row, expected_id):
    contract = json.loads(SYNTHETIC_CONTRACT_PATH.read_text(encoding="utf-8"))
    contract["campaign_rows"] = [
        {
            "name": "展示名",
            "type": "product_ads",
            "status": "Ongoing",
            "currency": "THB",
            **row,
        }
    ]

    page = parse_shopee_ads_page(contract, date(2026, 9, 9), store_id="store-1")

    assert page.campaigns[0].campaign_id == expected_id
    assert page.campaigns[0].current_budget is None
    assert page.campaigns[0].target_roas is None


def test_parser_rejects_display_name_only_campaign():
    contract = json.loads(SYNTHETIC_CONTRACT_PATH.read_text(encoding="utf-8"))
    contract["campaign_rows"] = [
        {
            "name": "只有展示名",
            "type": "product_ads",
            "status": "Ongoing",
            "current_budget": "100",
            "target_roas": "2",
            "currency": "THB",
        }
    ]

    assert parse_shopee_ads_page(contract, date(2026, 9, 9)).campaigns == ()


def test_empty_runtime_contract_has_explicit_missing_fields():
    contract = json.loads(EMPTY_SYNTHETIC_CONTRACT_PATH.read_text(encoding="utf-8"))

    page = parse_shopee_ads_page(contract, date(2026, 9, 9))

    assert page.campaigns == ()
    assert "status" in page.missing_fields
    assert "campaign_rows" in page.missing_fields


def test_parser_keeps_campaign_with_unknown_status_and_marks_missing_field():
    contract = json.loads(
        MISSING_STATUS_SYNTHETIC_CONTRACT_PATH.read_text(encoding="utf-8")
    )

    page = parse_shopee_ads_page(contract, date(2026, 9, 9))

    assert page.campaigns[0].status is CampaignStatus.UNKNOWN
    assert page.campaigns[0].campaign_id == "page-campaign-no-status"
    assert "status" in page.missing_fields


def test_parser_rejects_ambiguous_fallback_campaign_identity_in_entire_batch():
    contract = json.loads(SYNTHETIC_CONTRACT_PATH.read_text(encoding="utf-8"))
    contract["campaign_rows"] = [
        {
            "name": "甲",
            "type": "product_ads",
            "status": "Ongoing",
            "item_id": "same",
            "currency": "THB",
        },
        {
            "name": "乙",
            "type": "product_ads",
            "status": "Paused",
            "item_id": "same",
            "currency": "THB",
        },
    ]

    page = parse_shopee_ads_page(contract, date(2026, 9, 9))

    assert page.campaigns == ()
    assert "ambiguous_campaign_identity" in page.missing_fields
    assert "ambiguous_campaign_identity" in page.diagnostics


@pytest.mark.parametrize(
    "rows",
    [
        [
            {
                "name": "甲",
                "type": "product_ads",
                "status": "Ongoing",
                "page_campaign_id": "same",
                "item_id": "item-1",
            },
            {
                "name": "乙",
                "type": "product_ads",
                "status": "Paused",
                "page_campaign_id": "same",
                "item_id": "item-2",
            },
        ],
        [
            {
                "name": "甲",
                "type": "product_ads",
                "status": "Ongoing",
                "detail_href": "https://seller/campaign/shared",
                "item_id": "item-1",
            },
            {
                "name": "乙",
                "type": "product_ads",
                "status": "Paused",
                "detail_href": "https://seller/campaign/shared",
                "item_id": "item-2",
            },
        ],
        [
            {
                "name": "甲",
                "type": "product_ads",
                "status": "Ongoing",
                "page_campaign_id": "store-1:product_ads:item-1",
                "item_id": "item-2",
            },
            {
                "name": "乙",
                "type": "product_ads",
                "status": "Paused",
                "item_id": "item-1",
            },
        ],
    ],
)
def test_parser_rejects_all_rows_sharing_any_final_stable_campaign_id(rows):
    contract = json.loads(SYNTHETIC_CONTRACT_PATH.read_text(encoding="utf-8"))
    contract["campaign_rows"] = rows

    page = parse_shopee_ads_page(contract, date(2026, 9, 9))

    assert page.campaigns == ()
    assert "ambiguous_campaign_identity" in page.missing_fields
    assert "ambiguous_campaign_identity" in page.diagnostics


def test_parser_marks_malformed_campaign_rows_instead_of_treating_them_as_empty():
    contract = json.loads(SYNTHETIC_CONTRACT_PATH.read_text(encoding="utf-8"))
    contract["campaign_rows"] = {"not": "a-list"}

    page = parse_shopee_ads_page(contract, date(2026, 9, 9))

    assert "malformed_campaign_rows" in page.missing_fields
    assert "malformed_campaign_rows" in page.diagnostics


def test_fixture_kinds_distinguish_real_redaction_from_synthetic_contracts():
    real = json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))
    synthetic = json.loads(SYNTHETIC_CONTRACT_PATH.read_text(encoding="utf-8"))
    assert real["fixture_kind"] == "real_observation"
    assert real["source"] == "ziniao-cli/page-exec/read-only"
    assert synthetic["fixture_kind"] == "synthetic_contract"
    assert synthetic["source"] == "test-fixture"


def test_parser_keeps_valid_rows_when_batch_also_contains_malformed_rows():
    contract = json.loads(SYNTHETIC_CONTRACT_PATH.read_text(encoding="utf-8"))
    contract["campaign_rows"] = [
        contract["campaign_rows"][0],
        "not-a-row",
    ]

    page = parse_shopee_ads_page(contract, date(2026, 9, 9))

    assert len(page.campaigns) == 1
    assert "malformed_campaign_rows" in page.diagnostics
    assert "malformed_campaign_rows" in page.missing_fields


@pytest.mark.parametrize(
    "value", ["abc123", "1,23.45", "(2)", "12%", "฿฿1.00", "฿1 THB", "1.2.3"]
)
def test_parser_does_not_extract_substrings_from_control_decimal_fields(value: str):
    contract = json.loads(SYNTHETIC_CONTRACT_PATH.read_text(encoding="utf-8"))
    contract["campaign_rows"] = [
        {
            "name": "活动",
            "type": "product_ads",
            "status": "Ongoing",
            "page_campaign_id": "campaign-1",
            "item_id": "item-1",
            "currency": "THB",
            "current_budget": value,
            "target_roas": value,
        }
    ]

    page = parse_shopee_ads_page(contract, date(2026, 9, 9), store_id="store-1")

    assert page.campaigns[0].current_budget is None
    assert page.campaigns[0].target_roas is None


def test_parser_uses_injected_default_currency_when_row_currency_is_missing():
    contract = json.loads(SYNTHETIC_CONTRACT_PATH.read_text(encoding="utf-8"))
    contract["campaign_rows"] = [
        {
            "name": "活动",
            "type": "product_ads",
            "status": "Ongoing",
            "page_campaign_id": "campaign-1",
            "item_id": "item-1",
        }
    ]

    page = parse_shopee_ads_page(
        contract, date(2026, 9, 9), store_id="store-1", default_currency="USD"
    )

    assert page.campaigns[0].currency == "USD"
