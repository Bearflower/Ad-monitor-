from datetime import date

from adwatch.operations.fact_sync import export_facts, import_facts, publish_facts
from adwatch.storage.db import Database


def _insert_metric(connection, data_date: str, spend: str, sku: str = "__ALL__"):
    connection.execute(
        """
        INSERT INTO daily_ad_metrics(
            platform,store,account_id,campaign_id,sku_id,data_date,
            currency,spend,attributed_gmv,orders,source
        ) VALUES('shopee','shop','account','campaign',?,?,
                 'THB',?,'20',1,'test')
        """,
        (sku, data_date, spend),
    )


def test_fact_bundle_replaces_daily_snapshot_and_fills_missing_costs(tmp_path):
    source = Database(tmp_path / "source.sqlite3")
    target = Database(tmp_path / "target.sqlite3")
    source.migrate()
    target.migrate()
    with source.transaction() as connection:
        _insert_metric(connection, "2026-08-10", "10")
        connection.execute(
            """
            INSERT INTO platform_order_lines VALUES(
              'shopee','shop','ORDER-1','ITEM-1','MODEL-1','SKU-1',
              'variant','Product',1,'100','THB','completed','delivered','',
              '2026-08-10','2026-08-11T00:00:00Z')
            """
        )
        connection.execute(
            """
            INSERT INTO platform_sku_mappings VALUES(
              'shopee','shop','ITEM-1','MODEL-1','SKU-1','variant',
              'Product',5,'2026-08-11T00:00:00Z')
            """
        )
        connection.executemany(
            """
            INSERT INTO sku_cost_history(
              platform,store,seller_sku,effective_date,unit_cost_cny,note
            ) VALUES('shopee','shop',?,?,?,'local')
            """,
            (
                ("SKU-1", "2026-08-01", "5"),
                ("SKU-2", "2026-08-01", "6"),
            ),
        )
        connection.execute(
            """
            INSERT INTO order_sync_runs(
              id,platform,store,started_at,finished_at,status,
              order_count,sku_count,rejected_count,error_message,
              start_date,end_date,refund_count,logistics_count,
              inventory_snapshot_count,product_rejected_count,
              order_rejected_count
            ) VALUES(
              'sync-1','shopee','shop','2026-08-11T00:00:00Z',
              '2026-08-11T00:01:00Z','success',1,1,0,NULL,
              '2026-08-01','2026-08-10',0,1,1,0,0
            )
            """
        )
        connection.execute(
            """
            INSERT INTO daily_refund_inputs(
              platform,store,data_date,refund_amount_original,currency,
              status,source_type,source_ref,observed_on,closed_at,updated_at
            ) VALUES(
              'shopee','shop','2026-08-10','0','THB','t3_closed_zero',
              'refund_closure','sync-1',NULL,'2026-08-11T00:02:00Z',
              '2026-08-11T00:02:00Z'
            )
            """
        )
    with target.transaction() as connection:
        _insert_metric(connection, "2026-08-09", "9")
        _insert_metric(connection, "2026-08-10", "999")
        connection.execute(
            """
            INSERT INTO sku_cost_history(
              platform,store,seller_sku,effective_date,unit_cost_cny,note
            ) VALUES('shopee','shop','SKU-1','2026-08-01','8','production')
            """
        )
        connection.execute(
            """
            INSERT INTO daily_refund_inputs(
              platform,store,data_date,refund_amount_original,currency,
              status,source_type,source_ref,observed_on,closed_at,updated_at
            ) VALUES(
              'shopee','shop','2026-08-10','12','THB','observed',
              'manual','production-refund','2026-08-11T00:00:00Z',NULL,
              '2026-08-11T00:00:00Z'
            )
            """
        )

    payload = export_facts(
        source, start=date(2026, 8, 10), end=date(2026, 8, 10)
    )
    result = import_facts(target, payload)

    with target.connect() as connection:
        metrics = connection.execute(
            "SELECT data_date,spend FROM daily_ad_metrics ORDER BY data_date"
        ).fetchall()
        costs = connection.execute(
            """
            SELECT seller_sku,unit_cost_cny,note FROM sku_cost_history
            ORDER BY seller_sku
            """
        ).fetchall()
        orders = connection.execute(
            "SELECT COUNT(*) FROM platform_order_lines"
        ).fetchone()[0]
        sync_runs = connection.execute(
            "SELECT COUNT(*) FROM order_sync_runs WHERE id='sync-1'"
        ).fetchone()[0]
        refund = connection.execute(
            """
            SELECT refund_amount_original,status,source_ref
            FROM daily_refund_inputs
            WHERE platform='shopee' AND store='shop'
              AND data_date='2026-08-10'
            """
        ).fetchone()
    assert [tuple(row) for row in metrics] == [
        ("2026-08-09", "9"),
        ("2026-08-10", "10"),
    ]
    assert [tuple(row) for row in costs] == [
        ("SKU-1", "8", "production"),
        ("SKU-2", "6", "local"),
    ]
    assert orders == 1
    assert sync_runs == 1
    assert tuple(refund) == ("12", "observed", "production-refund")
    assert result.tables["daily_ad_metrics"] == 1


def test_publish_streams_bundle_to_remote_container():
    calls = []

    def runner(command, **kwargs):
        calls.append((command, kwargs))

        class Completed:
            returncode = 0
            stdout = b"replicate_import=ok rows=3"
            stderr = b""

        return Completed()

    output = publish_facts(
        b'{"version":1}',
        target="root@43.156.242.184",
        container="adwatch",
        runner=runner,
    )

    assert output == "replicate_import=ok rows=3"
    assert calls[0][0] == [
        "ssh",
        "root@43.156.242.184",
        "docker",
        "exec",
        "-i",
        "adwatch",
        "adwatch",
        "replicate",
        "import",
        "--stdin",
    ]
    assert calls[0][1]["input"] == b'{"version":1}'
