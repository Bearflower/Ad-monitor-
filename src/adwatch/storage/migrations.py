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
    (
        14,
        """
        CREATE TABLE strategy_rule_versions (
            id TEXT PRIMARY KEY, version INTEGER NOT NULL UNIQUE,
            effective_from TEXT NOT NULL UNIQUE, effective_to TEXT,
            parameters_json TEXT NOT NULL, created_by TEXT NOT NULL,
            created_at TEXT NOT NULL
        );
        ALTER TABLE recommendations ADD COLUMN rule_version_id TEXT;
        ALTER TABLE recommendations ADD COLUMN window_days INTEGER;
        ALTER TABLE recommendations
            ADD COLUMN confidence_level TEXT NOT NULL DEFAULT 'platform_only';
        ALTER TABLE recommendations
            ADD COLUMN evidence_json TEXT NOT NULL DEFAULT '{}';
        ALTER TABLE recommendations
            ADD COLUMN expected_before_json TEXT NOT NULL DEFAULT '{}';
        ALTER TABLE recommendations
            ADD COLUMN expected_impact_json TEXT NOT NULL DEFAULT '{}';
        """,
    ),
    (
        15,
        """
        CREATE TABLE reconciliation_days (
            platform TEXT NOT NULL, store TEXT NOT NULL,
            data_date TEXT NOT NULL, accuracy TEXT NOT NULL,
            expected_json TEXT NOT NULL, actual_json TEXT NOT NULL,
            differences_json TEXT NOT NULL, created_at TEXT NOT NULL,
            PRIMARY KEY(platform, store, data_date)
        );
        CREATE TABLE strategy_replays (
            id TEXT PRIMARY KEY, platform TEXT NOT NULL, store TEXT NOT NULL,
            campaign_id TEXT NOT NULL, starts_on TEXT NOT NULL,
            ends_on TEXT NOT NULL, rule_version_id TEXT,
            matches_original INTEGER NOT NULL,
            details_json TEXT NOT NULL, created_at TEXT NOT NULL
        );
        """,
    ),
    (
        16,
        """
        INSERT OR IGNORE INTO strategy_rule_versions(
            id, version, effective_from, parameters_json,
            created_by, created_at
        ) VALUES (
            'default-v1', 1, '2026-01-01',
            '{"max_refund_rate":"0.30","min_inventory_days":"14"}',
            'system', strftime('%Y-%m-%dT%H:%M:%fZ', 'now')
        );
        """,
    ),
    (
        17,
        """
        CREATE TABLE sku_fulfillment_history (
            platform TEXT NOT NULL,
            store TEXT NOT NULL,
            seller_sku TEXT NOT NULL,
            effective_date TEXT NOT NULL,
            mode TEXT NOT NULL CHECK(
                mode IN ('supplier_fulfilled', 'stocked')
            ),
            supply_status TEXT NOT NULL CHECK(
                supply_status IN ('available', 'paused')
            ),
            note TEXT NOT NULL DEFAULT '',
            created_at TEXT NOT NULL,
            PRIMARY KEY(platform, store, seller_sku, effective_date)
        );
        CREATE INDEX sku_fulfillment_effective_idx
        ON sku_fulfillment_history(
            platform, store, seller_sku, effective_date
        );

        CREATE TABLE order_fulfillment_snapshots (
            platform TEXT NOT NULL,
            store TEXT NOT NULL,
            order_id TEXT NOT NULL,
            seller_sku TEXT NOT NULL,
            mode TEXT NOT NULL CHECK(
                mode IN ('supplier_fulfilled', 'stocked')
            ),
            policy_effective_date TEXT NOT NULL,
            supply_status TEXT NOT NULL CHECK(
                supply_status IN ('available', 'paused')
            ),
            resolution_source TEXT NOT NULL DEFAULT 'sku_policy',
            created_at TEXT NOT NULL,
            PRIMARY KEY(platform, store, order_id, seller_sku)
        );
        """,
    ),
    (
        18,
        """
        CREATE TABLE knowledge_sources (
            id TEXT PRIMARY KEY,
            source_key TEXT NOT NULL UNIQUE,
            source_type TEXT NOT NULL,
            name_zh TEXT NOT NULL,
            entry_url TEXT NOT NULL,
            allowed_hosts_json TEXT NOT NULL,
            enabled INTEGER NOT NULL DEFAULT 0 CHECK(enabled IN (0, 1)),
            last_success_at TEXT,
            last_cursor_json TEXT NOT NULL DEFAULT '{}',
            extractor_version TEXT NOT NULL,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );

        CREATE TABLE knowledge_documents (
            id TEXT PRIMARY KEY,
            source_id TEXT NOT NULL REFERENCES knowledge_sources(id),
            external_id TEXT,
            canonical_url TEXT NOT NULL,
            document_type TEXT NOT NULL,
            site TEXT NOT NULL CHECK(site = 'shopee_th'),
            status TEXT NOT NULL CHECK(status IN (
                'active', 'review_missing', 'inactive',
                'access_blocked', 'unknown'
            )),
            current_version_id TEXT,
            first_seen_at TEXT NOT NULL,
            last_seen_at TEXT NOT NULL,
            last_checked_at TEXT NOT NULL,
            missing_successful_runs INTEGER NOT NULL DEFAULT 0,
            published_at TEXT,
            remote_updated_at TEXT,
            UNIQUE(source_id, canonical_url)
        );

        CREATE TABLE knowledge_document_versions (
            id TEXT PRIMARY KEY,
            document_id TEXT NOT NULL REFERENCES knowledge_documents(id),
            version_number INTEGER NOT NULL,
            content_hash TEXT NOT NULL,
            title_original TEXT NOT NULL,
            language TEXT NOT NULL,
            content_original TEXT NOT NULL,
            content_format TEXT NOT NULL,
            metadata_json TEXT NOT NULL,
            retrieved_at TEXT NOT NULL,
            extractor_version TEXT NOT NULL,
            previous_version_id TEXT,
            UNIQUE(document_id, version_number),
            UNIQUE(document_id, content_hash)
        );

        CREATE TABLE knowledge_enrichments (
            version_id TEXT PRIMARY KEY
                REFERENCES knowledge_document_versions(id),
            status TEXT NOT NULL CHECK(status IN (
                'pending', 'completed', 'failed', 'skipped'
            )),
            title_zh TEXT NOT NULL DEFAULT '',
            summary_zh TEXT NOT NULL DEFAULT '',
            category TEXT NOT NULL DEFAULT '',
            tags_json TEXT NOT NULL DEFAULT '[]',
            key_rules_json TEXT NOT NULL DEFAULT '[]',
            model TEXT NOT NULL DEFAULT '',
            prompt_version TEXT NOT NULL DEFAULT '',
            response_id TEXT NOT NULL DEFAULT '',
            error_code TEXT,
            error_message TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );

        CREATE TABLE knowledge_chunks (
            id TEXT PRIMARY KEY,
            version_id TEXT NOT NULL
                REFERENCES knowledge_document_versions(id),
            chunk_index INTEGER NOT NULL,
            heading_original TEXT NOT NULL DEFAULT '',
            heading_zh TEXT NOT NULL DEFAULT '',
            content_original TEXT NOT NULL,
            content_zh TEXT NOT NULL DEFAULT '',
            char_start INTEGER NOT NULL,
            char_end INTEGER NOT NULL,
            UNIQUE(version_id, chunk_index)
        );

        CREATE VIRTUAL TABLE knowledge_fts USING fts5(
            chunk_id UNINDEXED,
            document_id UNINDEXED,
            version_id UNINDEXED,
            title_original,
            title_zh,
            content_original,
            content_zh,
            category,
            tags
        );

        CREATE TABLE knowledge_collection_runs (
            id TEXT PRIMARY KEY,
            mode TEXT NOT NULL,
            source_id TEXT REFERENCES knowledge_sources(id),
            started_at TEXT NOT NULL,
            finished_at TEXT,
            status TEXT NOT NULL CHECK(status IN (
                'running', 'success', 'partial', 'failed', 'interrupted'
            )),
            discovered_count INTEGER NOT NULL DEFAULT 0,
            fetched_count INTEGER NOT NULL DEFAULT 0,
            new_count INTEGER NOT NULL DEFAULT 0,
            updated_count INTEGER NOT NULL DEFAULT 0,
            unchanged_count INTEGER NOT NULL DEFAULT 0,
            inactive_count INTEGER NOT NULL DEFAULT 0,
            enriched_count INTEGER NOT NULL DEFAULT 0,
            error_code TEXT,
            error_message TEXT,
            details_json TEXT NOT NULL DEFAULT '{}'
        );

        CREATE INDEX knowledge_documents_source_status_idx
        ON knowledge_documents(source_id, status);
        CREATE INDEX knowledge_documents_current_version_idx
        ON knowledge_documents(current_version_id);
        CREATE INDEX knowledge_versions_document_hash_idx
        ON knowledge_document_versions(document_id, content_hash);
        CREATE INDEX knowledge_enrichments_status_idx
        ON knowledge_enrichments(status);
        CREATE INDEX knowledge_runs_started_idx
        ON knowledge_collection_runs(started_at);
        """,
    ),
    (
        19,
        """
        ALTER TABLE exchange_rates ADD COLUMN execution_date TEXT;
        ALTER TABLE exchange_rates ADD COLUMN source_date TEXT;
        ALTER TABLE exchange_rates ADD COLUMN resolution_status TEXT;
        ALTER TABLE exchange_rates ADD COLUMN resolved_at TEXT;
        """,
    ),
    (
        20,
        """
        DROP INDEX IF EXISTS platform_sku_seller_idx;
        CREATE INDEX platform_sku_seller_idx
        ON platform_sku_mappings(platform, store, seller_sku);
        """,
    ),
    (
        21,
        """
        CREATE TABLE platform_refund_observations (
            platform TEXT NOT NULL,
            store TEXT NOT NULL,
            order_id TEXT NOT NULL,
            observed_on TEXT NOT NULL,
            order_date TEXT NOT NULL,
            refund_status TEXT NOT NULL,
            amount_original TEXT,
            currency TEXT NOT NULL,
            source_updated_at TEXT NOT NULL,
            observed_at TEXT NOT NULL,
            source TEXT NOT NULL,
            PRIMARY KEY(platform, store, order_id, observed_on),
            CHECK(
                amount_original IS NULL
                OR CAST(amount_original AS REAL) >= 0
            )
        );

        CREATE INDEX platform_refund_observations_date_idx
        ON platform_refund_observations(platform, store, order_date);

        CREATE TABLE platform_logistics_observations (
            platform TEXT NOT NULL,
            store TEXT NOT NULL,
            order_id TEXT NOT NULL,
            observed_on TEXT NOT NULL,
            order_date TEXT NOT NULL,
            logistics_status TEXT NOT NULL,
            source_updated_at TEXT NOT NULL,
            observed_at TEXT NOT NULL,
            source TEXT NOT NULL,
            PRIMARY KEY(platform, store, order_id, observed_on)
        );

        CREATE INDEX platform_logistics_observations_date_idx
        ON platform_logistics_observations(platform, store, order_date);

        CREATE TABLE platform_inventory_snapshots (
            platform TEXT NOT NULL,
            store TEXT NOT NULL,
            item_id TEXT NOT NULL,
            model_id TEXT NOT NULL,
            seller_sku TEXT NOT NULL,
            snapshot_date TEXT NOT NULL,
            available_units INTEGER NOT NULL CHECK(available_units >= 0),
            observed_at TEXT NOT NULL,
            source TEXT NOT NULL,
            PRIMARY KEY(
                platform, store, item_id, model_id, snapshot_date
            )
        );

        CREATE INDEX platform_inventory_snapshots_sku_date_idx
        ON platform_inventory_snapshots(
            platform, store, seller_sku, snapshot_date
        );

        ALTER TABLE order_sync_runs ADD COLUMN start_date TEXT;
        ALTER TABLE order_sync_runs ADD COLUMN end_date TEXT;
        ALTER TABLE order_sync_runs
            ADD COLUMN refund_count INTEGER NOT NULL DEFAULT 0;
        ALTER TABLE order_sync_runs
            ADD COLUMN logistics_count INTEGER NOT NULL DEFAULT 0;
        ALTER TABLE order_sync_runs
            ADD COLUMN inventory_snapshot_count INTEGER NOT NULL DEFAULT 0;
        """,
    ),
    (
        22,
        """
        CREATE TABLE platform_fee_statements (
            id TEXT PRIMARY KEY,
            platform TEXT NOT NULL,
            store TEXT NOT NULL,
            source_store TEXT NOT NULL,
            period_start TEXT NOT NULL,
            period_end TEXT NOT NULL,
            currency TEXT NOT NULL,
            product_price TEXT NOT NULL,
            commission_fee TEXT NOT NULL,
            service_fee TEXT NOT NULL,
            transaction_fee TEXT NOT NULL,
            affiliate_fee TEXT NOT NULL,
            sales_fee_total TEXT NOT NULL,
            effective_rate TEXT NOT NULL,
            seller_voucher TEXT NOT NULL,
            shipping_paid_by_buyer TEXT NOT NULL,
            shipping_provider_charge TEXT NOT NULL,
            shipping_rebate TEXT NOT NULL,
            ads_credit_topup TEXT NOT NULL,
            adjustment_amount TEXT NOT NULL,
            total_payout TEXT NOT NULL,
            source_name TEXT NOT NULL,
            source_sha256 TEXT NOT NULL UNIQUE,
            version INTEGER NOT NULL CHECK(version > 0),
            is_current INTEGER NOT NULL CHECK(is_current IN (0, 1)),
            imported_at TEXT NOT NULL,
            CHECK(period_start <= period_end),
            UNIQUE(platform, store, period_start, period_end, version)
        );

        CREATE INDEX platform_fee_statements_resolve_idx
        ON platform_fee_statements(
            platform, store, is_current, period_start, period_end
        );

        CREATE TABLE income_import_events (
            source_sha256 TEXT PRIMARY KEY,
            source_name TEXT NOT NULL,
            platform TEXT NOT NULL,
            store TEXT NOT NULL DEFAULT '',
            status TEXT NOT NULL CHECK(status IN (
                'imported', 'revised', 'quarantined', 'failed'
            )),
            reason_code TEXT NOT NULL DEFAULT '',
            statement_id TEXT REFERENCES platform_fee_statements(id),
            occurred_at TEXT NOT NULL
        );

        CREATE TABLE platform_settlement_summaries (
            external_key TEXT PRIMARY KEY,
            platform TEXT NOT NULL,
            store TEXT NOT NULL,
            period_start TEXT NOT NULL,
            period_end TEXT NOT NULL,
            gross_sales_original TEXT NOT NULL,
            payout_original TEXT NOT NULL,
            currency TEXT NOT NULL,
            recognition_status TEXT NOT NULL CHECK(
                recognition_status='settlement_only'
            ),
            source_sha256 TEXT NOT NULL UNIQUE,
            source TEXT NOT NULL,
            observed_at TEXT NOT NULL
        );

        CREATE TABLE platform_fee_entries (
            external_key TEXT PRIMARY KEY,
            platform TEXT NOT NULL,
            store TEXT NOT NULL,
            period_start TEXT NOT NULL,
            period_end TEXT NOT NULL,
            fee_type TEXT NOT NULL,
            amount_original TEXT NOT NULL,
            currency TEXT NOT NULL,
            source_sha256 TEXT NOT NULL,
            source TEXT NOT NULL,
            observed_at TEXT NOT NULL,
            UNIQUE(source_sha256, fee_type)
        );

        CREATE TABLE platform_ad_funding_observations (
            external_key TEXT PRIMARY KEY,
            platform TEXT NOT NULL,
            store TEXT NOT NULL,
            occurred_on TEXT NOT NULL,
            entry_type TEXT NOT NULL,
            amount_original TEXT NOT NULL,
            currency TEXT NOT NULL,
            funding_source TEXT NOT NULL,
            source_sha256 TEXT NOT NULL UNIQUE,
            source TEXT NOT NULL,
            observed_at TEXT NOT NULL
        );

        ALTER TABLE profit_results ADD COLUMN fee_effective_rate TEXT;
        ALTER TABLE profit_results ADD COLUMN fee_period_start TEXT;
        ALTER TABLE profit_results ADD COLUMN fee_period_end TEXT;
        ALTER TABLE profit_results ADD COLUMN fee_selection TEXT;
        ALTER TABLE profit_results ADD COLUMN fee_source_name TEXT;
        ALTER TABLE profit_results ADD COLUMN fee_source_sha256 TEXT;
        """,
    ),
    (
        23,
        """
        ALTER TABLE platform_settlement_summaries
        ADD COLUMN statement_id TEXT REFERENCES platform_fee_statements(id);
        ALTER TABLE platform_settlement_summaries
        ADD COLUMN statement_version INTEGER NOT NULL DEFAULT 1
            CHECK(statement_version > 0);
        ALTER TABLE platform_settlement_summaries
        ADD COLUMN is_current INTEGER NOT NULL DEFAULT 1
            CHECK(is_current IN (0, 1));

        ALTER TABLE platform_fee_entries
        ADD COLUMN statement_id TEXT REFERENCES platform_fee_statements(id);
        ALTER TABLE platform_fee_entries
        ADD COLUMN statement_version INTEGER NOT NULL DEFAULT 1
            CHECK(statement_version > 0);
        ALTER TABLE platform_fee_entries
        ADD COLUMN is_current INTEGER NOT NULL DEFAULT 1
            CHECK(is_current IN (0, 1));

        ALTER TABLE platform_ad_funding_observations
        ADD COLUMN statement_id TEXT REFERENCES platform_fee_statements(id);
        ALTER TABLE platform_ad_funding_observations
        ADD COLUMN statement_version INTEGER NOT NULL DEFAULT 1
            CHECK(statement_version > 0);
        ALTER TABLE platform_ad_funding_observations
        ADD COLUMN is_current INTEGER NOT NULL DEFAULT 1
            CHECK(is_current IN (0, 1));

        UPDATE platform_settlement_summaries
        SET statement_id=(
            SELECT id FROM platform_fee_statements AS statement
            WHERE statement.source_sha256=
                  platform_settlement_summaries.source_sha256
        ),
            statement_version=COALESCE((
                SELECT version FROM platform_fee_statements AS statement
                WHERE statement.source_sha256=
                      platform_settlement_summaries.source_sha256
            ), 1),
            is_current=COALESCE((
                SELECT is_current FROM platform_fee_statements AS statement
                WHERE statement.source_sha256=
                      platform_settlement_summaries.source_sha256
            ), 0);
        UPDATE platform_fee_entries
        SET statement_id=(
            SELECT id FROM platform_fee_statements AS statement
            WHERE statement.source_sha256=platform_fee_entries.source_sha256
        ),
            statement_version=COALESCE((
                SELECT version FROM platform_fee_statements AS statement
                WHERE statement.source_sha256=
                      platform_fee_entries.source_sha256
            ), 1),
            is_current=COALESCE((
                SELECT is_current FROM platform_fee_statements AS statement
                WHERE statement.source_sha256=
                      platform_fee_entries.source_sha256
            ), 0);
        UPDATE platform_ad_funding_observations
        SET statement_id=(
            SELECT id FROM platform_fee_statements AS statement
            WHERE statement.source_sha256=
                  platform_ad_funding_observations.source_sha256
        ),
            statement_version=COALESCE((
                SELECT version FROM platform_fee_statements AS statement
                WHERE statement.source_sha256=
                      platform_ad_funding_observations.source_sha256
            ), 1),
            is_current=COALESCE((
                SELECT is_current FROM platform_fee_statements AS statement
                WHERE statement.source_sha256=
                      platform_ad_funding_observations.source_sha256
            ), 0);
        """,
    ),
    (
        24,
        """
        ALTER TABLE product_costs
        ADD COLUMN refund_data_status TEXT NOT NULL DEFAULT 'pending_data'
            CHECK(refund_data_status IN ('observed', 'pending_data'));
        ALTER TABLE product_costs
        ADD COLUMN input_source_name TEXT NOT NULL DEFAULT '';

        CREATE TABLE profit_input_lineage (
            platform TEXT NOT NULL,
            store TEXT NOT NULL,
            account_id TEXT NOT NULL,
            campaign_id TEXT NOT NULL,
            sku_id TEXT NOT NULL,
            data_date TEXT NOT NULL,
            input_name TEXT NOT NULL CHECK(input_name IN (
                'gmv', 'refunds', 'commission_rate', 'product_cost',
                'ad_spend', 'seller_shipping', 'coupons',
                'allocated_fixed_cost', 'exchange_rate'
            )),
            value_text TEXT,
            status TEXT NOT NULL CHECK(status IN (
                'ready', 'carried_forward', 'pending_data', 'ambiguous',
                'not_applicable'
            )),
            source_type TEXT NOT NULL,
            source_date TEXT,
            age_days INTEGER CHECK(age_days IS NULL OR age_days >= 0),
            source_ref TEXT NOT NULL DEFAULT '',
            detail_code TEXT NOT NULL DEFAULT '',
            updated_at TEXT NOT NULL DEFAULT (
                strftime('%Y-%m-%dT%H:%M:%fZ', 'now')
            ),
            PRIMARY KEY(
                platform, store, account_id, campaign_id, sku_id,
                data_date, input_name
            )
        );

        CREATE INDEX profit_input_lineage_daily_idx
        ON profit_input_lineage(data_date, platform, store, status);

        CREATE TABLE analysis_recalculation_runs (
            id TEXT PRIMARY KEY,
            start_date TEXT NOT NULL,
            end_date TEXT NOT NULL,
            status TEXT NOT NULL CHECK(status IN (
                'running', 'succeeded', 'failed'
            )),
            days_processed INTEGER NOT NULL DEFAULT 0
                CHECK(days_processed >= 0),
            metrics_processed INTEGER NOT NULL DEFAULT 0
                CHECK(metrics_processed >= 0),
            profit_results INTEGER NOT NULL DEFAULT 0
                CHECK(profit_results >= 0),
            pending_data INTEGER NOT NULL DEFAULT 0
                CHECK(pending_data >= 0),
            report_name TEXT NOT NULL DEFAULT '',
            started_at TEXT NOT NULL,
            finished_at TEXT,
            error_code TEXT,
            CHECK(start_date <= end_date)
        );
        """,
    ),
    (
        25,
        """
        CREATE TABLE evidence_documents (
            id TEXT PRIMARY KEY,
            object_type TEXT NOT NULL,
            object_id TEXT NOT NULL,
            display_name TEXT NOT NULL,
            stored_name TEXT NOT NULL UNIQUE,
            media_type TEXT NOT NULL CHECK(media_type IN (
                'application/pdf', 'image/jpeg', 'image/png'
            )),
            size_bytes INTEGER NOT NULL
                CHECK(size_bytes > 0 AND size_bytes <= 5242880),
            sha256 TEXT NOT NULL CHECK(length(sha256) = 64),
            note TEXT NOT NULL DEFAULT '',
            created_by TEXT NOT NULL,
            created_at TEXT NOT NULL
        );

        CREATE INDEX evidence_documents_object_idx
        ON evidence_documents(object_type, object_id, created_at);

        CREATE UNIQUE INDEX evidence_documents_content_idx
        ON evidence_documents(object_type, object_id, sha256);

        CREATE TABLE financial_adjustments (
            id TEXT PRIMARY KEY,
            adjustment_type TEXT NOT NULL CHECK(adjustment_type IN (
                'income', 'cash', 'balance'
            )),
            amount_cny TEXT NOT NULL CHECK(length(trim(amount_cny)) > 0),
            occurred_on TEXT NOT NULL,
            reason TEXT NOT NULL CHECK(length(trim(reason)) > 0),
            status TEXT NOT NULL CHECK(status IN ('confirmed', 'reversed')),
            reversal_of TEXT REFERENCES financial_adjustments(id),
            created_by TEXT NOT NULL,
            created_at TEXT NOT NULL
        );

        CREATE UNIQUE INDEX financial_adjustments_single_reversal_idx
        ON financial_adjustments(reversal_of)
        WHERE reversal_of IS NOT NULL;

        CREATE TABLE balance_snapshots (
            id TEXT PRIMARY KEY,
            account_type TEXT NOT NULL CHECK(account_type IN (
                'cash', 'platform', 'bank'
            )),
            account_name TEXT NOT NULL,
            platform TEXT NOT NULL DEFAULT '',
            store TEXT NOT NULL DEFAULT '',
            snapshot_date TEXT NOT NULL,
            amount_cny TEXT NOT NULL CHECK(length(trim(amount_cny)) > 0),
            source TEXT NOT NULL,
            note TEXT NOT NULL DEFAULT '',
            created_by TEXT NOT NULL,
            created_at TEXT NOT NULL
        );

        CREATE INDEX balance_snapshots_lookup_idx
        ON balance_snapshots(
            snapshot_date, account_type, platform, store, created_at
        );

        CREATE TABLE balance_reconciliations (
            id TEXT PRIMARY KEY,
            data_date TEXT NOT NULL,
            ledger_cash_cny TEXT NOT NULL,
            cash_observed_cny TEXT,
            platform_observed_cny TEXT,
            bank_observed_cny TEXT,
            cash_difference_cny TEXT,
            platform_difference_cny TEXT,
            bank_difference_cny TEXT,
            status TEXT NOT NULL CHECK(status IN (
                'pending_data', 'open', 'resolved'
            )),
            resolution_adjustment_id TEXT
                REFERENCES financial_adjustments(id),
            supersedes_id TEXT REFERENCES balance_reconciliations(id),
            created_by TEXT NOT NULL,
            created_at TEXT NOT NULL,
            resolved_at TEXT
        );

        CREATE INDEX balance_reconciliations_date_idx
        ON balance_reconciliations(data_date, created_at);

        CREATE UNIQUE INDEX balance_reconciliations_single_resolution_idx
        ON balance_reconciliations(supersedes_id)
        WHERE supersedes_id IS NOT NULL;

        CREATE UNIQUE INDEX balance_reconciliations_adjustment_idx
        ON balance_reconciliations(resolution_adjustment_id)
        WHERE resolution_adjustment_id IS NOT NULL;

        ALTER TABLE inventory_movements
        ADD COLUMN actor TEXT NOT NULL DEFAULT 'system';
        ALTER TABLE inventory_movements
        ADD COLUMN reason TEXT NOT NULL DEFAULT '';
        ALTER TABLE inventory_movements
        ADD COLUMN reversal_of TEXT REFERENCES inventory_movements(id);

        CREATE UNIQUE INDEX inventory_movements_single_reversal_idx
        ON inventory_movements(reversal_of)
        WHERE reversal_of IS NOT NULL;

        ALTER TABLE profit_payments
        ADD COLUMN created_by TEXT NOT NULL DEFAULT 'system';
        ALTER TABLE profit_payments
        ADD COLUMN reversal_reason TEXT;
        ALTER TABLE profit_payments
        ADD COLUMN reversal_of TEXT REFERENCES profit_payments(id);

        CREATE UNIQUE INDEX profit_payments_single_reversal_idx
        ON profit_payments(reversal_of)
        WHERE reversal_of IS NOT NULL;

        ALTER TABLE capital_entries
        ADD COLUMN created_by TEXT NOT NULL DEFAULT 'system';
        ALTER TABLE capital_entries ADD COLUMN reversal_reason TEXT;

        ALTER TABLE withdrawal_entries
        ADD COLUMN created_by TEXT NOT NULL DEFAULT 'system';
        ALTER TABLE withdrawal_entries ADD COLUMN reversal_reason TEXT;

        ALTER TABLE ad_funding_entries
        ADD COLUMN created_by TEXT NOT NULL DEFAULT 'system';
        ALTER TABLE ad_funding_entries ADD COLUMN reversal_reason TEXT;

        ALTER TABLE review_order_costs
        ADD COLUMN created_by TEXT NOT NULL DEFAULT 'system';
        ALTER TABLE review_order_costs ADD COLUMN reversal_reason TEXT;

        ALTER TABLE purchase_receipts ADD COLUMN reversal_reason TEXT;
        """,
    ),
    (
        26,
        """
        CREATE TABLE ad_reconciliation_log (
            id INTEGER PRIMARY KEY,
            platform TEXT NOT NULL,
            store TEXT NOT NULL,
            account_id TEXT NOT NULL,
            data_date TEXT NOT NULL,
            overview_spend TEXT NOT NULL,
            detail_spend TEXT NOT NULL,
            difference TEXT NOT NULL,
            threshold TEXT NOT NULL,
            status TEXT NOT NULL CHECK(
                status IN ('matched', 'exceeded', 'no_overview')
            ),
            created_at TEXT NOT NULL
        );
        """,
    ),
    (
        27,
        """
        CREATE TABLE experiments (
            id TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            description TEXT NOT NULL DEFAULT '',
            platform TEXT NOT NULL,
            store TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'draft'
                CHECK(status IN (
                    'draft','running','paused',
                    'completed','reverted','cancelled'
                )),
            starts_on TEXT NOT NULL,
            ends_on TEXT NOT NULL,
            guardrail_config_json TEXT NOT NULL DEFAULT '{}',
            created_by TEXT NOT NULL,
            created_at TEXT NOT NULL,
            notes TEXT NOT NULL DEFAULT ''
        );

        CREATE TABLE experiment_variants (
            id TEXT PRIMARY KEY,
            experiment_id TEXT NOT NULL REFERENCES experiments(id),
            variant_type TEXT NOT NULL
                CHECK(variant_type IN ('control','treatment')),
            name TEXT NOT NULL,
            strategy_params_json TEXT NOT NULL DEFAULT '{}',
            traffic_percent REAL NOT NULL DEFAULT 50.0
                CHECK(traffic_percent >= 0 AND traffic_percent <= 100),
            status TEXT NOT NULL DEFAULT 'active'
                CHECK(status IN ('active','paused','removed')),
            created_at TEXT NOT NULL
        );

        CREATE TABLE experiment_windows (
            id TEXT PRIMARY KEY,
            experiment_id TEXT NOT NULL REFERENCES experiments(id),
            data_date TEXT NOT NULL,
            variant_id TEXT NOT NULL REFERENCES experiment_variants(id),
            metrics_json TEXT NOT NULL DEFAULT '{}',
            guardrail_breaches_json TEXT NOT NULL DEFAULT '{}',
            created_at TEXT NOT NULL
        );

        CREATE TABLE experiment_guardrail_breaches (
            id TEXT PRIMARY KEY,
            experiment_id TEXT NOT NULL REFERENCES experiments(id),
            window_id TEXT REFERENCES experiment_windows(id),
            metric_name TEXT NOT NULL,
            threshold REAL NOT NULL,
            actual_value REAL NOT NULL,
            severity TEXT NOT NULL
                CHECK(severity IN ('warning','critical')),
            occurred_at TEXT NOT NULL,
            resolved INTEGER NOT NULL DEFAULT 0
        );
        """,
    ),
    (
        28,
        """
        CREATE TABLE collector_health (
            collector_name TEXT NOT NULL,
            platform TEXT NOT NULL,
            healthy INTEGER NOT NULL DEFAULT 1,
            last_checked_at TEXT NOT NULL,
            consecutive_failures INTEGER NOT NULL DEFAULT 0,
            consecutive_successes INTEGER NOT NULL DEFAULT 0,
            failover_active INTEGER NOT NULL DEFAULT 0,
            failover_started_at TEXT,
            PRIMARY KEY(collector_name, platform)
        );
        """,
    ),
    (
        29,
        """
        CREATE TABLE store_fulfillment_history (
            platform TEXT NOT NULL,
            store TEXT NOT NULL,
            effective_date TEXT NOT NULL,
            mode TEXT NOT NULL CHECK(
                mode IN ('supplier_fulfilled', 'stocked')
            ),
            supply_status TEXT NOT NULL CHECK(
                supply_status IN ('available', 'paused')
            ),
            note TEXT NOT NULL DEFAULT '',
            created_at TEXT NOT NULL,
            PRIMARY KEY(platform, store, effective_date)
        );

        CREATE INDEX store_fulfillment_effective_idx
        ON store_fulfillment_history(platform, store, effective_date);

        CREATE TABLE daily_refund_inputs (
            platform TEXT NOT NULL,
            store TEXT NOT NULL,
            data_date TEXT NOT NULL,
            refund_amount_original TEXT NOT NULL,
            currency TEXT NOT NULL,
            status TEXT NOT NULL CHECK(
                status IN ('observed', 't3_closed_zero', 'pending_data')
            ),
            source_type TEXT NOT NULL,
            source_ref TEXT NOT NULL DEFAULT '',
            observed_on TEXT,
            closed_at TEXT,
            updated_at TEXT NOT NULL,
            PRIMARY KEY(platform, store, data_date),
            CHECK(CAST(refund_amount_original AS REAL) >= 0)
        );

        CREATE INDEX daily_refund_inputs_date_idx
        ON daily_refund_inputs(data_date, platform, store, status);
        """,
    ),
    (
        30,
        """
        CREATE VIEW effective_platform_order_lines AS
        WITH ranked AS (
            SELECT
                line.*,
                SUM(
                    CASE
                        WHEN line.item_id=line.seller_sku
                         AND line.model_id=line.seller_sku
                        THEN 0 ELSE 1
                    END
                ) OVER (
                    PARTITION BY line.platform, line.store,
                                 line.order_id, line.seller_sku
                ) AS resolved_line_count,
                ROW_NUMBER() OVER (
                    PARTITION BY line.platform, line.store,
                                 line.order_id, line.seller_sku
                    ORDER BY
                        CASE
                            WHEN line.item_id=line.seller_sku
                             AND line.model_id=line.seller_sku
                            THEN 1 ELSE 0
                        END,
                        line.source_updated_at DESC,
                        line.item_id,
                        line.model_id
                ) AS source_rank
            FROM platform_order_lines AS line
        )
        SELECT
            ranked.*,
            CASE
                WHEN resolved_line_count > 1 THEN 'ambiguous_resolved'
                ELSE 'ready'
            END AS resolution_status
        FROM ranked
        WHERE source_rank=1;
        """,
    ),
    (
        31,
        """
        DROP VIEW effective_platform_order_lines;

        CREATE VIEW effective_platform_order_lines AS
        WITH ranked AS (
            SELECT
                line.*,
                COALESCE(alias.canonical_store, line.store) AS canonical_store,
                SUM(
                    CASE
                        WHEN line.item_id=line.seller_sku
                         AND line.model_id=line.seller_sku
                        THEN 0 ELSE 1
                    END
                ) OVER (
                    PARTITION BY line.platform,
                                 COALESCE(alias.canonical_store, line.store),
                                 line.order_id, line.seller_sku
                ) AS resolved_line_count,
                ROW_NUMBER() OVER (
                    PARTITION BY line.platform,
                                 COALESCE(alias.canonical_store, line.store),
                                 line.order_id, line.seller_sku
                    ORDER BY
                        CASE
                            WHEN line.item_id=line.seller_sku
                             AND line.model_id=line.seller_sku
                            THEN 1 ELSE 0
                        END,
                        line.source_updated_at DESC,
                        line.item_id,
                        line.model_id,
                        line.store
                ) AS source_rank
            FROM platform_order_lines AS line
            LEFT JOIN store_aliases AS alias
              ON alias.platform=line.platform
             AND alias.source_store=line.store
        )
        SELECT
            ranked.*,
            CASE
                WHEN resolved_line_count > 1 THEN 'ambiguous_resolved'
                ELSE 'ready'
            END AS resolution_status
        FROM ranked
        WHERE source_rank=1;
        """,
    ),
    (
        32,
        """
        DROP VIEW effective_platform_order_lines;

        CREATE VIEW effective_platform_order_lines AS
        WITH canonicalized AS (
            SELECT
                line.*,
                COALESCE(alias.canonical_store, line.store) AS canonical_store,
                CASE
                    WHEN line.item_id=line.seller_sku
                     AND line.model_id=line.seller_sku
                    THEN 0 ELSE 1
                END AS is_resolved
            FROM platform_order_lines AS line
            LEFT JOIN store_aliases AS alias
              ON alias.platform=line.platform
             AND alias.source_store=line.store
        ),
        store_preference AS (
            SELECT
                canonicalized.*,
                MAX(
                    CASE
                        WHEN store=canonical_store AND is_resolved=1
                        THEN 1 ELSE 0
                    END
                ) OVER (
                    PARTITION BY platform, canonical_store,
                                 order_id, seller_sku
                ) AS has_canonical_resolved
            FROM canonicalized
        ),
        ranked AS (
            SELECT
                store_preference.*,
                SUM(
                    CASE
                        WHEN is_resolved=1
                         AND (
                            has_canonical_resolved=0
                            OR store=canonical_store
                         )
                        THEN 1 ELSE 0
                    END
                ) OVER (
                    PARTITION BY platform, canonical_store,
                                 order_id, seller_sku
                ) AS resolved_line_count,
                ROW_NUMBER() OVER (
                    PARTITION BY platform, canonical_store,
                                 order_id, seller_sku
                    ORDER BY
                        CASE
                            WHEN has_canonical_resolved=1
                             AND store<>canonical_store
                            THEN 1 ELSE 0
                        END,
                        CASE WHEN is_resolved=1 THEN 0 ELSE 1 END,
                        source_updated_at DESC,
                        item_id,
                        model_id,
                        store
                ) AS source_rank
            FROM store_preference
        )
        SELECT
            ranked.*,
            CASE
                WHEN resolved_line_count > 1 THEN 'ambiguous_resolved'
                ELSE 'ready'
            END AS resolution_status
        FROM ranked
        WHERE source_rank=1;
        """,
    ),
    (
        33,
        """
        ALTER TABLE order_sync_runs
        ADD COLUMN product_rejected_count INTEGER NOT NULL DEFAULT 0;
        ALTER TABLE order_sync_runs
        ADD COLUMN order_rejected_count INTEGER NOT NULL DEFAULT 0;
        """,
    ),
    (
        34,
        """
        DROP VIEW effective_platform_order_lines;

        CREATE VIEW effective_platform_order_lines AS
        WITH canonicalized AS (
            SELECT
                line.*,
                COALESCE(alias.canonical_store, line.store)
                    AS canonical_store,
                CASE
                    WHEN line.item_id=line.seller_sku
                     AND line.model_id=line.seller_sku
                    THEN 1 ELSE 0
                END AS is_placeholder
            FROM platform_order_lines AS line
            LEFT JOIN store_aliases AS alias
              ON alias.platform=line.platform
             AND alias.source_store=line.store
        ),
        candidates AS (
            SELECT
                canonicalized.*,
                MAX(CASE WHEN is_placeholder=0 THEN 1 ELSE 0 END)
                    OVER (
                        PARTITION BY platform, canonical_store,
                                     order_id, seller_sku
                    ) AS has_resolved
            FROM canonicalized
        ),
        ranked AS (
            SELECT
                candidates.*,
                ROW_NUMBER() OVER (
                    PARTITION BY platform, canonical_store, order_id,
                                 seller_sku, item_id, model_id
                    ORDER BY
                        CASE WHEN store=canonical_store THEN 0 ELSE 1 END,
                        source_updated_at DESC,
                        store
                ) AS source_rank
            FROM candidates
            WHERE NOT (is_placeholder=1 AND has_resolved=1)
        )
        SELECT ranked.*, 'ready' AS resolution_status
        FROM ranked
        WHERE source_rank=1;

        CREATE VIEW effective_order_sku_lines AS
        WITH seller_scope AS (
            SELECT
                sku.platform,
                COALESCE(alias.canonical_store, sku.store)
                    AS canonical_store,
                sku.seller_sku,
                COUNT(DISTINCT sku.item_id || char(31) || sku.model_id)
                    AS identity_count
            FROM platform_sku_mappings AS sku
            LEFT JOIN store_aliases AS alias
              ON alias.platform=sku.platform
             AND alias.source_store=sku.store
            GROUP BY
                sku.platform,
                COALESCE(alias.canonical_store, sku.store),
                sku.seller_sku
        )
        SELECT
            line.*,
            CASE
                WHEN scope.identity_count > 1
                THEN line.seller_sku || ' [' || line.item_id || ':'
                     || line.model_id || ']'
                ELSE line.seller_sku
            END AS sku_identity
        FROM effective_platform_order_lines AS line
        LEFT JOIN seller_scope AS scope
          ON scope.platform=line.platform
         AND scope.canonical_store=line.canonical_store
         AND scope.seller_sku=line.seller_sku;
        """,
    ),
    (
        35,
        """
        DROP VIEW effective_order_sku_lines;

        CREATE VIEW effective_order_sku_lines AS
        WITH seller_scope AS (
            SELECT
                sku.platform,
                COALESCE(alias.canonical_store, sku.store)
                    AS canonical_store,
                sku.seller_sku,
                COUNT(DISTINCT sku.item_id || char(31) || sku.model_id)
                    AS identity_count
            FROM platform_sku_mappings AS sku
            LEFT JOIN store_aliases AS alias
              ON alias.platform=sku.platform
             AND alias.source_store=sku.store
            GROUP BY
                sku.platform,
                COALESCE(alias.canonical_store, sku.store),
                sku.seller_sku
        )
        SELECT
            line.*,
            CASE
                WHEN scope.identity_count > 1
                THEN line.seller_sku || ' [' || line.item_id || ':'
                     || line.model_id || ']'
                ELSE line.seller_sku
            END AS sku_identity
        FROM effective_platform_order_lines AS line
        LEFT JOIN seller_scope AS scope
          ON scope.platform=line.platform
         AND scope.canonical_store=line.canonical_store
         AND scope.seller_sku=line.seller_sku;
        """,
    ),
    (
        36,
        """
        DROP VIEW effective_order_sku_lines;
        DROP VIEW effective_platform_order_lines;

        CREATE VIEW effective_platform_order_lines AS
        WITH canonicalized AS (
            SELECT
                line.*,
                COALESCE(alias.canonical_store, line.store)
                    AS canonical_store,
                CASE
                    WHEN line.item_id=line.seller_sku
                     AND line.model_id=line.seller_sku
                    THEN 1 ELSE 0
                END AS is_placeholder
            FROM platform_order_lines AS line
            LEFT JOIN store_aliases AS alias
              ON alias.platform=line.platform
             AND alias.source_store=line.store
        ),
        candidates AS (
            SELECT
                canonicalized.*,
                MAX(CASE WHEN is_placeholder=0 THEN 1 ELSE 0 END)
                    OVER (
                        PARTITION BY platform, canonical_store,
                                     order_id, seller_sku
                    ) AS has_resolved
                ,MAX(
                    CASE
                        WHEN store=canonical_store AND is_placeholder=0
                        THEN 1 ELSE 0
                    END
                ) OVER (
                    PARTITION BY platform, canonical_store,
                                 order_id, seller_sku
                ) AS has_canonical_resolved
            FROM canonicalized
        ),
        filtered AS (
            SELECT * FROM candidates
            WHERE NOT (is_placeholder=1 AND has_resolved=1)
              AND NOT (
                  has_canonical_resolved=1 AND store<>canonical_store
              )
        ),
        mapping_scope AS (
            SELECT
                sku.platform,
                COALESCE(alias.canonical_store, sku.store)
                    AS canonical_store,
                sku.seller_sku,
                COUNT(DISTINCT sku.item_id || char(31) || sku.model_id)
                    AS identity_count
            FROM platform_sku_mappings AS sku
            LEFT JOIN store_aliases AS alias
              ON alias.platform=sku.platform
             AND alias.source_store=sku.store
            GROUP BY
                sku.platform,
                COALESCE(alias.canonical_store, sku.store),
                sku.seller_sku
        ),
        line_scope AS (
            SELECT
                platform, canonical_store, order_id, seller_sku,
                COUNT(DISTINCT item_id || char(31) || model_id)
                    AS line_identity_count
            FROM filtered
            GROUP BY platform, canonical_store, order_id, seller_sku
        ),
        ranked AS (
            SELECT
                filtered.*,
                COALESCE(mapping_scope.identity_count, 0)
                    AS mapped_identity_count,
                line_scope.line_identity_count,
                ROW_NUMBER() OVER (
                    PARTITION BY
                        filtered.platform, filtered.canonical_store,
                        filtered.order_id, filtered.seller_sku,
                        CASE
                            WHEN COALESCE(
                                mapping_scope.identity_count, 0
                            ) > 1
                            THEN filtered.item_id ELSE ''
                        END,
                        CASE
                            WHEN COALESCE(
                                mapping_scope.identity_count, 0
                            ) > 1
                            THEN filtered.model_id ELSE ''
                        END
                    ORDER BY
                        CASE
                            WHEN filtered.store=filtered.canonical_store
                            THEN 0 ELSE 1
                        END,
                        filtered.source_updated_at DESC,
                        filtered.item_id,
                        filtered.model_id,
                        filtered.store
                ) AS source_rank
            FROM filtered
            JOIN line_scope
              ON line_scope.platform=filtered.platform
             AND line_scope.canonical_store=filtered.canonical_store
             AND line_scope.order_id=filtered.order_id
             AND line_scope.seller_sku=filtered.seller_sku
            LEFT JOIN mapping_scope
              ON mapping_scope.platform=filtered.platform
             AND mapping_scope.canonical_store=filtered.canonical_store
             AND mapping_scope.seller_sku=filtered.seller_sku
        )
        SELECT
            ranked.*,
            CASE
                WHEN line_identity_count > 1
                 AND mapped_identity_count <= 1
                THEN 'ambiguous_resolved'
                ELSE 'ready'
            END AS resolution_status
        FROM ranked
        WHERE source_rank=1;

        CREATE VIEW effective_order_sku_lines AS
        WITH seller_scope AS (
            SELECT
                sku.platform,
                COALESCE(alias.canonical_store, sku.store)
                    AS canonical_store,
                sku.seller_sku,
                COUNT(DISTINCT sku.item_id || char(31) || sku.model_id)
                    AS identity_count
            FROM platform_sku_mappings AS sku
            LEFT JOIN store_aliases AS alias
              ON alias.platform=sku.platform
             AND alias.source_store=sku.store
            GROUP BY
                sku.platform,
                COALESCE(alias.canonical_store, sku.store),
                sku.seller_sku
        )
        SELECT
            line.*,
            CASE
                WHEN scope.identity_count > 1
                THEN line.seller_sku || ' [' || line.item_id || ':'
                     || line.model_id || ']'
                ELSE line.seller_sku
            END AS sku_identity
        FROM effective_platform_order_lines AS line
        LEFT JOIN seller_scope AS scope
          ON scope.platform=line.platform
         AND scope.canonical_store=line.canonical_store
         AND scope.seller_sku=line.seller_sku;
        """,
    ),
    (
        37,
        """
        DROP VIEW effective_order_sku_lines;

        CREATE VIEW effective_order_sku_lines AS
        WITH seller_scope AS (
            SELECT
                sku.platform,
                COALESCE(alias.canonical_store, sku.store)
                    AS canonical_store,
                sku.seller_sku,
                COUNT(DISTINCT
                    CASE
                        WHEN TRIM(COALESCE(sku.product_name, '')) <> ''
                          OR TRIM(COALESCE(sku.variation_name, '')) <> ''
                        THEN LOWER(TRIM(COALESCE(sku.product_name, '')))
                             || char(31) ||
                             LOWER(TRIM(COALESCE(sku.variation_name, '')))
                        ELSE sku.item_id || char(31) || sku.model_id
                    END
                ) AS identity_count
            FROM platform_sku_mappings AS sku
            LEFT JOIN store_aliases AS alias
              ON alias.platform=sku.platform
             AND alias.source_store=sku.store
            GROUP BY
                sku.platform,
                COALESCE(alias.canonical_store, sku.store),
                sku.seller_sku
        )
        SELECT
            line.*,
            CASE
                WHEN scope.identity_count > 1
                THEN line.seller_sku || ' [' || line.item_id || ':'
                     || line.model_id || ']'
                ELSE line.seller_sku
            END AS sku_identity
        FROM effective_platform_order_lines AS line
        LEFT JOIN seller_scope AS scope
          ON scope.platform=line.platform
         AND scope.canonical_store=line.canonical_store
         AND scope.seller_sku=line.seller_sku;
        """,
    ),
    (
        38,
        """
        DROP VIEW effective_order_sku_lines;

        CREATE VIEW effective_order_sku_lines AS
        WITH normalized AS (
            SELECT
                line.*,
                COALESCE(
                    (
                        SELECT sku.seller_sku
                        FROM platform_sku_mappings AS sku
                        LEFT JOIN store_aliases AS sku_alias
                          ON sku_alias.platform=sku.platform
                         AND sku_alias.source_store=sku.store
                        WHERE sku.platform=line.platform
                          AND COALESCE(
                              sku_alias.canonical_store, sku.store
                          )=line.canonical_store
                          AND sku.item_id=line.item_id
                          AND sku.model_id=line.model_id
                        ORDER BY
                            CASE WHEN sku.store=line.canonical_store
                                 THEN 0 ELSE 1 END,
                            sku.observed_at DESC,
                            sku.store
                        LIMIT 1
                    ),
                    CASE
                        WHEN line.seller_sku LIKE
                             '% [' || line.item_id || ':' ||
                             line.model_id || ']'
                        THEN SUBSTR(
                            line.seller_sku,
                            1,
                            LENGTH(line.seller_sku) - LENGTH(
                                ' [' || line.item_id || ':' ||
                                line.model_id || ']'
                            )
                        )
                        ELSE line.seller_sku
                    END
                ) AS raw_seller_sku
            FROM effective_platform_order_lines AS line
        ),
        seller_scope AS (
            SELECT
                sku.platform,
                COALESCE(alias.canonical_store, sku.store)
                    AS canonical_store,
                sku.seller_sku,
                COUNT(DISTINCT
                    CASE
                        WHEN TRIM(COALESCE(sku.product_name, '')) <> ''
                          OR TRIM(COALESCE(sku.variation_name, '')) <> ''
                        THEN LOWER(TRIM(COALESCE(sku.product_name, '')))
                             || char(31) ||
                             LOWER(TRIM(COALESCE(sku.variation_name, '')))
                        ELSE sku.item_id || char(31) || sku.model_id
                    END
                ) AS identity_count
            FROM platform_sku_mappings AS sku
            LEFT JOIN store_aliases AS alias
              ON alias.platform=sku.platform
             AND alias.source_store=sku.store
            GROUP BY
                sku.platform,
                COALESCE(alias.canonical_store, sku.store),
                sku.seller_sku
        )
        SELECT
            line.*,
            CASE
                WHEN scope.identity_count > 1
                THEN line.raw_seller_sku || ' [' || line.item_id || ':'
                     || line.model_id || ']'
                ELSE line.raw_seller_sku
            END AS sku_identity
        FROM normalized AS line
        LEFT JOIN seller_scope AS scope
          ON scope.platform=line.platform
         AND scope.canonical_store=line.canonical_store
         AND scope.seller_sku=line.raw_seller_sku;
        """,
    ),
    (
        39,
        """
        DROP VIEW effective_order_sku_lines;

        CREATE VIEW effective_order_sku_lines AS
        WITH mapping_ranked AS (
            SELECT
                sku.*,
                COALESCE(alias.canonical_store, sku.store)
                    AS canonical_store,
                ROW_NUMBER() OVER (
                    PARTITION BY
                        sku.platform,
                        COALESCE(alias.canonical_store, sku.store),
                        sku.item_id,
                        sku.model_id
                    ORDER BY sku.observed_at DESC, sku.store
                ) AS mapping_rank
            FROM platform_sku_mappings AS sku
            LEFT JOIN store_aliases AS alias
              ON alias.platform=sku.platform
             AND alias.source_store=sku.store
        ),
        normalized AS (
            SELECT
                line.*,
                COALESCE(
                    mapping.seller_sku,
                    CASE
                        WHEN line.seller_sku LIKE
                             '% [' || line.item_id || ':' ||
                             line.model_id || ']'
                        THEN SUBSTR(
                            line.seller_sku,
                            1,
                            LENGTH(line.seller_sku) - LENGTH(
                                ' [' || line.item_id || ':' ||
                                line.model_id || ']'
                            )
                        )
                        ELSE line.seller_sku
                    END
                ) AS raw_seller_sku
            FROM effective_platform_order_lines AS line
            LEFT JOIN mapping_ranked AS mapping
              ON mapping.platform=line.platform
             AND mapping.canonical_store=line.canonical_store
             AND mapping.item_id=line.item_id
             AND mapping.model_id=line.model_id
             AND mapping.mapping_rank=1
        ),
        seller_scope AS (
            SELECT
                sku.platform,
                COALESCE(alias.canonical_store, sku.store)
                    AS canonical_store,
                sku.seller_sku,
                COUNT(DISTINCT
                    CASE
                        WHEN TRIM(COALESCE(sku.product_name, '')) <> ''
                          OR TRIM(COALESCE(sku.variation_name, '')) <> ''
                        THEN LOWER(TRIM(COALESCE(sku.product_name, '')))
                             || char(31) ||
                             LOWER(TRIM(COALESCE(sku.variation_name, '')))
                        ELSE sku.item_id || char(31) || sku.model_id
                    END
                ) AS identity_count
            FROM platform_sku_mappings AS sku
            LEFT JOIN store_aliases AS alias
              ON alias.platform=sku.platform
             AND alias.source_store=sku.store
            GROUP BY
                sku.platform,
                COALESCE(alias.canonical_store, sku.store),
                sku.seller_sku
        )
        SELECT
            line.*,
            CASE
                WHEN scope.identity_count > 1
                THEN line.raw_seller_sku || ' [' || line.item_id || ':'
                     || line.model_id || ']'
                ELSE line.raw_seller_sku
            END AS sku_identity
        FROM normalized AS line
        LEFT JOIN seller_scope AS scope
          ON scope.platform=line.platform
         AND scope.canonical_store=line.canonical_store
         AND scope.seller_sku=line.raw_seller_sku;
        """,
    ),
    (
        40,
        """
        CREATE TABLE monthly_exchange_rate_overrides (
            currency TEXT NOT NULL,
            year_month TEXT NOT NULL CHECK(length(year_month)=7),
            rate_to_cny TEXT NOT NULL,
            source TEXT NOT NULL,
            note TEXT NOT NULL DEFAULT '',
            created_by TEXT NOT NULL,
            created_at TEXT NOT NULL,
            PRIMARY KEY(currency, year_month)
        );

        ALTER TABLE ad_funding_entries
        ADD COLUMN credited_amount_original TEXT;
        ALTER TABLE ad_funding_entries
        ADD COLUMN credited_amount_cny TEXT;
        ALTER TABLE ad_funding_entries ADD COLUMN rate_date TEXT;
        ALTER TABLE ad_funding_entries
        ADD COLUMN rate_source TEXT NOT NULL DEFAULT 'legacy';
        UPDATE ad_funding_entries
        SET credited_amount_original=amount_original,
            credited_amount_cny=amount_cny,
            rate_date=occurred_on;

        ALTER TABLE capital_entries
        ADD COLUMN entry_name TEXT NOT NULL DEFAULT '';
        ALTER TABLE capital_entries ADD COLUMN rate_date TEXT;
        ALTER TABLE capital_entries
        ADD COLUMN rate_source TEXT NOT NULL DEFAULT 'legacy';
        ALTER TABLE capital_entries
        ADD COLUMN note TEXT NOT NULL DEFAULT '';
        UPDATE capital_entries SET rate_date=occurred_on;

        ALTER TABLE review_order_costs
        ADD COLUMN goods_cost_original TEXT;
        ALTER TABLE review_order_costs
        ADD COLUMN service_fee_original TEXT;
        ALTER TABLE review_order_costs
        ADD COLUMN currency TEXT NOT NULL DEFAULT 'CNY';
        ALTER TABLE review_order_costs
        ADD COLUMN rate_to_cny TEXT NOT NULL DEFAULT '1';
        ALTER TABLE review_order_costs ADD COLUMN rate_date TEXT;
        ALTER TABLE review_order_costs
        ADD COLUMN rate_source TEXT NOT NULL DEFAULT 'legacy';
        UPDATE review_order_costs
        SET goods_cost_original=goods_cost_cny,
            service_fee_original=service_fee_cny,
            rate_date=occurred_on;

        ALTER TABLE purchase_receipts
        ADD COLUMN currency TEXT NOT NULL DEFAULT 'CNY';
        ALTER TABLE purchase_receipts
        ADD COLUMN rate_to_cny TEXT NOT NULL DEFAULT '1';
        ALTER TABLE purchase_receipts ADD COLUMN rate_date TEXT;
        ALTER TABLE purchase_receipts
        ADD COLUMN rate_source TEXT NOT NULL DEFAULT 'legacy';
        ALTER TABLE purchase_receipts
        ADD COLUMN tracking_number TEXT NOT NULL DEFAULT '';
        ALTER TABLE purchase_receipts
        ADD COLUMN arrival_status TEXT NOT NULL DEFAULT 'received';
        ALTER TABLE purchase_receipts
        ADD COLUMN note TEXT NOT NULL DEFAULT '';
        UPDATE purchase_receipts SET rate_date=received_on;

        ALTER TABLE purchase_lines
        ADD COLUMN product_name TEXT NOT NULL DEFAULT '';
        ALTER TABLE purchase_lines ADD COLUMN unit_cost_original TEXT;
        ALTER TABLE purchase_lines ADD COLUMN line_cost_original TEXT;
        UPDATE purchase_lines
        SET unit_cost_original=unit_cost_cny,
            line_cost_original=line_cost_cny;
        """,
    ),
    (
        41,
        """
        CREATE TABLE manual_income_entries (
            id TEXT PRIMARY KEY,
            platform TEXT NOT NULL DEFAULT '',
            store TEXT NOT NULL DEFAULT '',
            order_id TEXT NOT NULL DEFAULT '',
            occurred_on TEXT NOT NULL,
            amount_original TEXT NOT NULL,
            currency TEXT NOT NULL,
            rate_to_cny TEXT NOT NULL,
            amount_cny TEXT NOT NULL,
            rate_date TEXT NOT NULL,
            rate_source TEXT NOT NULL,
            status TEXT NOT NULL CHECK(status IN ('confirmed', 'reversed')),
            note TEXT NOT NULL DEFAULT '',
            created_by TEXT NOT NULL,
            created_at TEXT NOT NULL,
            reversal_reason TEXT,
            UNIQUE(
                platform, store, order_id, occurred_on,
                amount_original, currency
            )
        );
        CREATE INDEX idx_manual_income_occurred
        ON manual_income_entries(occurred_on, status);
        """,
    ),
    (
        42,
        """
        ALTER TABLE platform_logistics_observations
        ADD COLUMN tracking_number TEXT NOT NULL DEFAULT '';
        """,
    ),
]
