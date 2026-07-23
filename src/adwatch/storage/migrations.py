MIGRATIONS = [
    (
        1,
        """
        CREATE TABLE daily_ad_metrics (
            id INTEGER PRIMARY KEY,
            platform TEXT NOT NULL,
            store TEXT NOT NULL,
            account_id TEXT NOT NULL,
            campaign_id TEXT NOT NULL,
            sku_id TEXT NOT NULL,
            data_date TEXT NOT NULL,
            currency TEXT NOT NULL,
            spend TEXT NOT NULL,
            attributed_gmv TEXT NOT NULL,
            orders INTEGER NOT NULL,
            roas TEXT,
            cpa TEXT,
            source TEXT NOT NULL,
            updated_at TEXT NOT NULL DEFAULT (
                strftime('%Y-%m-%dT%H:%M:%fZ', 'now')
            ),
            UNIQUE (
                platform, store, account_id, campaign_id, sku_id, data_date
            )
        );

        CREATE TABLE collection_runs (
            id TEXT PRIMARY KEY,
            mode TEXT NOT NULL,
            platform TEXT NOT NULL,
            started_at TEXT NOT NULL,
            finished_at TEXT,
            status TEXT NOT NULL,
            received_count INTEGER NOT NULL DEFAULT 0,
            accepted_count INTEGER NOT NULL DEFAULT 0,
            quarantined_count INTEGER NOT NULL DEFAULT 0,
            error_code TEXT,
            error_message TEXT
        );

        CREATE TABLE quality_checks (
            id INTEGER PRIMARY KEY,
            run_id TEXT NOT NULL REFERENCES collection_runs(id),
            check_code TEXT NOT NULL,
            passed INTEGER NOT NULL,
            affected_count INTEGER NOT NULL,
            details_json TEXT NOT NULL
        );

        CREATE TABLE quarantined_records (
            id INTEGER PRIMARY KEY,
            run_id TEXT NOT NULL REFERENCES collection_runs(id),
            raw_json TEXT NOT NULL,
            issues_json TEXT NOT NULL,
            created_at TEXT NOT NULL DEFAULT (
                strftime('%Y-%m-%dT%H:%M:%fZ', 'now')
            )
        );
        """,
    )
    ,
    (
        2,
        """
        CREATE TABLE stores (
            id INTEGER PRIMARY KEY,
            platform TEXT NOT NULL,
            store TEXT NOT NULL,
            country TEXT NOT NULL,
            currency TEXT NOT NULL,
            UNIQUE(platform, store)
        );

        CREATE TABLE campaign_settings (
            platform TEXT NOT NULL,
            campaign_id TEXT NOT NULL,
            start_date TEXT NOT NULL,
            target_roas TEXT NOT NULL,
            current_budget TEXT NOT NULL,
            baseline_budget TEXT NOT NULL,
            PRIMARY KEY(platform, campaign_id)
        );

        CREATE TABLE sku_mappings (
            sku_id TEXT PRIMARY KEY,
            tiktok_product_id TEXT,
            shopee_product_id TEXT
        );

        CREATE TABLE product_costs (
            sku_id TEXT NOT NULL,
            effective_date TEXT NOT NULL,
            product_cost TEXT NOT NULL,
            commission_rate TEXT NOT NULL,
            seller_shipping TEXT NOT NULL,
            coupons TEXT NOT NULL,
            allocated_fixed_cost TEXT NOT NULL,
            refund_amount TEXT NOT NULL DEFAULT '0',
            PRIMARY KEY(sku_id, effective_date)
        );

        CREATE TABLE inventory_snapshots (
            sku_id TEXT NOT NULL,
            snapshot_date TEXT NOT NULL,
            units INTEGER NOT NULL,
            expected_daily_units TEXT NOT NULL,
            PRIMARY KEY(sku_id, snapshot_date)
        );

        CREATE TABLE exchange_rates (
            currency TEXT NOT NULL,
            rate_date TEXT NOT NULL,
            rate_to_cny TEXT NOT NULL,
            PRIMARY KEY(currency, rate_date)
        );

        CREATE TABLE profit_results (
            platform TEXT NOT NULL,
            store TEXT NOT NULL,
            account_id TEXT NOT NULL,
            campaign_id TEXT NOT NULL,
            sku_id TEXT NOT NULL,
            data_date TEXT NOT NULL,
            net_sales_cny TEXT NOT NULL,
            platform_commission_cny TEXT NOT NULL,
            gross_profit_cny TEXT NOT NULL,
            net_profit_cny TEXT NOT NULL,
            break_even_roas TEXT,
            updated_at TEXT NOT NULL DEFAULT (
                strftime('%Y-%m-%dT%H:%M:%fZ', 'now')
            ),
            PRIMARY KEY(
                platform, store, account_id, campaign_id, sku_id, data_date
            )
        );

        CREATE TABLE alerts (
            id INTEGER PRIMARY KEY,
            rule_code TEXT NOT NULL,
            platform TEXT NOT NULL,
            campaign_id TEXT NOT NULL,
            sku_id TEXT NOT NULL DEFAULT '',
            data_date TEXT NOT NULL,
            severity TEXT NOT NULL,
            message TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'open',
            updated_at TEXT NOT NULL DEFAULT (
                strftime('%Y-%m-%dT%H:%M:%fZ', 'now')
            ),
            UNIQUE(rule_code, platform, campaign_id, sku_id, data_date)
        );

        CREATE TABLE recommendations (
            id INTEGER PRIMARY KEY,
            rule_code TEXT NOT NULL,
            platform TEXT NOT NULL,
            campaign_id TEXT NOT NULL,
            sku_id TEXT NOT NULL DEFAULT '',
            data_date TEXT NOT NULL,
            action TEXT NOT NULL,
            change_ratio TEXT,
            reason TEXT NOT NULL,
            requires_approval INTEGER NOT NULL,
            status TEXT NOT NULL DEFAULT 'proposed',
            updated_at TEXT NOT NULL DEFAULT (
                strftime('%Y-%m-%dT%H:%M:%fZ', 'now')
            ),
            UNIQUE(rule_code, platform, campaign_id, sku_id, data_date)
        );

        CREATE TABLE system_settings (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL,
            updated_at TEXT NOT NULL DEFAULT (
                strftime('%Y-%m-%dT%H:%M:%fZ', 'now')
            )
        );

        CREATE TABLE circuit_state (
            id INTEGER PRIMARY KEY CHECK(id = 1),
            is_open INTEGER NOT NULL,
            reasons_json TEXT NOT NULL,
            opened_at TEXT,
            recovered_at TEXT,
            recovered_by TEXT,
            recovery_reason TEXT
        );

        INSERT OR IGNORE INTO circuit_state(id, is_open, reasons_json)
        VALUES (1, 0, '[]');

        INSERT OR IGNORE INTO system_settings(key, value) VALUES
            ('tiktok_learning_days', '7'),
            ('shopee_learning_days', '14'),
            ('learning_budget_change_limit', '0.20'),
            ('normal_budget_change_limit', '0.30'),
            ('pause_roas_ratio', '0.50'),
            ('reduce_roas_ratio', '0.70'),
            ('high_roas_ratio', '1.00'),
            ('alert_daily_circuit_count', '5'),
            ('webdriver_failure_circuit_count', '3'),
            ('global_roas_circuit_ratio', '0.60');
        """,
    ),
]
