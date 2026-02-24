"""
steam.py
--------
Fetches your Steam wishlist via the Steam Web API.

Steam API overview:
  - Base URL: https://api.steampowered.com
  - Auth: API key passed as ?key= query param
  - Wishlist endpoint: IWishlistService/GetWishlist/v1
    Returns a list of app_ids. We then batch-fetch details
    from the Steam Store API (no key needed for that part).

How to get your Steam API key:
  â†’ https://steamcommunity.com/dev/apikey
  (Log in, enter any domain name, copy the key)

How to find your Steam ID:
  Open Steam Profile Copy URL
  If it's a custom URL like /id/username, visit:
  Open Steam Profile Copy URL
  If it's a custom URL like /id/username, visit:
    https://steamdb.info/calculator/ to convert to a numeric SteamID64
"""

import requests
import time
from src.database import upsert_game, get_all_games, upsert_price


# Steam endpoints
WISHLIST_URL = "https://api.steampowered.com/IWishlistService/GetWishlist/v1"
APP_DETAILS_URL = "https://store.steampowered.com/api/appdetails"

# Steam rate limits are generous but real. 1 request/sec is safe.
RATE_LIMIT_DELAY = 1.0  # seconds between app-detail fetches


def fetch_wishlist_app_ids(steam_id: str, api_key: str) -> list[int]:
    """
    Step 1: Get a flat list of App IDs from your wishlist.

    The IWishlistService endpoint returns items in priority order
    (the order you sorted your wishlist on Steam).

    Returns a list of integers like [570, 1091500, 289070, ...]
    """
    params = {
        "key": api_key,
        "steamid": steam_id,
        "count": 5000,   # max items raise if you have a very large wishlist
    }

    print("[Steam] Fetching wishlist app IDs...")
    resp = requests.get(WISHLIST_URL, params=params, timeout=15)

    # Raise an exception for HTTP errors (4xx, 5xx)
    resp.raise_for_status()

    data = resp.json()

    # The response nests under response items appid
    items = data.get("response", {}).get("items", [])

    if not items:
        print("[Steam] No wishlist items found. Check your Steam ID and privacy settings.")
        print("        Your Steam profile and game details must be set to Public.")
        return []

    app_ids = [item["appid"] for item in items]
    print(f"[Steam] Found {len(app_ids)} games on wishlist.")
    return app_ids


def fetch_app_details(app_id: int) -> dict | None:
    """
    Step 2: For each App ID, fetch the game's metadata from the Store API.

    This is a separate endpoint (store.steampowered.com, not api.steampowered.com)
    and doesn't require an API key.

    Returns a dict with keys: name, steam_url, header_image
    Returns None if the app doesn't exist or isn't a game.
    """
    params = {
        "appids": app_id,
        "cc": "gb",    # country code affects prices shown
        "l": "en",
    }

    resp = requests.get(APP_DETAILS_URL, params=params, timeout=15)
    resp.raise_for_status()

    data = resp.json()
    app_data = data.get(str(app_id), {})

    # The API returns {"success": false} for invalid/removed apps
    if not app_data.get("success"):
        return None

    info = app_data.get("data", {})

    # Filter to games only skip DLCs, soundtracks, tools
    if info.get("type") != "game":
        return None

    # Extract price information (prices are in pence, convert to pounds)
    price_overview = info.get("price_overview", {})
    price_gbp = None
    price_original_gbp = None
    
    if price_overview:
        # Prices come in pence from Steam API
        price_pence = price_overview.get("final")
        price_original_pence = price_overview.get("initial")
        
        if price_pence is not None:
            price_gbp = price_pence / 100.0
        if price_original_pence is not None:
            price_original_gbp = price_original_pence / 100.0
        
        # IMPORTANT: Only use prices if we have BOTH current AND original
        # If Steam doesn't provide the original price, we can't use it as a baseline
        # Better to skip Steam and use ITAD as the source
        if price_original_gbp is None:
            # No original price available, set both to None
            # This game will get prices from ITAD instead
            price_gbp = None
            price_original_gbp = None

    return {
        "name": info.get("name", f"App {app_id}"),
        "steam_url": f"https://store.steampowered.com/app/{app_id}/",
        "header_image": info.get("header_image"),
        "price_gbp": price_gbp,
        "price_original_gbp": price_original_gbp,
    }


def sync_wishlist(steam_id: str, api_key: str) -> list[int]:
    """
    Main entry point: fetch wishlist, look up each game's details,
    save everything to the database.

    Returns the list of app_ids that were successfully saved.

    Design note: we fetch all IDs first (fast, one request), then
    look up details one-by-one with a delay (slow but polite).
    """
    app_ids = fetch_wishlist_app_ids(steam_id, api_key)
    if not app_ids:
        return []

    # Check which games we already have in the DB (avoid redundant fetches)
    existing = {g["app_id"] for g in get_all_games()}
    new_ids = [aid for aid in app_ids if aid not in existing]
    print(f"[Steam] {len(existing)} already in DB, fetching details for {len(new_ids)} new games...")

    saved = list(existing)  # start with already-known games

    for i, app_id in enumerate(new_ids, 1):
        print(f"[Steam] ({i}/{len(new_ids)}) Fetching details for app {app_id}...", end=" ")

        details = fetch_app_details(app_id)

        if details is None:
            print("Skipped (not a game or unavailable)")
        else:
            upsert_game(
                app_id=app_id,
                title=details["name"],
                steam_url=details["steam_url"],
                header_image=details["header_image"],
            )
            
            # Save Steam's price as a store entry
            if details["price_gbp"] is not None:
                upsert_price(
                    app_id=app_id,
                    store="Steam",
                    price_current=details["price_gbp"],
                    price_regular=details["price_original_gbp"] or details["price_gbp"],
                    currency="GBP",
                    discount_pct=0,  # Will be calculated by discount logic later
                    url=details["steam_url"],
                )
            
            saved.append(app_id)
            print(f"{details['name']}")

        # Be polite to Steam's servers
        time.sleep(RATE_LIMIT_DELAY)

    print(f"\n[Steam] Sync complete. {len(saved)} games in database.")
    return saved
