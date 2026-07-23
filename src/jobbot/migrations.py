from __future__ import annotations

import sqlite3

PHASE_15_MIGRATION = "001_phase_1_5_profile_verification"


def record_phase_15_migration(connection: sqlite3.Connection) -> None:
    """Record the idempotent Phase I.5 schema upgrade after its DDL succeeds."""
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS schema_migrations (
            version TEXT PRIMARY KEY,
            applied_at TEXT NOT NULL
        )
        """
    )
    connection.execute(
        """
        INSERT OR IGNORE INTO schema_migrations (version, applied_at)
        VALUES (?, datetime('now'))
        """,
        (PHASE_15_MIGRATION,),
    )
