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
]
