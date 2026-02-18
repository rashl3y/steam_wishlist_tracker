"""
database.py
-----------
SQLite schema and all database operations.
All prices stored and displayed in GBP (£).

Tables:
  games         → wishlist items from Steam
  prices        → latest price per game per store
  price_history → full append-only price log (for historic lows)
  bundles       → bundle appearances (Humble, Fanatical, etc.)
"""

from datetime import datetime, timezone
import sqlite3
import os
from pathlib import Path

DB_PATH = Path(__file__).parent.parent / "data" / "wishlist.db"


def get_connection() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_db() -> None:
    """Create all tables if they don't already exist. Safe to call on every startup."""
    conn = get_connection()
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS games (
            app_id          INTEGER PRIMARY KEY,
            title           TEXT    NOT NULL,
            added_on        TEXT    DEFAULT (datetime('now')),
            steam_url       TEXT,
            header_image    TEXT,
            itad_slug       TEXT,
            last_checked    TEXT
        );

        -- One row per game+store, overwritten each sync
        CREATE TABLE IF NOT EXISTS prices (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            app_id          INTEGER NOT NULL REFERENCES games(app_id) ON DELETE CASCADE,
            store           TEXT    NOT NULL,
            price_current   REAL,
            price_regular   REAL,
            currency        TEXT    DEFAULT 'GBP',
            discount_pct    INTEGER DEFAULT 0,
            url             TEXT,
            fetched_at      TEXT    DEFAULT (datetime('now')),
            UNIQUE(app_id, store)
        );

        -- Append-only log — never deleted, used for historic low calculation
        CREATE TABLE IF NOT EXISTS price_history (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            app_id          INTEGER NOT NULL REFERENCES games(app_id) ON DELETE CASCADE,
            store           TEXT    NOT NULL,
            price           REAL    NOT NULL,
            currency        TEXT    DEFAULT 'GBP',
            discount_pct    INTEGER DEFAULT 0,
            recorded_at     TEXT    DEFAULT (datetime('now'))
        );

        CREATE TABLE IF NOT EXISTS bundles (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            app_id          INTEGER NOT NULL REFERENCES games(app_id) ON DELETE CASCADE,
            bundle_title    TEXT    NOT NULL,
            store           TEXT,
            tier_price      REAL,
            currency        TEXT    DEFAULT 'GBP',
            bundle_url      TEXT,
            expires_at      TEXT,
            discovered_at   TEXT    DEFAULT (datetime('now'))
        );

        CREATE INDEX IF NOT EXISTS idx_prices_app    ON prices(app_id);
        CREATE INDEX IF NOT EXISTS idx_history_app   ON price_history(app_id);
        CREATE INDEX IF NOT EXISTS idx_bundles_app   ON bundles(app_id);
    """)

    # Historic lows table — tracks all-time lowest prices
    conn.execute("""
        CREATE TABLE IF NOT EXISTS historic_lows (
            id INTEGER PRIMARY KEY,
            app_id INTEGER NOT NULL,
            store TEXT NOT NULL,
            price REAL NOT NULL,
            currency TEXT DEFAULT 'GBP',
            discount_pct INTEGER DEFAULT 0,
            recorded_at TEXT,
            fetched_at TEXT,
            UNIQUE(app_id, store)
        )
    """)

    conn.commit()
    conn.close()


def clear_database() -> None:
    """Drop all tables and reinitialize (for schema changes)."""
    conn = get_connection()
    conn.execute("DROP TABLE IF EXISTS price_history")
    conn.execute("DROP TABLE IF EXISTS historic_lows")
    conn.execute("DROP TABLE IF EXISTS prices")
    conn.execute("DROP TABLE IF EXISTS bundles")
    conn.execute("DROP TABLE IF EXISTS games")
    conn.commit()
    conn.close()
    init_db()


# ── GAME CRUD ──────────────────────────────────────────────────────────────────

def upsert_game(app_id: int, title: str, steam_url: str = None, header_image: str = None) -> None:
    conn = get_connection()
    conn.execute("""
        INSERT INTO games (app_id, title, steam_url, header_image)
        VALUES (?, ?, ?, ?)
        ON CONFLICT(app_id) DO UPDATE SET
            title        = excluded.title,
            steam_url    = excluded.steam_url,
            header_image = excluded.header_image
    """, (app_id, title, steam_url, header_image))
    conn.commit()
    conn.close()


def update_itad_slug(app_id: int, slug: str) -> None:
    conn = get_connection()
    conn.execute("UPDATE games SET itad_slug = ? WHERE app_id = ?", (slug, app_id))
    conn.commit()
    conn.close()


def mark_game_checked(app_id: int) -> None:
    conn = get_connection()
    conn.execute("UPDATE games SET last_checked = datetime('now') WHERE app_id = ?", (app_id,))
    conn.commit()
    conn.close()


def get_all_games() -> list[dict]:
    conn = get_connection()
    rows = conn.execute("SELECT * FROM games ORDER BY title").fetchall()
    conn.close()
    return [dict(r) for r in rows]


def delete_game(app_id: int) -> None:
    """Remove a game and all its associated price/bundle data (cascade)."""
    conn = get_connection()
    conn.execute("DELETE FROM games WHERE app_id = ?", (app_id,))
    conn.commit()
    conn.close()


# ── PRICE CRUD ─────────────────────────────────────────────────────────────────

def upsert_price(app_id: int, store: str, price_current: float,
                 price_regular: float, currency: str = "GBP",
                 discount_pct: int = 0, url: str = None) -> None:
    """
    Save latest price AND log to history.
    currency should always be 'GBP' — conversion happens before this call.
    """
    conn = get_connection()
    now = datetime.now(timezone.utc).isoformat()

    conn.execute("""
        INSERT INTO prices (app_id, store, price_current, price_regular, currency, discount_pct, url, fetched_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(app_id, store) DO UPDATE SET
            price_current = excluded.price_current,
            price_regular = excluded.price_regular,
            currency      = excluded.currency,
            discount_pct  = excluded.discount_pct,
            url           = excluded.url,
            fetched_at    = excluded.fetched_at
    """, (app_id, store, price_current, price_regular, currency, discount_pct, url, now))

    conn.execute("""
        INSERT INTO price_history (app_id, store, price, currency, discount_pct, recorded_at)
        VALUES (?, ?, ?, ?, ?, ?)
    """, (app_id, store, price_current, currency, discount_pct, now))

    conn.commit()
    conn.close()


def upsert_historic_low(app_id: int, store: str, price: float, 
                        currency: str = "GBP", discount_pct: int = 0, 
                        recorded_date: str = None) -> None:
    """
    Save historic low price separately from current prices.
    This tracks the all-time lowest price seen for a game.
    """
    conn = get_connection()
    now = datetime.now(timezone.utc).isoformat()

    # First, check if this app_id + store combination exists
    existing = conn.execute(
        "SELECT price FROM historic_lows WHERE app_id = ? AND store = ?",
        (app_id, store)
    ).fetchone()

    if existing:
        # Only update if the new price is lower
        existing_price = existing[0]
        if price < existing_price:
            conn.execute("""
                UPDATE historic_lows 
                SET price = ?, discount_pct = ?, recorded_at = ?, fetched_at = ?
                WHERE app_id = ? AND store = ?
            """, (price, discount_pct, recorded_date, now, app_id, store))
    else:
        # Insert new record
        conn.execute("""
            INSERT INTO historic_lows (app_id, store, price, currency, discount_pct, recorded_at, fetched_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        """, (app_id, store, price, currency, discount_pct, recorded_date, now))

    conn.commit()
    conn.close()


def upsert_bundle(app_id: int, bundle_title: str, store: str = None,
                  tier_price: float = None, currency: str = "GBP",
                  bundle_url: str = None, expires_at: str = None) -> None:
    conn = get_connection()
    conn.execute("""
        INSERT OR IGNORE INTO bundles
            (app_id, bundle_title, store, tier_price, currency, bundle_url, expires_at)
        VALUES (?, ?, ?, ?, ?, ?, ?)
    """, (app_id, bundle_title, store, tier_price, currency, bundle_url, expires_at))
    conn.commit()
    conn.close()


# ── REPORTS ────────────────────────────────────────────────────────────────────

def get_deals_report() -> list[dict]:
    """
    Get all games with their best current deal + historic low.
    Sorted by discount (desc) then price (asc).
    """
    conn = get_connection()
    rows = conn.execute("""
        WITH best_prices AS (
            SELECT 
                app_id,
                store,
                price_current,
                currency,
                discount_pct,
                ROW_NUMBER() OVER (PARTITION BY app_id ORDER BY discount_pct DESC, price_current ASC) as rn
            FROM prices
            WHERE price_current IS NOT NULL
        ),
        lowest_recorded AS (
            SELECT 
                app_id,
                MIN(price) as lowest_price
            FROM historic_lows
            WHERE price IS NOT NULL
            GROUP BY app_id
        )
        SELECT 
            g.app_id,
            g.title,
            bp.store AS best_store,
            bp.price_current AS best_price,
            bp.currency,
            bp.discount_pct AS best_discount,
            lr.lowest_price AS historic_low
        FROM games g
        LEFT JOIN best_prices bp ON g.app_id = bp.app_id AND bp.rn = 1
        LEFT JOIN lowest_recorded lr ON g.app_id = lr.app_id
        WHERE bp.app_id IS NOT NULL
        ORDER BY 
            CAST(COALESCE(bp.discount_pct, 0) AS INTEGER) DESC,
            bp.price_current ASC
    """).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_all_prices_for_game(app_id: int) -> list[dict]:
    """All current store prices for a single game."""
    conn = get_connection()
    rows = conn.execute("""
        SELECT store, price_current, price_regular, currency, discount_pct, url, fetched_at
        FROM prices WHERE app_id = ?
        ORDER BY price_current ASC
    """, (app_id,)).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_game_price_history(app_id: int) -> list[dict]:
    """Get price history for a specific game."""
    conn = get_connection()
    rows = conn.execute(
        "SELECT * FROM price_history WHERE app_id = ? ORDER BY recorded_at DESC",
        (app_id,)
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_game_bundles(app_id: int) -> list[dict]:
    """Get bundle history for a specific game."""
    conn = get_connection()
    rows = conn.execute(
        "SELECT * FROM bundles WHERE app_id = ? ORDER BY expires_at DESC",
        (app_id,)
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_game_by_id(app_id: int) -> dict | None:
    conn = get_connection()
    row = conn.execute("SELECT * FROM games WHERE app_id = ?", (app_id,)).fetchone()
    conn.close()
    return dict(row) if row else None


def get_stats() -> dict:
    """Get summary statistics for the database."""
    conn = get_connection()
    
    total_games = conn.execute("SELECT COUNT(*) FROM games").fetchone()[0]
    total_bundles = conn.execute("SELECT COUNT(*) FROM bundles").fetchone()[0]
    games_with_prices = conn.execute(
        "SELECT COUNT(DISTINCT app_id) FROM prices"
    ).fetchone()[0]
    
    conn.close()
    
    return {
        "total_games": total_games,
        "total_bundles": total_bundles,
        "games_with_prices": games_with_prices,
    }
