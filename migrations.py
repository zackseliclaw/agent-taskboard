"""One-time database upgrades."""

import sqlite3
from datetime import datetime, timezone
from pathlib import Path


def migrate_ambiguity(connection: sqlite3.Connection, db_path: Path) -> None:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    backup_path = db_path.with_name(f"{db_path.stem}.backup-{stamp}.db")
    with sqlite3.connect(backup_path) as backup:
        connection.backup(backup)
    connection.execute("BEGIN IMMEDIATE")
    try:
        connection.execute(
            "ALTER TABLE tasks ADD COLUMN ambiguity TEXT "
            "CHECK(ambiguity IN ('low', 'medium', 'high', 'very_high'))"
        )
        columns = {row[1] for row in connection.execute("PRAGMA table_info(tasks)")}
        for column in ("priority", "archived"):
            if column in columns:
                connection.execute(f"ALTER TABLE tasks DROP COLUMN {column}")
        if connection.execute("PRAGMA foreign_key_check").fetchall():
            raise RuntimeError("Database foreign-key check failed during migration")
        connection.execute("PRAGMA user_version = 4")
        connection.commit()
    except Exception:
        connection.rollback()
        raise
