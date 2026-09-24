import sqlite3
from contextlib import closing
from pathlib import Path

from . import config

# Statuses that are retried on later runs, up to config.MAX_ATTEMPTS.
RETRYABLE_STATUSES = ("not_found", "download_failed", "file_not_found")


def _connect():
    Path(config.STATE_DB_PATH).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(config.STATE_DB_PATH)
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS seen_tracks (
            spotify_id TEXT PRIMARY KEY,
            title TEXT,
            artist TEXT,
            status TEXT,
            processed_at TEXT DEFAULT (datetime('now')),
            attempts INTEGER NOT NULL DEFAULT 1
        )
        """
    )
    columns = {row[1] for row in conn.execute("PRAGMA table_info(seen_tracks)")}
    if "attempts" not in columns:  # databases created before retries existed
        conn.execute("ALTER TABLE seen_tracks ADD COLUMN attempts INTEGER NOT NULL DEFAULT 1")
        conn.commit()
    return conn


def get_seen_ids() -> set:
    with closing(_connect()) as conn:
        return {row[0] for row in conn.execute("SELECT spotify_id FROM seen_tracks")}


def get_retryable_ids(max_attempts: int) -> list[str]:
    placeholders = ",".join("?" * len(RETRYABLE_STATUSES))
    with closing(_connect()) as conn:
        rows = conn.execute(
            f"SELECT spotify_id FROM seen_tracks WHERE status IN ({placeholders}) "
            "AND attempts < ? ORDER BY processed_at",
            (*RETRYABLE_STATUSES, max_attempts),
        )
        return [row[0] for row in rows]


def mark_many(rows: list[tuple[str, str, str, str]]) -> None:
    """Inserts (spotify_id, title, artist, status) rows, leaving existing ones alone."""
    with closing(_connect()) as conn:
        conn.executemany(
            "INSERT OR IGNORE INTO seen_tracks (spotify_id, title, artist, status) VALUES (?, ?, ?, ?)",
            rows,
        )
        conn.commit()


def mark_processed(spotify_id: str, title: str, artist: str, status: str) -> None:
    with closing(_connect()) as conn:
        conn.execute(
            """
            INSERT INTO seen_tracks (spotify_id, title, artist, status)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(spotify_id) DO UPDATE SET
                status = excluded.status,
                attempts = seen_tracks.attempts + 1,
                processed_at = datetime('now')
            """,
            (spotify_id, title, artist, status),
        )
        conn.commit()
