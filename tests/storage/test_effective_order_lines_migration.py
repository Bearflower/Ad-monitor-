from adwatch.storage.db import Database


def _order_line(
    *,
    item_id: str,
    model_id: str,
    updated_at: str,
) -> tuple:
    return (
        "shopee",
        "shop",
        "ORDER-1",
        item_id,
        model_id,
        "SKU-1",
        "Blue",
        "Product",
        1,
        "20",
        "THB",
        "completed",
        "delivered",
        "",
        "2026-07-01",
        updated_at,
    )


def test_effective_order_lines_prefers_resolved_row_over_legacy_placeholder(
    tmp_path,
):
    database = Database(tmp_path / "effective-lines.sqlite3")
    database.migrate()
    with database.transaction() as connection:
        connection.executemany(
            """
            INSERT INTO platform_order_lines VALUES(
                ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?
            )
            """,
            (
                _order_line(
                    item_id="SKU-1",
                    model_id="SKU-1",
                    updated_at="2026-07-01T00:00:00Z",
                ),
                _order_line(
                    item_id="item-1",
                    model_id="model-1",
                    updated_at="2026-07-02T00:00:00Z",
                ),
            ),
        )

    with database.connect() as connection:
        row = connection.execute(
            """
            SELECT item_id, model_id, quantity, resolution_status
            FROM effective_platform_order_lines
            """
        ).fetchone()

    assert tuple(row) == ("item-1", "model-1", 1, "ready")


def test_effective_order_lines_marks_multiple_resolved_rows_ambiguous(tmp_path):
    database = Database(tmp_path / "ambiguous-effective-lines.sqlite3")
    database.migrate()
    with database.transaction() as connection:
        connection.executemany(
            """
            INSERT INTO platform_order_lines VALUES(
                ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?
            )
            """,
            (
                _order_line(
                    item_id="item-1",
                    model_id="model-1",
                    updated_at="2026-07-01T00:00:00Z",
                ),
                _order_line(
                    item_id="item-2",
                    model_id="model-2",
                    updated_at="2026-07-02T00:00:00Z",
                ),
            ),
        )

    with database.connect() as connection:
        row = connection.execute(
            """
            SELECT item_id, model_id, resolution_status
            FROM effective_platform_order_lines
            """
        ).fetchone()

    assert tuple(row) == ("item-2", "model-2", "ambiguous_resolved")


def test_effective_order_lines_deduplicate_equivalent_store_aliases(tmp_path):
    database = Database(tmp_path / "alias-effective-lines.sqlite3")
    database.migrate()
    with database.transaction() as connection:
        connection.execute(
            """
            INSERT INTO store_aliases(platform, source_store, canonical_store)
            VALUES ('shopee', 'source-shop', 'shop')
            """
        )
        connection.executemany(
            """
            INSERT INTO platform_order_lines VALUES(
                ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?
            )
            """,
            (
                (
                    "shopee", "source-shop", *_order_line(
                        item_id="SKU-1",
                        model_id="SKU-1",
                        updated_at="2026-07-01T00:00:00Z",
                    )[2:]
                ),
                _order_line(
                    item_id="item-1",
                    model_id="model-1",
                    updated_at="2026-07-02T00:00:00Z",
                ),
            ),
        )

    with database.connect() as connection:
        rows = connection.execute(
            """
            SELECT store, item_id, model_id, canonical_store, resolution_status
            FROM effective_platform_order_lines
            """
        ).fetchall()

    assert [tuple(row) for row in rows] == [
        ("shop", "item-1", "model-1", "shop", "ready")
    ]


def test_effective_order_lines_prefer_canonical_store_identity_over_alias_history(
    tmp_path,
):
    database = Database(tmp_path / "canonical-store-effective-lines.sqlite3")
    database.migrate()
    with database.transaction() as connection:
        connection.execute(
            """
            INSERT INTO store_aliases(platform, source_store, canonical_store)
            VALUES ('shopee', 'source-shop', 'shop')
            """
        )
        connection.executemany(
            """
            INSERT INTO platform_order_lines VALUES(
                ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?
            )
            """,
            (
                (
                    "shopee", "source-shop", *_order_line(
                        item_id="item-from-legacy-store",
                        model_id="model-from-legacy-store",
                        updated_at="2026-07-01T00:00:00Z",
                    )[2:]
                ),
                _order_line(
                    item_id="item-from-canonical-store",
                    model_id="model-from-canonical-store",
                    updated_at="2026-07-02T00:00:00Z",
                ),
            ),
        )

    with database.connect() as connection:
        row = connection.execute(
            """
            SELECT store, item_id, model_id, canonical_store, resolution_status
            FROM effective_platform_order_lines
            """
        ).fetchone()

    assert tuple(row) == (
        "shop",
        "item-from-canonical-store",
        "model-from-canonical-store",
        "shop",
        "ready",
    )
