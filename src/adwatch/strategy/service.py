from __future__ import annotations

import json
import uuid
from datetime import UTC, date, datetime
from decimal import Decimal

from adwatch.storage.db import Database


class StrategyRuleError(ValueError):
    """策略规则版本操作异常。"""


class StrategyRuleService:
    """策略规则版本管理服务。

    提供策略规则版本的增删改查功能，支持按生效日期自动切换版本，
    确保策略参数变更可追溯、可回滚。

    数据库表: strategy_rule_versions
    """

    def __init__(self, database: Database) -> None:
        """初始化策略规则服务。

        参数:
            database: 数据库访问对象
        """
        self.database = database

    def list_versions(self) -> tuple[dict, ...]:
        """列出所有策略规则版本。

        返回所有版本，按版本号降序排列（最新版本在前）。

        返回:
            版本记录元组，每条记录包含:
            - id: 版本唯一标识
            - version: 版本号
            - effective_from: 生效起始日期
            - effective_to: 生效结束日期（None 表示至今）
            - parameters: 规则参数字典
            - created_by: 创建人
            - created_at: 创建时间
        """
        with self.database.connect() as connection:
            rows = connection.execute(
                """
                SELECT id, version, effective_from, effective_to,
                       parameters_json, created_by, created_at
                FROM strategy_rule_versions
                ORDER BY version DESC
                """
            ).fetchall()
            return tuple(
                {
                    "id": row["id"],
                    "version": row["version"],
                    "effective_from": row["effective_from"],
                    "effective_to": row["effective_to"],
                    "parameters": json.loads(row["parameters_json"]),
                    "created_by": row["created_by"],
                    "created_at": row["created_at"],
                }
                for row in rows
            )

    def get_current_version(
        self, as_of: date | None = None
    ) -> dict | None:
        """获取当前生效的策略规则版本。

        参数:
            as_of: 查询日期，为 None 时返回最新版本；
                   指定日期时返回该日期生效的版本

        返回:
            当前版本字典，包含 id, version, effective_from,
            effective_to, parameters, created_by, created_at；
            若无版本则返回 None
        """
        with self.database.connect() as connection:
            if as_of is not None:
                row = connection.execute(
                    """
                    SELECT id, version, effective_from, effective_to,
                           parameters_json, created_by, created_at
                    FROM strategy_rule_versions
                    WHERE effective_from <= ?
                      AND (effective_to IS NULL OR effective_to >= ?)
                    ORDER BY version DESC LIMIT 1
                    """,
                    (as_of.isoformat(), as_of.isoformat()),
                ).fetchone()
            else:
                row = connection.execute(
                    """
                    SELECT id, version, effective_from, effective_to,
                           parameters_json, created_by, created_at
                    FROM strategy_rule_versions
                    ORDER BY version DESC LIMIT 1
                    """
                ).fetchone()
            if row is None:
                return None
            return {
                "id": row["id"],
                "version": row["version"],
                "effective_from": row["effective_from"],
                "effective_to": row["effective_to"],
                "parameters": json.loads(row["parameters_json"]),
                "created_by": row["created_by"],
                "created_at": row["created_at"],
            }

    def get_version(self, version_id: str) -> dict | None:
        """获取指定版本的详细信息。

        参数:
            version_id: 版本唯一标识

        返回:
            版本详情字典；若版本不存在则返回 None
        """
        with self.database.connect() as connection:
            row = connection.execute(
                """
                SELECT id, version, effective_from, effective_to,
                       parameters_json, created_by, created_at
                FROM strategy_rule_versions
                WHERE id = ?
                """,
                (version_id,),
            ).fetchone()
            if row is None:
                return None
            return {
                "id": row["id"],
                "version": row["version"],
                "effective_from": row["effective_from"],
                "effective_to": row["effective_to"],
                "parameters": json.loads(row["parameters_json"]),
                "created_by": row["created_by"],
                "created_at": row["created_at"],
            }

    def create_version(
        self,
        *,
        parameters: dict,
        effective_from: date,
        actor: str,
    ) -> str:
        """创建新的策略规则版本。

        自动处理版本号递增、关闭上一版本的生效期，
        确保同一日期只有一个生效版本。

        参数:
            parameters: 规则参数键值对，包含如 max_refund_rate、
                        min_inventory_days 等业务参数
            effective_from: 新版本生效起始日期
            actor: 创建人标识

        返回:
            新版本的唯一标识 ID

        异常:
            StrategyRuleError: 参数为空、生效日期冲突、或版本号冲突时抛出
        """
        if not parameters:
            raise StrategyRuleError("parameters must not be empty")

        version_id = str(uuid.uuid4())
        now = datetime.now(UTC).isoformat()
        effective_from_str = effective_from.isoformat()

        with self.database.transaction() as connection:
            # 查询最新版本号和生效日期
            latest = connection.execute(
                """
                SELECT id, effective_from, version
                FROM strategy_rule_versions
                ORDER BY version DESC LIMIT 1
                """
            ).fetchone()

            # 校验生效日期冲突：不允许与已有版本生效日期相同
            if latest and effective_from_str <= latest["effective_from"]:
                # 允许生效日期早于最新版本（如回滚场景），但需校验唯一性
                existing = connection.execute(
                    """
                    SELECT 1 FROM strategy_rule_versions
                    WHERE effective_from = ?
                    """,
                    (effective_from_str,),
                ).fetchone()
                if existing:
                    raise StrategyRuleError(
                        f"effective_from {effective_from_str} conflicts "
                        "with an existing version"
                    )

            # 确定新版本号
            version_num = 1 if latest is None else int(latest["version"]) + 1

            # 如果有上一版本，关闭其生效期（设置 effective_to 为新版本前一天）
            if latest:
                prev_end_date = date.fromordinal(
                    effective_from.toordinal() - 1
                ).isoformat()
                connection.execute(
                    """
                    UPDATE strategy_rule_versions
                    SET effective_to = ?
                    WHERE id = ? AND effective_to IS NULL
                    """,
                    (prev_end_date, latest["id"]),
                )

            # 插入新版本
            connection.execute(
                """
                INSERT INTO strategy_rule_versions(
                    id, version, effective_from, parameters_json,
                    created_by, created_at
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    version_id,
                    version_num,
                    effective_from_str,
                    json.dumps(parameters, ensure_ascii=False, sort_keys=True),
                    actor,
                    now,
                ),
            )

        return version_id

    def close_version(self, version_id: str) -> None:
        """关闭指定版本，设置其生效结束日期为今天。

        关闭后该版本不再被视为"生效中"版本，后续新版本不受影响。

        参数:
            version_id: 版本唯一标识

        异常:
            StrategyRuleError: 版本不存在或已关闭
        """
        with self.database.transaction() as connection:
            # 查询版本是否存在以及是否已关闭
            version = connection.execute(
                """
                SELECT id, effective_to
                FROM strategy_rule_versions
                WHERE id = ?
                """,
                (version_id,),
            ).fetchone()
            if version is None:
                raise StrategyRuleError(
                    f"version {version_id} not found"
                )
            if version["effective_to"] is not None:
                raise StrategyRuleError(
                    f"version {version_id} is already closed"
                )
            # 设置生效结束日期为今天
            connection.execute(
                """
                UPDATE strategy_rule_versions
                SET effective_to = ?
                WHERE id = ?
                """,
                (datetime.now(UTC).date().isoformat(), version_id),
            )

    def run_shadow(self, parameters: dict) -> list[dict]:
        """在 Shadow 模式下模拟运行推荐引擎。

        使用给定的参数对最近 7 天的广告指标进行模拟推荐，
        不写入 recommendations 表，仅返回模拟结果用于预览。

        参数:
            parameters: 模拟规则参数，包含如 max_refund_rate、
                       min_inventory_days 等阈值配置

        返回:
            模拟推荐结果列表，每个结果包含平台、店铺、活动 ID、
            SKU、花费、GMV、订单数和模拟动作
        """
        with self.database.connect() as connection:
            rows = connection.execute(
                """
                SELECT platform, store, campaign_id, sku_id,
                       SUM(CAST(spend AS REAL)) AS total_spend,
                       SUM(CAST(attributed_gmv AS REAL)) AS total_gmv,
                       SUM(orders) AS total_orders
                FROM daily_ad_metrics AS metric
                WHERE data_date >= date('now', '-7 days')
                  AND (
                      metric.sku_id<>'__ALL__'
                      OR NOT EXISTS (
                          SELECT 1 FROM daily_ad_metrics AS detail
                          WHERE detail.platform=metric.platform
                            AND detail.store=metric.store
                            AND detail.account_id=metric.account_id
                            AND detail.data_date=metric.data_date
                            AND detail.sku_id<>'__ALL__'
                      )
                  )
                GROUP BY platform, store, campaign_id, sku_id
                """
            ).fetchall()

        results = []
        for row in rows:
            result = {
                "platform": row["platform"],
                "store": row["store"],
                "campaign_id": row["campaign_id"],
                "sku_id": row["sku_id"],
                "total_spend": round(row["total_spend"], 2),
                "total_gmv": round(row["total_gmv"], 2),
                "total_orders": row["total_orders"],
                "simulated_action": self._simulate_action(row, parameters),
            }
            results.append(result)

        return results

    @staticmethod
    def _simulate_action(row, parameters: dict) -> str:
        """根据模拟参数和实际数据生成推荐动作。

        基于 ROAS（GMV / 花费）与参数中的阈值进行对比：
        - 低于 pause_roas_ratio: 暂停
        - 低于 reduce_roas_ratio: 降低预算
        - 其他: 不调整

        参数:
            row: 数据库查询结果行，包含 total_spend 和 total_gmv
            parameters: 模拟规则参数字典

        返回:
            推荐动作字符串: "pause" / "reduce" / "no_change"
        """
        spend = Decimal(str(row["total_spend"] or 0))
        gmv = Decimal(str(row["total_gmv"] or 0))
        if spend == 0:
            return "no_change"

        roas = gmv / spend
        # 从参数中读取阈值，使用默认值兜底
        pause_ratio = Decimal(
            str(parameters.get("pause_roas_ratio", "0.50"))
        )
        reduce_ratio = Decimal(
            str(parameters.get("reduce_roas_ratio", "0.70"))
        )

        if roas < pause_ratio:
            return "pause"
        if roas < reduce_ratio:
            return "reduce"
        return "no_change"
