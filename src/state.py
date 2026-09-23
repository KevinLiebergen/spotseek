import sqlite3
from contextlib import closing
from pathlib import Path

from . import config


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
            processed_at TEXT DEFAULT (datetime('now'))
        )
        """
    )
    return conn


def get_seen_ids() -> set:
    with closing(_connect()) as conn:
        return {row[0] for row in conn.execute("SELECT spotify_id FROM seen_tracks")}


def mark_processed(spotify_id: str, title: str, artist: str, status: str) -> None:
    with closing(_connect()) as conn:
        conn.execute(
            """
            INSERT INTO seen_tracks (spotify_id, title, artist, status)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(spotify_id) DO UPDATE SET
                status = excluded.status,
                processed_at = datetime('now')
            """,
            (spotify_id, title, artist, status),
        )
        conn.commit()
