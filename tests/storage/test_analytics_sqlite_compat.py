import tempfile
import unittest
from datetime import date
from pathlib import Path

from adwatch.storage.analytics import AnalyticsRepository
from adwatch.storage.db import Database


class AnalyticsSqliteCompatibilityTest(unittest.TestCase):
    def test_load_analysis_rows_runs_on_deployed_sqlite(self) -> None:
        data_date = date(2026, 8, 10)
        with tempfile.TemporaryDirectory() as directory:
            database = Database(Path(directory) / "adwatch.sqlite3")
            database.migrate()
            with database.transaction() as connection:
                connection.execute(
                    """
                    INSERT INTO daily_ad_metrics(
                        data_date, platform, store, account_id, campaign_id,
                        sku_id, spend, attributed_gmv, orders, currency, source
                    ) VALUES (?, 'shopee', 'shop', 'account-1', 'campaign-1',
                              'SKU-1', '10', '20', 1, 'CNY', 'test')
                    """,
                    (data_date.isoformat(),),
                )

            rows = AnalyticsRepository(database).load_analysis_rows(data_date)

        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["data_date"], data_date.isoformat())


if __name__ == "__main__":
    unittest.main()
