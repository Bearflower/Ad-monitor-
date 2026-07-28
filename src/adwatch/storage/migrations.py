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
    (
        3,
        """
        CREATE TABLE approvals (
            id TEXT PRIMARY KEY,
            recommendation_id INTEGER NOT NULL UNIQUE
                REFERENCES recommendations(id),
            status TEXT NOT NULL,
            requested_at TEXT NOT NULL,
            expires_at TEXT NOT NULL,
            decided_at TEXT,
            decided_by TEXT,
            decision_token_hash TEXT NOT NULL,
            decision_reason TEXT
        );

        CREATE TABLE execution_audits (
            id TEXT PRIMARY KEY,
            approval_id TEXT NOT NULL REFERENCES approvals(id),
            action TEXT NOT NULL,
            before_json TEXT,
            after_json TEXT,
            before_screenshot TEXT,
            after_screenshot TEXT,
            status TEXT NOT NULL,
            error_code TEXT,
            error_message TEXT,
            idempotency_key TEXT NOT NULL UNIQUE,
            created_at TEXT NOT NULL,
            finished_at TEXT
        );
        """,
    ),
    (
        4,
        """
        CREATE TABLE callback_events (
            event_id TEXT PRIMARY KEY,
            received_at TEXT NOT NULL DEFAULT (
                strftime('%Y-%m-%dT%H:%M:%fZ', 'now')
            )
        );
        """,
    ),
    (
        5,
        """
        ALTER TABLE recommendations
        ADD COLUMN store_id TEXT NOT NULL DEFAULT '';
        """,
    ),
    (
        6,
        """
        CREATE TABLE product_retest_candidates (
            platform TEXT NOT NULL,
            campaign_id TEXT NOT NULL,
            sku_id TEXT NOT NULL,
            available_test_budget TEXT NOT NULL,
            enabled INTEGER NOT NULL DEFAULT 1,
            PRIMARY KEY(platform, campaign_id, sku_id)
        );

        ALTER TABLE recommendations
        ADD COLUMN amount TEXT;
        """,
    ),
    (
        7,
        """
        CREATE TABLE selector_activations (
            platform TEXT NOT NULL,
            action TEXT NOT NULL,
            selector_version TEXT NOT NULL,
            selectors_json TEXT NOT NULL,
            store_id TEXT NOT NULL,
            activated_by TEXT NOT NULL,
            evidence_before TEXT NOT NULL,
            evidence_after TEXT NOT NULL,
            activated_at TEXT NOT NULL DEFAULT (
                strftime('%Y-%m-%dT%H:%M:%fZ', 'now')
            ),
            PRIMARY KEY(platform, action)
        );
        """,
    ),
    (
        8,
        """
        CREATE TABLE order_cost_lines (
            platform TEXT NOT NULL,
            store TEXT NOT NULL,
            order_id TEXT NOT NULL,
            sku_id TEXT NOT NULL,
            order_date TEXT NOT NULL,
            quantity INTEGER NOT NULL CHECK(quantity > 0),
            unit_cost_cny TEXT NOT NULL,
            line_cost_cny TEXT NOT NULL,
            source_file TEXT NOT NULL,
            updated_at TEXT NOT NULL DEFAULT (
                strftime('%Y-%m-%dT%H:%M:%fZ', 'now')
            ),
            PRIMARY KEY(platform, store, order_id, sku_id)
        );

        CREATE INDEX order_cost_lines_daily_idx
        ON order_cost_lines(platform, store, order_date);

        CREATE TABLE store_aliases (
            platform TEXT NOT NULL,
            source_store TEXT NOT NULL,
            canonical_store TEXT NOT NULL,
            PRIMARY KEY(platform, source_store)
        );
        """,
    ),
    (
        9,
        """
        CREATE TABLE platform_order_lines (
            platform TEXT NOT NULL,
            store TEXT NOT NULL,
            order_id TEXT NOT NULL,
            item_id TEXT NOT NULL,
            model_id TEXT NOT NULL,
            seller_sku TEXT NOT NULL,
            variation_name TEXT NOT NULL,
            product_name TEXT NOT NULL,
            quantity INTEGER NOT NULL CHECK(quantity > 0),
            buyer_paid TEXT NOT NULL DEFAULT '0',
            currency TEXT NOT NULL,
            order_status TEXT NOT NULL,
            logistics_status TEXT NOT NULL,
            refund_status TEXT NOT NULL,
            ordered_at TEXT NOT NULL,
            source_updated_at TEXT NOT NULL,
            PRIMARY KEY(platform, store, order_id, item_id, model_id)
        );

        CREATE INDEX platform_order_lines_date_idx
        ON platform_order_lines(platform, store, ordered_at);

        CREATE TABLE platform_sku_mappings (
            platform TEXT NOT NULL,
            store TEXT NOT NULL,
            item_id TEXT NOT NULL,
            model_id TEXT NOT NULL,
            seller_sku TEXT NOT NULL,
            variation_name TEXT NOT NULL,
            product_name TEXT NOT NULL,
            inventory_units INTEGER NOT NULL CHECK(inventory_units >= 0),
            observed_at TEXT NOT NULL,
            PRIMARY KEY(platform, store, item_id, model_id)
        );

        CREATE UNIQUE INDEX platform_sku_seller_idx
        ON platform_sku_mappings(platform, store, seller_sku);

        CREATE TABLE sku_cost_history (
            platform TEXT NOT NULL,
            store TEXT NOT NULL,
            seller_sku TEXT NOT NULL,
            effective_date TEXT NOT NULL,
            unit_cost_cny TEXT NOT NULL,
            note TEXT NOT NULL DEFAULT '',
            updated_at TEXT NOT NULL DEFAULT (
                strftime('%Y-%m-%dT%H:%M:%fZ', 'now')
            ),
            PRIMARY KEY(platform, store, seller_sku, effective_date)
        );

        CREATE TABLE order_sync_runs (
            id TEXT PRIMARY KEY,
            platform TEXT NOT NULL,
            store TEXT NOT NULL,
            started_at TEXT NOT NULL,
            finished_at TEXT,
            status TEXT NOT NULL,
            order_count INTEGER NOT NULL DEFAULT 0,
            sku_count INTEGER NOT NULL DEFAULT 0,
            rejected_count INTEGER NOT NULL DEFAULT 0,
            error_message TEXT
        );
        """,
    ),
    (
        10,
        """
        CREATE TABLE expense_entries (
            id TEXT PRIMARY KEY,
            occurred_on TEXT NOT NULL,
            category TEXT NOT NULL,
            amount_original TEXT NOT NULL,
            currency TEXT NOT NULL,
            rate_to_cny TEXT NOT NULL,
            amount_cny TEXT NOT NULL,
            payer TEXT NOT NULL,
            fund_nature TEXT NOT NULL,
            affects_profit INTEGER NOT NULL CHECK(affects_profit IN (0, 1)),
            affects_capital INTEGER NOT NULL CHECK(affects_capital IN (0, 1)),
            status TEXT NOT NULL CHECK(status IN ('draft','confirmed','reversed')),
            note TEXT NOT NULL DEFAULT '',
            reversal_reason TEXT,
            created_by TEXT NOT NULL,
            created_at TEXT NOT NULL
        );

        CREATE TABLE capital_entries (
            id TEXT PRIMARY KEY, partner TEXT NOT NULL, entry_type TEXT NOT NULL,
            amount_original TEXT NOT NULL, currency TEXT NOT NULL,
            rate_to_cny TEXT NOT NULL, amount_cny TEXT NOT NULL,
            occurred_on TEXT NOT NULL, status TEXT NOT NULL,
            created_at TEXT NOT NULL
        );
        CREATE TABLE withdrawal_entries (
            id TEXT PRIMARY KEY, partner TEXT NOT NULL,
            amount_original TEXT NOT NULL, currency TEXT NOT NULL,
            rate_to_cny TEXT NOT NULL, amount_cny TEXT NOT NULL,
            occurred_on TEXT NOT NULL, purpose TEXT NOT NULL,
            status TEXT NOT NULL, created_at TEXT NOT NULL
        );
        CREATE TABLE ad_funding_entries (
            id TEXT PRIMARY KEY, platform TEXT NOT NULL, store TEXT NOT NULL,
            entry_type TEXT NOT NULL, amount_original TEXT NOT NULL,
            currency TEXT NOT NULL, rate_to_cny TEXT NOT NULL,
            amount_cny TEXT NOT NULL, occurred_on TEXT NOT NULL,
            source TEXT NOT NULL, external_key TEXT UNIQUE,
            status TEXT NOT NULL, created_at TEXT NOT NULL
        );
        CREATE TABLE ad_spend_entries (
            id TEXT PRIMARY KEY, platform TEXT NOT NULL, store TEXT NOT NULL,
            campaign_id TEXT NOT NULL, amount_original TEXT NOT NULL,
            currency TEXT NOT NULL, rate_to_cny TEXT NOT NULL,
            amount_cny TEXT NOT NULL, occurred_on TEXT NOT NULL,
            source TEXT NOT NULL, external_key TEXT UNIQUE,
            created_at TEXT NOT NULL
        );
        CREATE TABLE review_order_costs (
            id TEXT PRIMARY KEY, platform TEXT NOT NULL, store TEXT NOT NULL,
            order_id TEXT NOT NULL, seller_sku TEXT NOT NULL DEFAULT '',
            goods_cost_cny TEXT NOT NULL, service_fee_cny TEXT NOT NULL,
            occurred_on TEXT NOT NULL, excluded_from_real_metrics INTEGER
                NOT NULL DEFAULT 1 CHECK(excluded_from_real_metrics IN (0, 1)),
            status TEXT NOT NULL, created_at TEXT NOT NULL,
            UNIQUE(platform, store, order_id, seller_sku)
        );
        CREATE TABLE cash_movements (
            id TEXT PRIMARY KEY, occurred_on TEXT NOT NULL,
            movement_type TEXT NOT NULL, amount_cny TEXT NOT NULL,
            source_type TEXT NOT NULL, source_id TEXT NOT NULL,
            reversal_of TEXT REFERENCES cash_movements(id),
            created_at TEXT NOT NULL,
            UNIQUE(source_type, source_id, movement_type)
        );
        CREATE TABLE audit_events (
            id TEXT PRIMARY KEY, object_type TEXT NOT NULL,
            object_id TEXT NOT NULL, action TEXT NOT NULL,
            before_json TEXT, after_json TEXT, actor TEXT NOT NULL,
            reason TEXT, created_at TEXT NOT NULL
        );
        CREATE INDEX audit_events_object_idx
        ON audit_events(object_type, object_id, created_at);
        """,
    ),
    (
        11,
        """
        CREATE TABLE purchase_receipts (
            id TEXT PRIMARY KEY, supplier TEXT NOT NULL,
            received_on TEXT NOT NULL, status TEXT NOT NULL,
            created_by TEXT NOT NULL, created_at TEXT NOT NULL
        );
        CREATE TABLE purchase_lines (
            receipt_id TEXT NOT NULL REFERENCES purchase_receipts(id),
            seller_sku TEXT NOT NULL, quantity INTEGER NOT NULL CHECK(quantity > 0),
            unit_cost_cny TEXT NOT NULL, line_cost_cny TEXT NOT NULL,
            PRIMARY KEY(receipt_id, seller_sku)
        );
        CREATE TABLE inventory_movements (
            id TEXT PRIMARY KEY, seller_sku TEXT NOT NULL,
            movement_type TEXT NOT NULL, quantity_delta INTEGER NOT NULL,
            occurred_on TEXT NOT NULL, source_type TEXT NOT NULL,
            source_id TEXT NOT NULL, note TEXT NOT NULL DEFAULT '',
            created_at TEXT NOT NULL,
            UNIQUE(source_type, source_id, seller_sku, movement_type)
        );
        CREATE TABLE inventory_balances (
            seller_sku TEXT PRIMARY KEY, units INTEGER NOT NULL,
            updated_at TEXT NOT NULL
        );
        CREATE TABLE order_cost_snapshots (
            platform TEXT NOT NULL, store TEXT NOT NULL,
            order_id TEXT NOT NULL, seller_sku TEXT NOT NULL,
            quantity INTEGER NOT NULL CHECK(quantity > 0),
            unit_cost_cny TEXT NOT NULL, total_cost_cny TEXT NOT NULL,
            cost_effective_date TEXT, status TEXT NOT NULL,
            created_at TEXT NOT NULL,
            PRIMARY KEY(platform, store, order_id, seller_sku)
        );
        """,
    ),
    (
        12,
        """
        CREATE TABLE profit_share_agreements (
            id TEXT PRIMARY KEY, effective_from TEXT NOT NULL UNIQUE,
            effective_to TEXT, version INTEGER NOT NULL UNIQUE,
            shares_json TEXT NOT NULL, created_by TEXT NOT NULL,
            created_at TEXT NOT NULL
        );
        CREATE TABLE profit_periods (
            id TEXT PRIMARY KEY, starts_on TEXT NOT NULL, ends_on TEXT NOT NULL,
            agreement_id TEXT NOT NULL REFERENCES profit_share_agreements(id),
            net_profit_cny TEXT NOT NULL, status TEXT NOT NULL,
            reversal_reason TEXT, created_by TEXT NOT NULL,
            created_at TEXT NOT NULL,
            CHECK(starts_on <= ends_on)
        );
        CREATE TABLE profit_allocations (
            period_id TEXT NOT NULL REFERENCES profit_periods(id),
            partner TEXT NOT NULL, share_ratio TEXT NOT NULL,
            amount_cny TEXT NOT NULL,
            PRIMARY KEY(period_id, partner)
        );
        CREATE TABLE profit_payments (
            id TEXT PRIMARY KEY,
            period_id TEXT NOT NULL REFERENCES profit_periods(id),
            partner TEXT NOT NULL, amount_cny TEXT NOT NULL,
            paid_on TEXT NOT NULL, status TEXT NOT NULL,
            note TEXT NOT NULL DEFAULT '', created_at TEXT NOT NULL
        );
        """,
    ),
    (
        13,
        """
        CREATE TABLE settlement_records (
            external_key TEXT PRIMARY KEY, platform TEXT NOT NULL,
            store TEXT NOT NULL, order_id TEXT NOT NULL,
            settled_on TEXT NOT NULL, amount_original TEXT NOT NULL,
            currency TEXT NOT NULL, rate_to_cny TEXT NOT NULL,
            amount_cny TEXT NOT NULL, source TEXT NOT NULL,
            imported_at TEXT NOT NULL
        );
        CREATE TABLE integration_capability_runs (
            id TEXT PRIMARY KEY, capability TEXT NOT NULL,
            status TEXT NOT NULL, reason TEXT NOT NULL,
            record_count INTEGER NOT NULL, checked_at TEXT NOT NULL
        );
        """,
    ),
]
