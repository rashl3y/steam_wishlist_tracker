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

import sqlite3
from pathlib import Path
from datetime import datetime

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
    conn.commit()
    conn.close()


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
    now = datetime.now(datetime.timezone.utc).isoformat()

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
    Main report: cheapest current price, historic low, and bundle count per game.
    Sorted by discount descending, then price ascending.
    """
    conn = get_connection()
    rows = conn.execute("""
        WITH cheapest AS (
            SELECT
                p.app_id,
                MIN(p.price_current)  AS best_price,
                p.currency,
                p.store               AS best_store,
                p.url                 AS best_url,
                p.discount_pct        AS best_discount
            FROM prices p
            -- Exclude "Historic Low" metadata entries — they're for reference only
            WHERE p.store NOT LIKE 'Historic Low%'
            INNER JOIN (
                SELECT app_id, MIN(price_current) AS min_price
                FROM prices
                WHERE store NOT LIKE 'Historic Low%'
                GROUP BY app_id
            ) sub ON p.app_id = sub.app_id AND p.price_current = sub.min_price
            GROUP BY p.app_id
        ),
        historic AS (
            SELECT
                app_id,
                MIN(price) AS historic_low,
                store      AS historic_low_store
            FROM price_history
            GROUP BY app_id
        ),
        bundle_count AS (
            SELECT app_id, COUNT(*) AS num_bundles
            FROM bundles GROUP BY app_id
        )
        SELECT
            g.app_id,
            g.title,
            g.steam_url,
            g.header_image,
            g.last_checked,
            c.best_price,
            c.best_store,
            c.best_url,
            c.best_discount,
            c.currency,
            h.historic_low,
            h.historic_low_store,
            COALESCE(bc.num_bundles, 0) AS num_bundles
        FROM games g
        LEFT JOIN cheapest      c  ON g.app_id = c.app_id
        LEFT JOIN historic      h  ON g.app_id = h.app_id
        LEFT JOIN bundle_count  bc ON g.app_id = bc.app_id
        ORDER BY c.best_discount DESC NULLS LAST, c.best_price ASC NULLS LAST
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
    """Full price history for a single game — for chart rendering."""
    conn = get_connection()
    rows = conn.execute("""
        SELECT store, price, currency, discount_pct, recorded_at
        FROM price_history WHERE app_id = ?
        ORDER BY recorded_at ASC
    """, (app_id,)).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_game_bundles(app_id: int) -> list[dict]:
    conn = get_connection()
    rows = conn.execute("""
        SELECT bundle_title, store, tier_price, currency, bundle_url, expires_at, discovered_at
        FROM bundles WHERE app_id = ?
        ORDER BY discovered_at DESC
    """, (app_id,)).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_game_by_id(app_id: int) -> dict | None:
    conn = get_connection()
    row = conn.execute("SELECT * FROM games WHERE app_id = ?", (app_id,)).fetchone()
    conn.close()
    return dict(row) if row else None


def get_stats() -> dict:
    """Summary stats for the dashboard header."""
    conn = get_connection()
    total_games = conn.execute("SELECT COUNT(*) FROM games").fetchone()[0]
    on_sale     = conn.execute("SELECT COUNT(DISTINCT app_id) FROM prices WHERE discount_pct > 0").fetchone()[0]
    total_prices = conn.execute("SELECT COUNT(*) FROM prices").fetchone()[0]
    total_bundles = conn.execute("SELECT COUNT(*) FROM bundles").fetchone()[0]
    conn.close()
    return {
        "total_games": total_games,
        "on_sale": on_sale,
        "total_prices": total_prices,
        "total_bundles": total_bundles,
    }
