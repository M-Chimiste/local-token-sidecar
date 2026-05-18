"""
Background sync helpers for uploading local SQLite outbox rows to Postgres.
"""

from __future__ import annotations

import db as _db
import postgres_store


def flush_once(
    *,
    db_path: str,
    postgres_dsn: str,
    batch_size: int,
    node_id: str,
) -> int:
    """
    Upload one batch of local outbox rows to Postgres.

    Returns the number of local rows deleted after Postgres acknowledged the
    batch. Raises on central write failure so the async caller can back off.
    """
    rows = _db.get_unsynced_token_usage(
        db_path=db_path,
        limit=batch_size,
        node_id=node_id,
    )
    if not rows:
        return 0

    event_ids = [str(row["event_id"]) for row in rows]
    try:
        acknowledged = postgres_store.insert_token_usage_batch(postgres_dsn, rows)
    except Exception as exc:
        _db.mark_token_usage_sync_failed(db_path, event_ids, str(exc))
        raise

    return _db.mark_token_usage_synced(db_path, acknowledged)
