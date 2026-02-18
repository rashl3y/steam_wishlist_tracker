"""
itad.py
-------
Fetches price data from IsThereAnyDeal (ITAD) API (current version).

ITAD tracks prices across 30+ PC game stores and provides:
  ✓ Current prices across all stores
  ✓ Historic low prices
  ✓ Bundle history

Authentication:
  Most public endpoints (prices, lookups, bundles) use a plain API key
  passed as a Bearer token in the Authorization header.

  OAuth2 (Authorization Code + PKCE) is only needed for user-specific
  endpoints like Waitlist and Collection — we don't use those here.

How to get your free API key:
  → https://isthereanydeal.com/apps/my/
  Register an app → your key and OAuth credentials are generated automatically.

API documentation:
  → https://docs.isthereanydeal.com/

Key concepts:
  - ITAD identifies games by internal UUIDs, not Steam App IDs.
  - We first convert Steam App ID → ITAD UUID via the lookup endpoint.
  - That UUID is then used for all price/bundle queries.
  - UUIDs are cached in the DB (itad_slug column) so we only look them up once.
"""

import requests
from database import (
    get_all_games,
    upsert_game,
    update_itad_slug,
    mark_game_checked,
    upsert_price,
    upsert_historic_low,  # Add this import
    upsert_bundle,
)

BASE_URL = "https://api.isthereanydeal.com"


def _warn_itad(err: Exception, what: str) -> None:
    """
    Print a clear, actionable warning when an ITAD call fails.
    A 403 means the endpoint needs explicit approval from ITAD —
    email api@isthereanydeal.com to request access.
    All other errors are logged as-is.
    """
    msg = str(err)
    if "403" in msg:
        print(f"[ITAD] ⚠  {what} skipped — endpoint not yet approved.")
        print(f"[ITAD]    Email api@isthereanydeal.com to request access.")
        print(f"[ITAD]    Once approved, this will work automatically.")
    else:
        print(f"[ITAD] ⚠  {what} failed: {err}")


def _headers() -> dict:
    """Standard headers for ITAD requests (no auth — key goes in query params)."""
    return {"Content-Type": "application/json"}


def lookup_itad_ids(app_ids: list[int], api_key: str) -> dict[int, str]:
    """
    Convert Steam App IDs to ITAD internal UUIDs via the lookup endpoint.

    Endpoint: POST /lookup/id/shop/{shopId}/v1
    Docs: https://docs.isthereanydeal.com/#tag/Lookup/operation/lookup-id-shop

    Steam's numeric shop ID on ITAD is 61.

    Request body: list of shop-format ID strings e.g. ["app/1091500", "app/570"]
    Response: dict keyed by those same strings e.g. {"app/1091500": "uuid...", ...}
    A null value means ITAD doesn't have that game.

    Chunked into groups of 100 (API limit per request).
    """
    STEAM_SHOP_ID = 61  # Steam's numeric ID in the ITAD shop registry
    CHUNK_SIZE = 100
    result: dict[int, str] = {}

    for i in range(0, len(app_ids), CHUNK_SIZE):
        chunk_ids = app_ids[i:i + CHUNK_SIZE]
        # Format: "app/{steam_app_id}" — ITAD's shop ID format for Steam
        payload = [f"app/{aid}" for aid in chunk_ids]

        resp = requests.post(
            f"{BASE_URL}/lookup/id/shop/{STEAM_SHOP_ID}/v1",
            params={"key": api_key},
            headers=_headers(),
            json=payload,
            timeout=20,
        )
        resp.raise_for_status()

        # Response is a dict: {"app/570": "uuid...", "app/1091500": null, ...}
        data = resp.json()
        for app_id, itad_uuid in zip(chunk_ids, [data.get(f"app/{aid}") for aid in chunk_ids]):
            if itad_uuid:
                result[app_id] = itad_uuid

    print(f"[ITAD] Matched {len(result)}/{len(app_ids)} games to ITAD IDs.")
    return result


def fetch_current_prices(itad_ids: list[str], api_key: str) -> dict[str, list]:
    """Fetch current prices across all stores in GBP."""
    CHUNK_SIZE = 100
    result: dict[str, list] = {}

    for i in range(0, len(itad_ids), CHUNK_SIZE):
        chunk = itad_ids[i:i + CHUNK_SIZE]

        resp = requests.post(
            f"{BASE_URL}/games/prices/v2",
            params={"country": "GB", "key": api_key, "shops": ""},
            headers=_headers(),
            json=chunk,
            timeout=30,
        )
        resp.raise_for_status()

        data = resp.json()
        print(f"[ITAD] Response keys: {data.keys() if isinstance(data, dict) else 'list'}")
        
        games = data.get("data", data) if isinstance(data, dict) and "data" in data else data
        
        if not isinstance(games, list):
            raise ValueError(f"Unexpected API response: expected list, got {type(games).__name__}")
        
        print(f"[ITAD] Fetched prices for {len(games)} games in this chunk")
        
        for game_data in games:
            result[game_data["id"]] = game_data.get("deals", [])

    print(f"[ITAD] Total games with price data: {len(result)}")
    return result


def fetch_historic_lows(itad_ids: list[str], api_key: str) -> dict[str, dict]:
    """
    Fetch the all-time lowest recorded price per game in GBP.

    Endpoint: POST /games/historylow/v1?country=GB
    Docs: https://docs.isthereanydeal.com/#tag/Games/operation/games-historylow-v1

    Returns: {itad_id: {shop, price, cut, date}}
    """
    CHUNK_SIZE = 100
    result: dict[str, dict] = {}

    for i in range(0, len(itad_ids), CHUNK_SIZE):
        chunk = itad_ids[i:i + CHUNK_SIZE]

        resp = requests.post(
            f"{BASE_URL}/games/historylow/v1",
            params={"country": "GB", "key": api_key, "shops": ""},
            headers=_headers(),
            json=chunk,
            timeout=30,
        )
        resp.raise_for_status()

        for item in resp.json():
            low = item.get("low")
            if low:
                result[item["id"]] = {
                    "shop":  low.get("shop", {}).get("name", "Unknown"),
                    "price": low.get("price", {}).get("amount"),
                    "cut":   low.get("price", {}).get("cut", 0),
                    "date":  (low.get("timestamp") or "")[:10],
                }

    return result


def fetch_bundles(itad_ids: list[str], api_key: str) -> dict[str, list]:
    """
    Fetch bundle history for each game.

    Endpoint: GET /games/bundles/v2?id=UUID1&id=UUID2...
    Docs: https://docs.isthereanydeal.com/#tag/Games/operation/games-bundles-v2

    Note: This endpoint uses GET with repeated ?id= params (not a POST JSON body),
    so we pass each UUID as a separate param. Chunked to avoid URL length limits.

    Bundle tier prices are returned in USD regardless of the country param.
    """
    CHUNK_SIZE = 50  # smaller — GET params have URL length limits
    result: dict[str, list] = {}

    for i in range(0, len(itad_ids), CHUNK_SIZE):
        chunk = itad_ids[i:i + CHUNK_SIZE]

        # Build params list with key first, then all the id params
        params = [("key", api_key)] + [("id", iid) for iid in chunk]
        
        resp = requests.get(
            f"{BASE_URL}/games/bundles/v2",
            params=params,
            headers=_headers(),
            timeout=30,
        )
        resp.raise_for_status()

        for item in resp.json():
            bundles = item.get("bundles", [])
            if bundles:
                result[item["id"]] = bundles

    return result


def sync_prices(api_key: str) -> None:
    """
    Full ITAD price sync. Fetches current prices, historic lows, and
    bundle history for every game in the database, all in GBP.
    """
    games = get_all_games()
    if not games:
        print("[ITAD] No games in DB. Run Steam sync first.")
        return

    needs_lookup = [g for g in games if not g["itad_slug"]]
    steam_to_itad: dict[int, str] = {
        g["app_id"]: g["itad_slug"] for g in games if g["itad_slug"]
    }

    if needs_lookup:
        print(f"[ITAD] Looking up IDs for {len(needs_lookup)} new games...")
        new_map = lookup_itad_ids([g["app_id"] for g in needs_lookup], api_key)
        for app_id, itad_id in new_map.items():
            update_itad_slug(app_id, itad_id)
            steam_to_itad[app_id] = itad_id

    itad_to_steam: dict[str, int] = {v: k for k, v in steam_to_itad.items()}
    itad_ids = list(itad_to_steam.keys())

    if not itad_ids:
        print("[ITAD] No games matched to ITAD. Check your API key.")
        return

    print(f"[ITAD] Fetching GBP prices for {len(itad_ids)} games...")

    prices_map: dict = {}
    historic_map: dict = {}
    bundles_map: dict = {}

    try:
        prices_map = fetch_current_prices(itad_ids, api_key)
    except Exception as e:
        _warn_itad(e, "prices")

    try:
        historic_map = fetch_historic_lows(itad_ids, api_key)
    except Exception as e:
        _warn_itad(e, "historic lows")

    try:
        bundles_map = fetch_bundles(itad_ids, api_key)
    except Exception as e:
        _warn_itad(e, "bundles")

    # Cache all game titles once (avoid repeated DB calls)
    all_games_map = {g['app_id']: g['title'] for g in games}

    # ── Save current prices ────────────────────────────────────────────────────
    games_with_no_deals = []
    total_prices_saved = 0
    
    for itad_id, deals in prices_map.items():
        app_id = itad_to_steam.get(itad_id)
        if not app_id:
            continue
        
        if not deals:
            games_with_no_deals.append(app_id)
            continue
            
        for deal in deals:
            shop  = deal.get("shop", {}).get("name", "unknown")
            price = deal.get("price", {})
            
            current = price.get("amount")
            if current is None:
                continue
            
            regular = price.get("regular", {}).get("amount")
            if regular is None:
                regular = current
            
            print(f"[ITAD] Saving: App {app_id} @ {shop} = £{current} (discount {price.get('cut', 0)}%)")
            
            upsert_price(
                app_id=app_id,
                store=shop,
                price_current=current,
                price_regular=regular,
                currency="GBP",
                discount_pct=price.get("cut", 0),
                url=deal.get("url"),
            )
            total_prices_saved += 1
    
    print(f"[ITAD] Saved {total_prices_saved} current prices across all games")
    
    # Report games with only 1 store
    debug_single_store_games = []
    for itad_id, deals in prices_map.items():
        if len(deals) == 1:
            debug_single_store_games.append((itad_to_steam.get(itad_id), deals[0].get("shop", {}).get("name", "unknown")))
    
    if debug_single_store_games and len(debug_single_store_games) > 5:
        print(f"[ITAD] ⚠  {len(debug_single_store_games)} games only available from 1 store")
        print(f"[ITAD]    Examples:")
        for app_id, store in debug_single_store_games[:5]:
            print(f"[ITAD]    - {all_games_map.get(app_id, 'Unknown')}: only on {store}")
    
    # Report games with no deals
    if games_with_no_deals:
        print(f"[ITAD] ⚠  {len(games_with_no_deals)} games returned no current prices from ITAD:")
        for aid in games_with_no_deals[:10]:
            print(f"[ITAD]    - {all_games_map.get(aid, 'Unknown')} (App ID: {aid})")

    # ── Save historic lows ─────────────────────────────────────────────────────
    for itad_id, low in historic_map.items():
        app_id = itad_to_steam.get(itad_id)
        if not app_id or not low.get("price"):
            continue
        
        print(f"[ITAD] Saving historic low: App {app_id} = £{low['price']} @ {low['shop']}")
        
        upsert_historic_low(
            app_id=app_id,
            store=low["shop"],
            price=low["price"],
            currency="GBP",
            discount_pct=low.get("cut", 0),
            recorded_date=low.get("date"),
        )

    # ── Save bundles ───────────────────────────────────────────────────────────
    for itad_id, bundle_list in bundles_map.items():
        app_id = itad_to_steam.get(itad_id)
        if not app_id:
            continue
        for bundle in bundle_list:
            tiers = bundle.get("tiers", [])
            tier_price = None
            if tiers:
                sorted_tiers = sorted(
                    tiers, key=lambda t: t.get("price", {}).get("amount", 9999)
                )
                tier_price = sorted_tiers[0].get("price", {}).get("amount")
            upsert_bundle(
                app_id=app_id,
                bundle_title=bundle.get("title", "Unknown Bundle"),
                store=bundle.get("type", ""),
                tier_price=tier_price,
                currency="USD",
                bundle_url=bundle.get("url"),
                expires_at=bundle.get("expiry"),
            )

    for app_id in steam_to_itad.keys():
        mark_game_checked(app_id)

    # Summary
    games_with_prices = sum(1 for g in prices_map.values() if g)
    print(f"[ITAD] ✓ Done. {games_with_prices}/{len(itad_ids)} games had prices available")
    print(f"[ITAD] ✓ Total: {total_prices_saved} prices saved, {len(historic_map)} historic lows, {sum(len(b) for b in bundles_map.values())} bundles")
