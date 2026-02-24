"""
itad.py
-------
Fetches price data from IsThereAnyDeal (ITAD) API (current version).

ITAD tracks prices across 30+ PC game stores and provides:
  Current prices across all stores
  Historic low prices
  Bundle history

Authentication:
  Most public endpoints (prices, lookups, bundles) use a plain API key
  passed as a Bearer token in the Authorization header.

  OAuth2 (Authorization Code + PKCE) is only needed for user-specific
  endpoints like Waitlist and Collection â€” we don't use those here.

How to get your free API key:
  https://isthereanydeal.com/apps/my/
  Register an app your key and OAuth credentials are generated automatically.

API documentation:
  https://docs.isthereanydeal.com/

Key concepts:
  - ITAD identifies games by internal UUIDs, not Steam App IDs.
  - We first convert Steam App ID â†’ ITAD UUID via the lookup endpoint.
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
    get_connection,  # Add this import for discount recalculation
)

BASE_URL = "https://api.isthereanydeal.com"


def _warn_itad(err: Exception, what: str) -> None:
    """
    Print a clear, actionable warning when an ITAD call fails.
    A 403 means the endpoint needs explicit approval from ITAD â€”
    email api@isthereanydeal.com to request access.
    All other errors are logged as-is.
    """
    msg = str(err)
    if "403" in msg:
        print(f"[ITAD]    {what} skipped endpoint not yet approved.")
        print(f"[ITAD]    Email api@isthereanydeal.com to request access.")
        print(f"[ITAD]    Once approved, this will work automatically.")
    else:
        print(f"[ITAD] âš   {what} failed: {err}")


def _headers() -> dict:
    """Standard headers for ITAD requests (no auth key goes in query params)."""
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
        # Format: "app/{steam_app_id}" ITAD's shop ID format for Steam
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


def fetch_all_data(itad_ids: list[str], api_key: str) -> tuple[dict, dict, dict]:
    """
    Fetch all game data using prices/v3 endpoint.
    
    prices/v3 returns:
    - deals: All current prices from all shops
    - historyLow: Historic low prices (all, y1, m3)
    - Plus all shop data in one call
    
    Then fetch bundles separately from overview/v2.
    
    Returns: (prices_map, historic_map, bundles_map)
    """
    CHUNK_SIZE = 100
    prices_map: dict[str, list] = {}
    historic_map: dict[str, dict] = {}
    bundles_map: dict[str, list] = {}
    all_stores_found = set()

    for i in range(0, len(itad_ids), CHUNK_SIZE):
        chunk = itad_ids[i:i + CHUNK_SIZE]

        # ══ CALL 1: Fetch prices/v3 - includes all shops and historic low ══
        try:
            resp_prices = requests.post(
                f"{BASE_URL}/games/prices/v3",
                params={
                    "country": "GB", 
                    "key": api_key
                },
                headers=_headers(),
                json=chunk,
                timeout=30,
            )
            resp_prices.raise_for_status()

            data_prices = resp_prices.json()
            
            # prices/v3 returns a list of games
            if isinstance(data_prices, list):
                prices_list = data_prices
            else:
                prices_list = []
            
            chunk_stores = set()
            chunk_with_lows = 0
            
            # Extract current prices AND historic lows
            for price_entry in prices_list:
                game_id = price_entry.get("id")
                if not game_id:
                    continue
                
                # === CURRENT PRICES: deals array has all shops ===
                deals = price_entry.get("deals", [])
                if deals:
                    for deal in deals:
                        shop_name = deal.get("shop", {}).get("name", "Unknown")
                        chunk_stores.add(shop_name)
                        all_stores_found.add(shop_name)
                    
                    if game_id not in prices_map:
                        prices_map[game_id] = []
                    prices_map[game_id].extend(deals)
                
                # === HISTORIC LOW: historyLow.all ===
                history_low = price_entry.get("historyLow", {})
                if history_low:
                    low_data = history_low.get("all")  # all-time low
                    if low_data and low_data.get("amount") is not None:
                        chunk_with_lows += 1
                        # For historic low, we need to find which shop had it
                        # Use the storeLow from deals or mark as "Historic Low"
                        historic_map[game_id] = {
                            "shop": "Historic Low",
                            "price": low_data.get("amount"),
                            "cut": 0,  # Historic low percentage is always 0
                            "date": "",  # prices/v3 doesn't include historic low timestamp
                        }
            
            chunk_num = (i // CHUNK_SIZE) + 1
            print(f"[ITAD] Chunk {chunk_num}: {len(prices_list)} games, {len(chunk_stores)} unique stores")
            if chunk_stores:
                print(f"[ITAD]   Stores: {', '.join(sorted(chunk_stores))}")
            print(f"[ITAD]   Historic lows: {chunk_with_lows}/{len(prices_list)}")
        
        except Exception as e:
            _warn_itad(e, f"prices/v3 (chunk {(i // CHUNK_SIZE) + 1})")

        # ══ CALL 2: Fetch bundles from overview/v2 ══
        try:
            resp_overview = requests.post(
                f"{BASE_URL}/games/overview/v2",
                params={
                    "country": "GB", 
                    "key": api_key
                },
                headers=_headers(),
                json=chunk,
                timeout=30,
            )
            resp_overview.raise_for_status()

            data_overview = resp_overview.json()
            bundles_list = data_overview.get("bundles", [])
            
            # Extract bundles
            for bundle in bundles_list:
                game_id = bundle.get("id")
                if game_id:
                    if game_id not in bundles_map:
                        bundles_map[game_id] = []
                    bundles_map[game_id].append(bundle)
        
        except Exception as e:
            _warn_itad(e, f"bundles (chunk {(i // CHUNK_SIZE) + 1})")

    print(f"\n[ITAD] Total: {len(prices_map)} games with prices, {len(historic_map)} with historic lows, {len(bundles_map)} with bundles")
    print(f"[ITAD] Unique stores found: {len(all_stores_found)}")
    if all_stores_found:
        print(f"[ITAD]   {', '.join(sorted(all_stores_found))}")
    
    return prices_map, historic_map, bundles_map
def _recalculate_discounts_from_steam(app_id: int) -> None:
    """
    Recalculate all discounts for a game using Steam's original price as baseline.
    
    Steam's price_overview.initial field is the original/full price before any discount.
    This is the authoritative baseline for calculating discounts across all stores.
    
    Formula: discount% = ((steam_initial - current_price) / steam_initial) * 100
    """
    conn = get_connection()
    
    try:
        # Get Steam's ORIGINAL (initial) price as the universal baseline
        # This is Steam's official "full price" before discounts
        steam_row = conn.execute(
            "SELECT price_regular FROM prices WHERE app_id = ? AND store = 'Steam' AND price_regular IS NOT NULL",
            (app_id,)
        ).fetchone()
        
        if not steam_row or not steam_row[0]:
            # No Steam baseline available, skip discount recalculation
            return
        
        steam_baseline = float(steam_row[0])
        
        if steam_baseline <= 0:
            # Invalid baseline, skip
            return
        
        # Get all prices (including Steam itself) for discount calculation
        all_prices = conn.execute(
            "SELECT id, store, price_current FROM prices WHERE app_id = ? AND price_current IS NOT NULL",
            (app_id,)
        ).fetchall()
        
        recalculated_count = 0
        for price_id, store_name, current_price in all_prices:
            if current_price is None or current_price < 0:
                continue
            
            # Calculate discount as percentage off Steam's original price
            discount_pct = int(((steam_baseline - current_price) / steam_baseline) * 100)
            
            # Clamp to [0, 100] - discount can't be negative or over 100%
            discount_pct = max(0, min(100, discount_pct))
            
            # Update the discount
            conn.execute(
                "UPDATE prices SET discount_pct = ? WHERE id = ?",
                (discount_pct, price_id)
            )
            recalculated_count += 1
        
        conn.commit()
        
        if recalculated_count > 0:
            print(f"[ITAD] Game {app_id}: Recalculated {recalculated_count} prices (baseline: £{steam_baseline:.2f})")
    
    except Exception as e:
        print(f"[ITAD] Error recalculating discounts for app {app_id}: {e}")
    finally:
        conn.close()


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

    # Two efficient API calls get all data (prices, historic lows, bundles)
    try:
        prices_map, historic_map, bundles_map = fetch_all_data(itad_ids, api_key)
    except Exception as e:
        _warn_itad(e, "data fetch")
        return

    # Cache all game titles once (avoid repeated DB calls)
    all_games_map = {g['app_id']: g['title'] for g in games}

    # Load Steam's baseline prices once (before saving ITAD prices)
    steam_baselines = {}
    try:
        baseline_conn = get_connection()
        baseline_rows = baseline_conn.execute(
            "SELECT app_id, price_regular FROM prices WHERE store = 'Steam' AND price_regular IS NOT NULL"
        ).fetchall()
        baseline_conn.close()
        for app_id, baseline in baseline_rows:
            steam_baselines[app_id] = baseline
        print(f"[ITAD] Loaded Steam baselines for {len(steam_baselines)} games")
    except Exception as e:
        print(f"[ITAD] Warning: Could not load Steam baselines: {e}")

    # Save current prices
    games_with_no_deals = []
    total_prices_saved = 0
    
    for itad_id, deals in prices_map.items():
        app_id = itad_to_steam.get(itad_id)
        if not app_id:
            continue
        
        if not deals:
            games_with_no_deals.append(app_id)
            continue
        
        # Get this game's Steam baseline (if it exists)
        steam_baseline = steam_baselines.get(app_id)
            
        for deal in deals:
            shop  = deal.get("shop", {}).get("name", "unknown")
            price = deal.get("price", {})
            
            current = price.get("amount")
            if current is None:
                continue
            
            # Use Steam's baseline for all ITAD prices (not ITAD's regular field)
            regular = steam_baseline
            
            # Extract DRM requirements (comma-separated list of DRM names)
            drm_list = deal.get("drm", [])
            drm_str = ",".join([d.get("name", "Unknown") for d in drm_list]) if drm_list else None
            
            print(f"[ITAD] Saving: App {app_id} @ {shop} = £{current} (discount {deal.get('cut', 0)}%)")
            
            upsert_price(
                app_id=app_id,
                store=shop,
                price_current=current,
                price_regular=regular,
                currency="GBP",
                discount_pct=0,  # Will be recalculated properly based on Steam baseline
                url=deal.get("url"),
                drm=drm_str,
            )
            total_prices_saved += 1
    
    print(f"[ITAD] Saved {total_prices_saved} current prices across all games")
    
    # Report games with only 1 store
    debug_single_store_games = []
    for itad_id, deals in prices_map.items():
        if len(deals) == 1:
            debug_single_store_games.append((itad_to_steam.get(itad_id), deals[0].get("shop", {}).get("name", "unknown")))
    
    if debug_single_store_games and len(debug_single_store_games) > 5:
        print(f"[ITAD]  {len(debug_single_store_games)} games only available from 1 store")
        print(f"[ITAD]  Examples:")
        for app_id, store in debug_single_store_games[:5]:
            print(f"[ITAD]    - {all_games_map.get(app_id, 'Unknown')}: only on {store}")
    
    # Report games with no deals
    if games_with_no_deals:
        print(f"[ITAD]  {len(games_with_no_deals)} games returned no current prices from ITAD:")
        for aid in games_with_no_deals[:10]:
            print(f"[ITAD]    - {all_games_map.get(aid, 'Unknown')} (App ID: {aid})")

    # Save historic lows
    for itad_id, low in historic_map.items():
        app_id = itad_to_steam.get(itad_id)
        if not app_id or not low.get("price"):
            continue
        
        print(f"[ITAD] Saving historic low: App {app_id} = {low['price']} @ {low['shop']}")
        
        upsert_historic_low(
            app_id=app_id,
            store=low["shop"],
            price=low["price"],
            currency="GBP",
            discount_pct=low.get("cut", 0),
            recorded_date=low.get("date"),
        )

    # Save bundles
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

    # Recalculate discounts based on Steam baseline
    print(f"\n[ITAD] Recalculating discounts based on Steam prices...")
    for app_id in steam_to_itad.keys():
        _recalculate_discounts_from_steam(app_id)

    for app_id in steam_to_itad.keys():
        mark_game_checked(app_id)

    # Summary
    games_with_prices = sum(1 for g in prices_map.values() if g)
    print(f"[ITAD] Done. {games_with_prices}/{len(itad_ids)} games had prices available")
    print(f"[ITAD] Total: {total_prices_saved} prices saved, {len(historic_map)} historic lows, {sum(len(b) for b in bundles_map.values())} bundles")


def sync_loaded(steam_id_to_title: dict = None) -> None:
    """
    Sync prices from loaded.com for games in the database.
    
    loaded.com is a UK game key reseller with good prices.
    We scrape the prices and save them to the database.
    
    Args:
        steam_id_to_title: Optional dict of {app_id: game_title} to sync
                          If None, syncs all games in database
    """
    import time
    from loaded import scrape_game_price
    
    if steam_id_to_title is None:
        # Get all games from database
        games = get_all_games()
        steam_id_to_title = {g["app_id"]: g["title"] for g in games}
    
    if not steam_id_to_title:
        print("[Loaded] No games to sync")
        return
    
    print(f"\n[Loaded] Scraping prices for {len(steam_id_to_title)} games...")
    
    prices_saved = 0
    not_found = []
    
    for i, (app_id, title) in enumerate(steam_id_to_title.items(), 1):
        try:
            result = scrape_game_price(title, platform="pc", drm="steam")
            
            if result:
                from database import upsert_price
                
                upsert_price(
                    app_id=app_id,
                    store="Loaded",
                    price_current=result["price"],
                    price_regular=result["regular_price"],
                    currency=result["currency"],
                    discount_pct=result["discount_pct"],
                    url=result["url"],
                    drm=result["drm"],
                )
                prices_saved += 1
            else:
                not_found.append(title)
        
        except Exception as e:
            print(f"[Loaded] Error processing {title}: {e}")
            not_found.append(title)
        
        # Delay between requests (rate limiting is also done in loaded.py)
        if i < len(steam_id_to_title):
            time.sleep(2)
    
    print(f"[Loaded] ✓ Saved {prices_saved}/{len(steam_id_to_title)} prices")
    
    if not_found and len(not_found) <= 10:
        print(f"[Loaded] ⚠  Not found ({len(not_found)}):")
        for title in not_found[:10]:
            print(f"[Loaded]   - {title}")
    elif not_found:
        print(f"[Loaded] ⚠  {len(not_found)} games not found on Loaded")
