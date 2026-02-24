"""
sync_loaded_helper.py
---------------------
Syncs prices from loaded.com using BeautifulSoup scraper.
Separate from itad.py to keep ITAD API module unchanged.
"""

import time
from loaded_bs4 import scrape_game_price
from database import get_all_games, upsert_price


def sync_loaded(steam_id_to_title: dict = None) -> None:
    """
    Sync prices from loaded.com for games in the database.
    
    loaded.com is a UK game key reseller with good prices.
    We scrape the prices using Selenium and save them to the database.
    
    Args:
        steam_id_to_title: Optional dict of {app_id: game_title} to sync
                          If None, syncs all games in database
    """
    
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
                upsert_price(
                    app_id=app_id,
                    store="Loaded",
                    price_current=result["price"],
                    price_regular=result["regular_price"],
                    currency=result["currency"],
                    discount_pct=result["discount_pct"],
                    url=result["url"],
                )
                prices_saved += 1
            else:
                not_found.append(title)
        
        except Exception as e:
            print(f"[Loaded] Error processing {title}: {e}")
            not_found.append(title)
    
    print(f"[Loaded] ✓ Saved {prices_saved}/{len(steam_id_to_title)} prices")
    
    if not_found and len(not_found) <= 10:
        print(f"[Loaded] ⚠  Not found ({len(not_found)}):")
        for title in not_found[:10]:
            print(f"[Loaded]   - {title}")
    elif not_found and len(not_found) > 10:
        print(f"[Loaded] ⚠  {len(not_found)} games not found on Loaded.com")
