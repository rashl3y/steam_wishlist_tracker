"""
loaded.py
---------
Scrapes game prices and DRM info from loaded.com (formerly CDKeys).

URL format: https://www.loaded.com/[game-slug]-pc-[drm]
Example: https://www.loaded.com/warhammer-40-000-space-marine-2-pc-steam

DRM types detected from URL suffix:
- -pc-steam    → Steam
- -pc-epic     → Epic Games
- -pc-gog      → GOG
- -pc-uplay    → Uplay
- -xbox-live   → Xbox Live
- -psn         → PlayStation Network
- etc.
"""

import requests
import re
import time
from typing import Optional, Dict
from datetime import datetime, timezone

# User agent to avoid being blocked
HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
    'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,image/apng,*/*;q=0.8',
    'Accept-Language': 'en-GB,en-US;q=0.9,en;q=0.8',
    'Accept-Encoding': 'gzip, deflate, br',
    'DNT': '1',
    'Connection': 'keep-alive',
    'Upgrade-Insecure-Requests': '1',
    'Sec-Fetch-Dest': 'document',
    'Sec-Fetch-Mode': 'navigate',
    'Sec-Fetch-Site': 'none',
    'Sec-Fetch-User': '?1',
    'Cache-Control': 'max-age=0',
}

LOADED_BASE = "https://www.loaded.com"
LOADED_TIMEOUT = 20

# Global session for connection reuse
_session = None
_last_request_time = 0
_min_delay_seconds = 15  # Minimum delay between requests to avoid rate limiting

def _get_session():
    """Get or create a persistent session."""
    global _session
    if _session is None:
        _session = requests.Session()
        _session.headers.update(HEADERS)
    return _session


def _enforce_rate_limit():
    """Enforce minimum delay between requests to avoid rate limiting."""
    global _last_request_time
    now = time.time()
    elapsed = now - _last_request_time
    if elapsed < _min_delay_seconds:
        wait_time = _min_delay_seconds - elapsed
        print(f"[Loaded] Rate limiting: waiting {wait_time:.1f}s before next request...")
        time.sleep(wait_time)
    _last_request_time = time.time()


def _normalize_game_title(title: str) -> str:
    """
    Convert a game title to the loaded.com URL format.
    
    Example: "Warhammer 40,000: Space Marine 2" → "warhammer-40-000-space-marine-2"
    """
    # Remove special characters, keep only alphanumeric and spaces/hyphens
    normalized = re.sub(r'[^\w\s-]', '', title)
    # Convert spaces to hyphens
    normalized = re.sub(r'\s+', '-', normalized)
    # Remove multiple consecutive hyphens
    normalized = re.sub(r'-+', '-', normalized)
    # Convert to lowercase and strip leading/trailing hyphens
    return normalized.lower().strip('-')


def _extract_drm_from_url(url: str) -> Optional[str]:
    """
    Extract DRM type from loaded.com URL suffix.
    
    Examples:
    - https://www.loaded.com/game-pc-steam → "Steam"
    - https://www.loaded.com/game-pc-epic → "Epic Games"
    - https://www.loaded.com/game-xbox-live → "Xbox Live"
    """
    url_lower = url.lower()
    
    # Map URL suffixes to DRM names
    drm_map = {
        '-pc-steam': 'Steam',
        '-pc-epic': 'Epic Games',
        '-pc-gog': 'GOG',
        '-pc-uplay': 'Uplay',
        '-pc-rockstar': 'Rockstar',
        '-xbox-live': 'Xbox Live',
        '-xbox-game-pass': 'Xbox Game Pass',
        '-psn': 'PlayStation Network',
        '-switch': 'Nintendo Switch',
    }
    
    for suffix, drm_name in drm_map.items():
        if url.endswith(suffix) or url.endswith(suffix + '/'):
            return drm_name
    
    # If no recognized suffix, try to extract from URL end
    parts = url.rstrip('/').split('-')
    if len(parts) >= 2:
        # Last part might be DRM
        last = parts[-1].lower()
        if last in ['steam', 'epic', 'gog', 'uplay', 'rockstar', 'psn', 'switch']:
            return last.capitalize()
    
    return None


def scrape_game_price(game_title: str, platform: str = "pc", drm: str = "steam") -> Optional[Dict]:
    """
    Scrape price and info for a game from loaded.com.
    
    Args:
        game_title: Game name (e.g., "Warhammer 40,000: Space Marine 2")
        platform: Platform (default "pc", could be "xbox", "psn", "switch")
        drm: DRM type (default "steam", could be "epic", "gog", etc.)
    
    Returns:
        Dict with keys:
        - price: Current price in GBP (float)
        - regular_price: Regular/original price (float) 
        - discount_pct: Discount percentage (int)
        - currency: "GBP"
        - drm: DRM string (e.g., "Steam")
        - url: Full loaded.com URL
        - in_stock: Boolean
        - timestamp: When scraped
        
        Returns None if game not found or scrape fails
    """
    import time
    
    try:
        # Build URL
        normalized_title = _normalize_game_title(game_title)
        url = f"{LOADED_BASE}/{normalized_title}-{platform}-{drm}"
        
        print(f"[Loaded] Scraping: {url}")
        
        # Enforce minimum delay between requests
        _enforce_rate_limit()
        
        # Retry logic with AGGRESSIVE backoff for 403 errors
        max_retries = 3
        for attempt in range(max_retries + 1):
            try:
                session = _get_session()
                
                resp = session.get(url, timeout=LOADED_TIMEOUT, allow_redirects=True)
                
                # Handle 404 - game doesn't exist at this URL
                if resp.status_code == 404:
                    print(f"[Loaded] 404 Not Found: {game_title}")
                    return None
                
                # Handle 403 Forbidden with AGGRESSIVE backoff
                if resp.status_code == 403:
                    if attempt < max_retries:
                        # Exponential backoff: 10s, 30s, 90s, 270s
                        wait_time = 10 * (3 ** attempt)
                        print(f"[Loaded] 403 Forbidden, waiting {wait_time}s (attempt {attempt + 1}/{max_retries})...")
                        time.sleep(wait_time)
                        continue
                    else:
                        print(f"[Loaded] ✗ 403 Forbidden - IP is rate limited/blocked by Loaded.com")
                        print(f"[Loaded]   Wait 24-48 hours or use VPN to retry")
                        return None
                
                if resp.status_code != 200:
                    print(f"[Loaded] HTTP {resp.status_code}: {game_title}")
                    return None
                
                resp.raise_for_status()
                break
                
            except requests.exceptions.ConnectionError:
                if attempt < max_retries:
                    wait_time = 10 * (3 ** attempt)
                    print(f"[Loaded] Connection error, waiting {wait_time}s...")
                    time.sleep(wait_time)
                    continue
                else:
                    raise
            except requests.exceptions.Timeout:
                if attempt < max_retries:
                    wait_time = 10 * (3 ** attempt)
                    print(f"[Loaded] Timeout, waiting {wait_time}s...")
                    time.sleep(wait_time)
                    continue
                else:
                    raise

        html = resp.text
        
        # Check if game is out of stock / sold out / product not found
        if "out of stock" in html.lower() or "sold out" in html.lower() or "product not found" in html.lower():
            print(f"[Loaded] ⚠ Out of stock / unavailable: {game_title}")
            return None
        
        # Extract price from page
        # Find ALL £ prices - typically: [sale_price, original_price, ...related_items...]
        prices = re.findall(r'£([\d.]+)', html)
        
        if not prices:
            # Fallback to JSON meta tag format
            meta_match = re.search(r'<meta\s+itemprop="price"\s+content="([\d.]+)"', html)
            if not meta_match:
                print(f"[Loaded] Price not found on page: {url}")
                return None
            current_price = float(meta_match.group(1))
            regular_price = current_price
        else:
            # Use first price as current (sale), second as regular (original) if it exists
            current_price = float(prices[0])
            regular_price = float(prices[1]) if len(prices) > 1 else current_price
        
        # Calculate discount
        discount_pct = 0
        if regular_price > current_price:
            discount_pct = int(((regular_price - current_price) / regular_price) * 100)
        
        # Check if in stock (explicitly check for these phrases)
        in_stock = "out of stock" not in html.lower() and "sold out" not in html.lower()
        
        # Extract DRM
        detected_drm = _extract_drm_from_url(url) or drm.capitalize()
        
        result = {
            "price": current_price,
            "regular_price": regular_price,
            "discount_pct": discount_pct,
            "currency": "GBP",
            "drm": detected_drm,
            "url": url,
            "in_stock": in_stock,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
        
        print(f"[Loaded] ✓ Found: £{current_price} (was £{regular_price}, {discount_pct}% off)")
        return result
        
    except requests.exceptions.Timeout:
        print(f"[Loaded] ✗ Timeout scraping {game_title}")
        return None
    except requests.exceptions.HTTPError as e:
        if e.response.status_code == 404:
            print(f"[Loaded] ✗ Game not found: {game_title}")
        else:
            print(f"[Loaded] ✗ HTTP error {e.response.status_code}: {game_title}")
        return None
    except Exception as e:
        print(f"[Loaded] ✗ Error scraping {game_title}: {e}")
        return None


def sync_loaded_prices(app_id: int, game_title: str, itad_drm: str = None) -> Optional[Dict]:
    """
    Sync price from loaded.com to database.
    
    Args:
        app_id: Steam App ID
        game_title: Game title
        itad_drm: DRM string from ITAD (e.g., "Steam,Denuvo")
    
    Returns:
        Scraped data if successful, None otherwise
    """
    # Try scraping (default to Steam)
    data = scrape_game_price(game_title, platform="pc", drm="steam")
    
    if data:
        from database import upsert_price
        
        # Save to database
        upsert_price(
            app_id=app_id,
            store="Loaded",
            price_current=data["price"],
            price_regular=data["regular_price"],
            currency=data["currency"],
            discount_pct=data["discount_pct"],
            url=data["url"],
            drm=data["drm"],
        )
        
        return data
    
    return None


if __name__ == "__main__":
    # Test scraper
    test_games = [
        "Warhammer 40000 Space Marine 2",
        "Cyberpunk 2077",
        "Elden Ring",
    ]
    
    for title in test_games:
        result = scrape_game_price(title)
        if result:
            print(f"  {title}: £{result['price']} ({result['discount_pct']}% off)")
        print()
