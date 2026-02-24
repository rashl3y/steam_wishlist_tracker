"""
app.py
------
Flask web application for the Steam Wishlist Price Tracker.

Run with: python app.py
Then open: http://localhost:8080

Routes:
  GET  /                     dashboard (deals table)
  GET  /game/<app_id>        game detail page
  POST /api/sync/steam       trigger Steam wishlist sync
  POST /api/sync/itad        trigger ITAD price sync
  GET  /api/games            JSON: all games + deals
  GET  /api/game/<app_id>    JSON: game detail with prices + bundles + history
  GET  /api/stats            JSON: summary stats
  DELETE /api/game/<app_id>  remove a game from DB
"""

import os
import sys
import threading
from pathlib import Path

# Load .env file if present
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

# Flask
from flask import Flask, render_template, jsonify, request, redirect, url_for

# Add src/ to path
sys.path.insert(0, str(Path(__file__).parent / "src"))

from database import (
    init_db, get_deals_report, get_game_by_id,
    get_all_prices_for_game, get_game_price_history,
    get_game_bundles, get_stats, get_all_games, delete_game, get_connection
)
from steam import sync_wishlist
from itad import sync_prices
from sync_loaded_helper import sync_loaded

app = Flask(__name__)

# Sync state (simple in-memory flag for showing progress in UI)
sync_status = {
    "running": False,
    "step": "",
    "error": None,
    "done": False,
}


def run_full_sync(steam_id: str, steam_key: str, itad_key: str):
    """Run the full sync in a background thread so the UI doesn't block."""
    global sync_status
    sync_status = {"running": True, "step": "Steam wishlist", "error": None, "done": False}

    try:
        sync_wishlist(steam_id, steam_key)

        if itad_key:
            sync_status["step"] = "ITAD prices"
            try:
                sync_prices(itad_key)
            except Exception as itad_err:
                # ITAD may return 403 if the app hasn't been approved yet for
                # price endpoints. Log the warning and continue â€” the code will
                # work automatically once ITAD grants access.
                print(f"[ITAD] Skipped: {itad_err}")
                sync_status["step"] = "ITAD skipped (see terminal)"

        # Sync Loaded.com prices (with rate limiting to avoid blocks)
        sync_status["step"] = "Loaded.com prices"
        try:
            sync_loaded()
        except Exception as loaded_err:
            print(f"[Loaded] Skipped: {loaded_err}")
            sync_status["step"] = "Loaded skipped (see terminal)"

        sync_status = {"running": False, "step": "Done", "error": None, "done": True}

    except Exception as e:
        sync_status = {"running": False, "step": "", "error": str(e), "done": True}


# Page routes

@app.route("/")
def index():
    """Main dashboard â€” renders the full deals table."""
    return render_template("index.html")


@app.route("/game/<int:app_id>")
def game_detail(app_id: int):
    """Detail page for a single game."""
    game = get_game_by_id(app_id)
    if not game:
        return redirect(url_for("index"))
    return render_template("game.html", game=game)


@app.route("/settings")
def settings():
    """Settings / sync page."""
    return render_template("settings.html")


# API routes

@app.route("/api/stats")
def api_stats():
    return jsonify(get_stats())


@app.route("/api/games")
def api_games():
    """
    Returns the deals report as JSON.
    Supports ?filter=sale (only discounted) and ?min_discount=50
    """
    rows = get_deals_report()

    filter_param    = request.args.get("filter")
    min_disc        = request.args.get("min_discount", type=int)
    search          = request.args.get("q", "").lower().strip()

    if filter_param == "sale":
        rows = [r for r in rows if (r.get("best_discount") or 0) > 0]

    if min_disc:
        rows = [r for r in rows if (r.get("best_discount") or 0) >= min_disc]

    if search:
        rows = [r for r in rows if search in r["title"].lower()]

    return jsonify(rows)


@app.route("/api/games/with-prices")
def api_games_with_all_prices():
    """
    Returns all games with ALL their store prices (not just best).
    Used by the frontend to recalculate best price when stores are excluded.
    
    Response format:
    [
      {
        app_id, title, steam_url, header_image, last_checked,
        prices: [{store, price_current, price_regular, discount_pct, url, currency}, ...],
        historic_low, historic_low_store, num_bundles
      },
      ...
    ]
    """
    from database import get_all_prices_for_game
    
    games = get_all_games()
    result = []
    
    for game in games:
        try:
            # Get all prices for this game (excluding historic low metadata entries)
            all_prices = get_all_prices_for_game(game["app_id"])
            prices = [p for p in all_prices if not p["store"].startswith("Historic Low")]
            
            # Calculate historic low from the historic_lows table (from ITAD)
            conn = get_connection()
            historic_row = conn.execute(
                "SELECT MIN(price) as low_price, store FROM historic_lows WHERE app_id = ? GROUP BY app_id ORDER BY low_price ASC LIMIT 1",
                (game["app_id"],)
            ).fetchone()
            historic_low = historic_row[0] if historic_row else None
            historic_low_store = historic_row[1] if historic_row else None
            conn.close()
            
            # Count bundles
            bundles = get_game_bundles(game["app_id"])
            
            # If no current prices exist but we have a historic low,
            # add it as a synthetic "reference only" price so the game shows up
            if not prices and historic_low is not None:
                prices = [{
                    "store": "Reference Price (Historic Low)",
                    "price_current": historic_low,
                    "price_regular": historic_low,
                    "currency": "GBP",
                    "discount_pct": 0,
                    "url": None,
                }]
            
            result.append({
                "app_id": game["app_id"],
                "title": game["title"],
                "steam_url": game["steam_url"],
                "header_image": game["header_image"],
                "last_checked": game["last_checked"],
                "prices": prices,
                "historic_low": historic_low,
                "historic_low_store": historic_low_store,
                "num_bundles": len(bundles),
            })
        except Exception as e:
            print(f"[API] Error processing game {game.get('app_id')}: {e}")
            # Still add the game but with empty prices
            result.append({
                "app_id": game["app_id"],
                "title": game["title"],
                "steam_url": game.get("steam_url"),
                "header_image": game.get("header_image"),
                "last_checked": game.get("last_checked"),
                "prices": [],
                "historic_low": None,
                "historic_low_store": None,
                "num_bundles": 0,
            })
    
    return jsonify(result)


@app.route("/api/game/<int:app_id>")
def api_game_detail(app_id: int):
    """Full detail for one game: metadata + all prices + history + bundles."""
    game    = get_game_by_id(app_id)
    if not game:
        return jsonify({"error": "Not found"}), 404

    prices  = get_all_prices_for_game(app_id)
    history = get_game_price_history(app_id)
    bundles = get_game_bundles(app_id)

    return jsonify({
        "game":    dict(game),
        "prices":  prices,
        "history": history,
        "bundles": bundles,
    })


@app.route("/api/sync/status")
def api_sync_status():
    return jsonify(sync_status)


@app.route("/api/sync/full", methods=["POST"])
def api_sync_full():
    """
    Trigger a full sync. Accepts JSON body:
      { steam_id, steam_key, itad_key }
    Runs in a background thread â€” poll /api/sync/status for progress.
    """
    global sync_status

    if sync_status.get("running"):
        return jsonify({"error": "Sync already running"}), 409

    data        = request.get_json() or {}
    steam_id    = data.get("steam_id")    or os.getenv("STEAM_ID", "")
    steam_key   = data.get("steam_key")   or os.getenv("STEAM_API_KEY", "")
    itad_key    = data.get("itad_key")    or os.getenv("ITAD_API_KEY", "")

    if not steam_id or not steam_key:
        return jsonify({"error": "steam_id and steam_key are required"}), 400

    thread = threading.Thread(
        target=run_full_sync,
        args=(steam_id, steam_key, itad_key),
        daemon=True,
    )
    thread.start()

    return jsonify({"ok": True, "message": "Sync started"})


@app.route("/api/game/<int:app_id>", methods=["DELETE"])
def api_delete_game(app_id: int):
    """Remove a game from the DB (and all its prices/bundles)."""
    delete_game(app_id)
    return jsonify({"ok": True})


# Template filters

@app.template_filter("gbp")
def fmt_gbp(value):
    """Format a float as GBP: Â£14.99"""
    if value is None:
        return "N/A"
    return f"Â£{value:,.2f}"


@app.template_filter("short_date")
def short_date(value):
    """Trim ISO datetime to YYYY-MM-DD."""
    if not value:
        return "â€”"
    return str(value)[:10]


# Startup

if __name__ == "__main__":
    init_db()
    print("\nðŸŽ® Steam Wishlist Tracker running at http://localhost:8080\n")
    # debug=False for stability; set to True during development
    app.run(host="0.0.0.0", port=8080, debug=False)
