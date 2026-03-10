"""
SQLite persistence for the eBay listing tracker.

Stores search terms and tracks which listing IDs have already been seen
so we only notify on genuinely new listings.
"""

import sqlite3
import threading
from pathlib import Path

DB_PATH = Path(__file__).parent.parent / "tracker.db"

_local = threading.local()


def _get_conn() -> sqlite3.Connection:
    """Get a thread-local database connection."""
    if not hasattr(_local, "conn") or _local.conn is None:
        _local.conn = sqlite3.connect(str(DB_PATH))
        _local.conn.execute("PRAGMA journal_mode=WAL")
        _init_tables(_local.conn)
    return _local.conn


def _init_tables(conn: sqlite3.Connection):
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS searches (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            query TEXT NOT NULL UNIQUE COLLATE NOCASE,
            added_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE IF NOT EXISTS seen_listings (
            listing_id TEXT NOT NULL,
            query TEXT NOT NULL,
            title TEXT,
            price REAL,
            url TEXT,
            first_seen TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY (listing_id, query)
        );
    """)
    conn.commit()


def add_search(query: str) -> bool:
    """Add a search term. Returns True if newly added, False if already exists."""
    conn = _get_conn()
    try:
        conn.execute("INSERT INTO searches (query) VALUES (?)", (query,))
        conn.commit()
        return True
    except sqlite3.IntegrityError:
        return False


def remove_search(query: str) -> bool:
    """Remove a search term. Returns True if removed, False if not found."""
    conn = _get_conn()
    cur = conn.execute("DELETE FROM searches WHERE query = ?", (query,))
    conn.commit()
    if cur.rowcount > 0:
        # Clean up seen listings for this query
        conn.execute("DELETE FROM seen_listings WHERE query = ?", (query,))
        conn.commit()
        return True
    return False


def get_all_searches() -> list[str]:
    """Return all active search terms."""
    conn = _get_conn()
    rows = conn.execute("SELECT query FROM searches ORDER BY added_at").fetchall()
    return [r[0] for r in rows]


def is_listing_seen(listing_id: str, query: str) -> bool:
    """Check if we've already seen this listing for this query."""
    conn = _get_conn()
    row = conn.execute(
        "SELECT 1 FROM seen_listings WHERE listing_id = ? AND query = ?",
        (listing_id, query),
    ).fetchone()
    return row is not None


def mark_listing_seen(listing_id: str, query: str, title: str = "", price: float = 0, url: str = ""):
    """Record a listing as seen."""
    conn = _get_conn()
    conn.execute(
        "INSERT OR IGNORE INTO seen_listings (listing_id, query, title, price, url) VALUES (?, ?, ?, ?, ?)",
        (listing_id, query, title, price, url),
    )
    conn.commit()


def mark_listings_seen_bulk(listings: list[dict], query: str):
    """Record multiple listings as seen in one transaction."""
    conn = _get_conn()
    conn.executemany(
        "INSERT OR IGNORE INTO seen_listings (listing_id, query, title, price, url) VALUES (?, ?, ?, ?, ?)",
        [(l["id"], query, l.get("title", ""), l.get("price", 0), l.get("url", "")) for l in listings],
    )
    conn.commit()


def cleanup_old_seen(days: int = 30):
    """Remove seen listings older than N days to keep the DB small."""
    conn = _get_conn()
    conn.execute(
        "DELETE FROM seen_listings WHERE first_seen < datetime('now', ?)",
        (f"-{days} days",),
    )
    conn.commit()
