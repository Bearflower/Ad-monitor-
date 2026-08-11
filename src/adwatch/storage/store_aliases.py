from __future__ import annotations


def equivalent_store_names(
    connection, *, platform: str, store: str
) -> tuple[str, ...]:
    """Return the exact store followed by its configured source/canonical aliases."""
    rows = connection.execute(
        """
        SELECT source_store, canonical_store
        FROM store_aliases
        WHERE platform=? AND (source_store=? OR canonical_store=?)
        ORDER BY source_store, canonical_store
        """,
        (platform.lower(), store, store),
    ).fetchall()
    names = [store]
    for row in rows:
        names.extend((row["source_store"], row["canonical_store"]))
    return tuple(dict.fromkeys(names))
