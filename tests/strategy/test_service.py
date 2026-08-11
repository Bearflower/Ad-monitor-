"""策略规则版本管理服务测试。"""

from datetime import date

import pytest

from adwatch.storage.db import Database
from adwatch.strategy.service import StrategyRuleError, StrategyRuleService


@pytest.fixture
def strategy_service(tmp_path):
    """创建带有迁移数据库的策略规则服务测试夹具。"""
    database = Database(tmp_path / "strategy_test.sqlite3")
    database.migrate()
    return StrategyRuleService(database), database


def test_list_versions_returns_default(strategy_service):
    """验证列表查询返回默认版本 default-v1。"""
    service, _ = strategy_service
    versions = service.list_versions()

    assert len(versions) >= 1
    default = versions[0]
    assert default["id"] == "default-v1"
    assert default["version"] == 1
    assert default["parameters"]["max_refund_rate"] == "0.30"
    assert default["parameters"]["min_inventory_days"] == "14"


def test_get_current_version_returns_default(strategy_service):
    """验证获取当前版本返回默认版本。"""
    service, _ = strategy_service
    current = service.get_current_version()

    assert current is not None
    assert current["id"] == "default-v1"
    assert current["version"] == 1
    assert current["effective_from"] == "2026-01-01"


def test_get_current_version_with_as_of(strategy_service):
    """验证按日期查询当前版本功能。"""
    service, _ = strategy_service

    # 查询 2026-06-01 生效的版本（默认版本从 2026-01-01 开始）
    current = service.get_current_version(as_of=date(2026, 6, 1))
    assert current is not None
    assert current["id"] == "default-v1"

    # 查询 2025-01-01（默认版本尚未生效）
    current = service.get_current_version(as_of=date(2025, 1, 1))
    assert current is None


def test_get_version_by_id(strategy_service):
    """验证按 ID 查询版本详情。"""
    service, _ = strategy_service
    version = service.get_version("default-v1")

    assert version is not None
    assert version["id"] == "default-v1"
    assert version["version"] == 1


def test_get_version_nonexistent(strategy_service):
    """验证查询不存在的版本返回 None。"""
    service, _ = strategy_service
    version = service.get_version("non-existent-id")
    assert version is None


def test_create_new_version(strategy_service):
    """验证创建新版本功能。"""
    service, _database = strategy_service

    new_id = service.create_version(
        parameters={"max_refund_rate": "0.35", "min_inventory_days": "21"},
        effective_from=date(2026, 7, 1),
        actor="test-user",
    )

    # 验证新版本已创建
    assert new_id
    new_version = service.get_version(new_id)
    assert new_version is not None
    assert new_version["version"] == 2
    assert new_version["parameters"]["max_refund_rate"] == "0.35"
    assert new_version["parameters"]["min_inventory_days"] == "21"
    assert new_version["created_by"] == "test-user"

    # 验证当前版本已切换
    current = service.get_current_version()
    assert current["id"] == new_id
    assert current["version"] == 2

    # 验证旧版本已关闭
    old_version = service.get_version("default-v1")
    assert old_version["effective_to"] == "2026-06-30"


def test_create_version_with_empty_parameters_raises(strategy_service):
    """验证空参数创建版本抛出异常。"""
    service, _ = strategy_service
    with pytest.raises(StrategyRuleError, match="must not be empty"):
        service.create_version(
            parameters={},
            effective_from=date(2026, 8, 1),
            actor="test-user",
        )


def test_create_version_with_duplicate_effective_from_raises(strategy_service):
    """验证重复生效日期创建版本抛出异常。"""
    service, _ = strategy_service

    # 第一次创建成功
    service.create_version(
        parameters={"max_refund_rate": "0.35", "min_inventory_days": "21"},
        effective_from=date(2026, 7, 1),
        actor="test-user",
    )

    # 第二次使用相同生效日期应抛出异常
    with pytest.raises(StrategyRuleError, match="conflicts"):
        service.create_version(
            parameters={"max_refund_rate": "0.40", "min_inventory_days": "30"},
            effective_from=date(2026, 7, 1),
            actor="test-user",
        )


def test_version_list_order(strategy_service):
    """验证版本列表按版本号降序排列。"""
    service, _ = strategy_service

    # 创建新版本
    service.create_version(
        parameters={"max_refund_rate": "0.40", "min_inventory_days": "30"},
        effective_from=date(2026, 8, 1),
        actor="test-user",
    )

    versions = service.list_versions()
    # 最新版本在前
    assert versions[0]["version"] == 2
    assert versions[1]["version"] == 1


def test_version_parameters_parsed_correctly(strategy_service):
    """验证版本参数 JSON 解析正确。"""
    service, _ = strategy_service

    new_id = service.create_version(
        parameters={
            "max_refund_rate": "0.35",
            "min_inventory_days": "21",
            "bonus_threshold": "0.10",
        },
        effective_from=date(2026, 7, 1),
        actor="test-user",
    )

    version = service.get_version(new_id)
    assert version["parameters"]["max_refund_rate"] == "0.35"
    assert version["parameters"]["min_inventory_days"] == "21"
    assert version["parameters"]["bonus_threshold"] == "0.10"


def test_as_of_date_finds_correct_version(strategy_service):
    """验证按日期查询找到正确的历史版本。"""
    service, _ = strategy_service

    # 创建两个新版本
    v2_id = service.create_version(
        parameters={"max_refund_rate": "0.35", "min_inventory_days": "21"},
        effective_from=date(2026, 7, 1),
        actor="test-user",
    )
    service.create_version(
        parameters={"max_refund_rate": "0.40", "min_inventory_days": "30"},
        effective_from=date(2026, 9, 1),
        actor="test-user",
    )

    # 查询 2026-08-15 应返回 v2
    version = service.get_current_version(as_of=date(2026, 8, 15))
    assert version["id"] == v2_id
    assert version["version"] == 2
    assert version["effective_from"] == "2026-07-01"
    assert version["effective_to"] == "2026-08-31"


def _seed_shadow_metrics(database: Database) -> None:
    """向 daily_ad_metrics 表插入 Shadow 测试数据。"""
    with database.connect() as connection:
        rows = [
            ("tiktok", "store_a", "cam_1", "sku_1", "100.00", "30.00", 5),
            ("tiktok", "store_a", "cam_1", "sku_2", "200.00", "150.00", 10),
            ("shopee", "store_b", "cam_2", "sku_3", "300.00", "280.00", 15),
            ("shopee", "store_b", "cam_2", "sku_4", "50.00", "0.00", 0),
        ]
        for platform, store, campaign_id, sku_id, spend, gmv, orders in rows:
            connection.execute(
                """
                INSERT INTO daily_ad_metrics(
                    platform, store, account_id, campaign_id, sku_id,
                    data_date, currency, spend, attributed_gmv, orders, source
                ) VALUES (?, ?, ?, ?, ?, date('now', '-1 day'), 'USD', ?, ?, ?, 'api')
                """,
                (platform, store, f"acct_{store}", campaign_id, sku_id,
                 spend, gmv, orders),
            )


def test_run_shadow_returns_results_for_seeded_data(strategy_service):
    """验证 run_shadow 在有数据时返回模拟推荐结果。"""
    service, database = strategy_service
    _seed_shadow_metrics(database)

    results = service.run_shadow({
        "pause_roas_ratio": "0.50",
        "reduce_roas_ratio": "0.70",
    })

    assert len(results) == 4
    # 验证结果包含所有字段
    for result in results:
        assert "platform" in result
        assert "store" in result
        assert "campaign_id" in result
        assert "sku_id" in result
        assert "total_spend" in result
        assert "total_gmv" in result
        assert "total_orders" in result
        assert "simulated_action" in result


def test_run_shadow_does_not_write_to_recommendations(strategy_service):
    """验证 Shadow 模式不写入 recommendations 表。"""
    service, database = strategy_service
    _seed_shadow_metrics(database)

    # 运行前检查 recommendations 表
    with database.connect() as connection:
        before = connection.execute(
            "SELECT COUNT(*) AS cnt FROM recommendations"
        ).fetchone()["cnt"]

    service.run_shadow({
        "pause_roas_ratio": "0.50",
        "reduce_roas_ratio": "0.70",
    })

    # 运行后检查 recommendations 表无变化
    with database.connect() as connection:
        after = connection.execute(
            "SELECT COUNT(*) AS cnt FROM recommendations"
        ).fetchone()["cnt"]
    assert after == before


def test_run_shadow_empty_data_returns_empty_list(strategy_service):
    """验证无数据时 run_shadow 返回空列表。"""
    service, _ = strategy_service
    results = service.run_shadow({
        "pause_roas_ratio": "0.50",
        "reduce_roas_ratio": "0.70",
    })
    assert results == []


def test_simulate_action_returns_pause_for_low_roas(strategy_service):
    """验证 ROAS 低于 pause 阈值时返回 pause。"""
    service, _ = strategy_service

    row = {"total_spend": "100.00", "total_gmv": "30.00"}  # ROAS = 0.30
    action = service._simulate_action(
        row, {"pause_roas_ratio": "0.50", "reduce_roas_ratio": "0.70"}
    )
    assert action == "pause"


def test_simulate_action_returns_reduce_for_medium_roas(strategy_service):
    """验证 ROAS 在 pause 和 reduce 阈值之间时返回 reduce。"""
    service, _ = strategy_service

    row = {"total_spend": "100.00", "total_gmv": "60.00"}  # ROAS = 0.60
    action = service._simulate_action(
        row, {"pause_roas_ratio": "0.50", "reduce_roas_ratio": "0.70"}
    )
    assert action == "reduce"


def test_simulate_action_returns_no_change_for_high_roas(strategy_service):
    """验证 ROAS 高于 reduce 阈值时返回 no_change。"""
    service, _ = strategy_service

    row = {"total_spend": "100.00", "total_gmv": "80.00"}  # ROAS = 0.80
    action = service._simulate_action(
        row, {"pause_roas_ratio": "0.50", "reduce_roas_ratio": "0.70"}
    )
    assert action == "no_change"


def test_simulate_action_zero_spend_returns_no_change(strategy_service):
    """验证零花费时返回 no_change（避免除零错误）。"""
    service, _ = strategy_service

    row = {"total_spend": "0", "total_gmv": "0"}
    action = service._simulate_action(
        row, {"pause_roas_ratio": "0.50", "reduce_roas_ratio": "0.70"}
    )
    assert action == "no_change"


def test_simulate_action_uses_custom_parameters(strategy_service):
    """验证自定义参数生效。"""
    service, _ = strategy_service

    row = {"total_spend": "100.00", "total_gmv": "65.00"}  # ROAS = 0.65
    # 使用更宽松的阈值
    action = service._simulate_action(
        row, {"pause_roas_ratio": "0.30", "reduce_roas_ratio": "0.60"}
    )
    # ROAS 0.65 > 0.60，所以应为 no_change
    assert action == "no_change"
