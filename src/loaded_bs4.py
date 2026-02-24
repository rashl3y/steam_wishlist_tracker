"""
loaded_bs4.py
-------------
Scrapes game prices from loaded.com using BeautifulSoup (lightweight & fast).

Why BeautifulSoup instead of Selenium?
- Faster (no browser overhead)
- Simpler (no browser automation complexity)
- Lighter (no 150MB Chrome driver)
- Still works well for Loaded.com (mostly static HTML)

Handles title matching issues:
- URLs don't always match exact game titles
- Uses fuzzy matching to find the right page
- Tries multiple URL variations
- Extracts actual title from page content
"""

import requests
from bs4 import BeautifulSoup
import re
from typing import Optional, Dict
from datetime import datetime, timezone
from difflib import SequenceMatcher
import time

LOADED_BASE = "https://www.loaded.com"
LOADED_TIMEOUT = 20

# Rate limiting - adaptive
_last_request_time = 0
_min_delay_seconds = 1.0  # Start aggressive (1 second)
_rate_limited = False  # Track if we hit 403 (rate limited)
_consecutive_errors = 0  # Track consecutive 403s

# # Headers -------------------------------------------------------------------

HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
    'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,image/apng,*/*;q=0.8',
    'Accept-Language': 'en-GB,en-US;q=0.9,en;q=0.8',
    'Accept-Encoding': 'gzip, deflate, br',
    'DNT': '1',
    'Connection': 'keep-alive',
    'Cache-Control': 'max-age=0',
}


def _enforce_rate_limit():
    """Enforce minimum delay between requests (adaptive based on 403 errors)."""
    global _last_request_time, _min_delay_seconds
    now = time.time()
    elapsed = now - _last_request_time
    if elapsed < _min_delay_seconds:
        wait_time = _min_delay_seconds - elapsed
        if wait_time > 0.1:  # Only log if significant wait
            print(f"[Loaded] Rate limit: waiting {wait_time:.1f}s...")
        time.sleep(wait_time)
    _last_request_time = time.time()


def _handle_rate_limit_error():
    """Called when we get a 403 error. Increases delay for future requests."""
    global _min_delay_seconds, _consecutive_errors
    _consecutive_errors += 1
    # Exponential backoff: 1s -> 5s -> 15s -> 30s
    if _consecutive_errors == 1:
        _min_delay_seconds = 5.0
        print(f"[Loaded] [WARNING] Rate limited (403)! Increasing delay to 5s...")
    elif _consecutive_errors == 2:
        _min_delay_seconds = 15.0
        print(f"[Loaded] [WARNING] Still rate limited. Increasing delay to 15s...")
    elif _consecutive_errors == 3:
        _min_delay_seconds = 30.0
        print(f"[Loaded] [WARNING] Heavily rate limited. Increasing delay to 30s...")
    else:
        _min_delay_seconds = 60.0
        print(f"[Loaded] [WARNING] Extremely rate limited. Using 60s delay...")


def _reset_rate_limit_on_success():
    """Called on successful request. Resets error counter."""
    global _consecutive_errors
    if _consecutive_errors > 0:
        print(f"[Loaded] [OK] Recovery: rate limit resolved, back to normal")
        _consecutive_errors = 0


def _normalize_game_title(title: str) -> str:
    """
    Convert game title to URL slug.
    
    Examples:
        "Warhammer 40,000: Space Marine 2" -> "warhammer-40-000-space-marine-2"
        "Baldur's Gate 3" -> "baldur-s-gate-3"
        "Ghost of Tsushima DIRECTOR'S CUT" -> "ghost-of-tsushima-director-s-cut"
        "Stardew Valley" -> "stardew-valley"
        "L.A. Noire" -> "l-a-noire"
    """
    title = title.lower()
    # Replace punctuation with hyphens (not removal)
    title = re.sub(r"[\'.]", "-", title)  # Replace apostrophes and periods with hyphens
    title = re.sub(r"[:]", "", title)  # Remove colons (not URLs)
    title = re.sub(r"[^a-z0-9\s-]", "", title)  # Remove other special characters
    title = re.sub(r"\s+", "-", title)  # Replace spaces with dashes
    title = re.sub(r"-+", "-", title)  # Replace multiple dashes with single
    title = title.strip("-")  # Remove leading/trailing dashes
    return title


def _similarity(a: str, b: str) -> float:
    """Calculate similarity between two strings (0.0 to 1.0)."""
    return SequenceMatcher(None, a.lower(), b.lower()).ratio()


def _extract_prices_from_html(html: str) -> tuple:
    """
    Extract prices and title from HTML.
    
    Returns: (current_price, regular_price, title, discount_pct)
    """
    soup = BeautifulSoup(html, 'html.parser')
    
    # Try to find the game title from page
    title = None
    
    # Method 1: Look for h1 tag (usually the title)
    h1 = soup.find('h1')
    if h1:
        title = h1.get_text(strip=True)
    
    # Method 2: Look for meta title
    if not title:
        meta_title = soup.find('meta', {'property': 'og:title'})
        if meta_title:
            title = meta_title.get('content', '').strip()
    
    # Method 3: Look in page title
    if not title:
        page_title = soup.find('title')
        if page_title:
            title = page_title.get_text(strip=True).split('|')[0].strip()
    
    # Extract prices using regex
    prices = re.findall(r'GBP ([\d.]+)', html)
    
    if not prices:
        # Try meta tag
        meta_price = soup.find('meta', {'itemprop': 'price'})
        if meta_price:
            price_str = meta_price.get('content', '')
            if price_str:
                prices = [float(price_str)]
    
    if not prices:
        return None, None, title, 0
    
    # On Loaded.com, first price is usually the regular (original) price,
    # second price is the current (discounted) price
    regular_price = float(prices[0])
    current_price = float(prices[1]) if len(prices) > 1 else regular_price
    
    # If current price is higher than regular, they're swapped - fix it
    if current_price > regular_price and len(prices) > 1:
        current_price, regular_price = regular_price, current_price
    
    # Calculate discount
    discount_pct = 0
    if regular_price > 0:
        discount_pct = int((regular_price - current_price) / regular_price * 100)
    
    return current_price, regular_price, title, discount_pct


def scrape_game_price(game_title: str, platform: str = "pc", drm: str = "steam") -> Optional[Dict]:
    """
    Scrape game price from Loaded.com using BeautifulSoup.
    
    Handles title matching issues:
    - Tries exact URL match first
    - Falls back to fuzzy matching if exact fails
    - Extracts actual title from page
    
    Args:
        game_title: Game name (can be approximate)
        platform: Platform (default "pc")
        drm: DRM type (default "steam")
    
    Returns:
        Dict with price info or None if not found
    """
    
    _enforce_rate_limit()
    
    normalized_title = _normalize_game_title(game_title)
    url = f"{LOADED_BASE}/{normalized_title}-{platform}-{drm}"
    
    print(f"[Loaded] BS4: {url}")
    
    try:
        resp = requests.get(url, headers=HEADERS, timeout=LOADED_TIMEOUT)
        
        # 404: Try wildcard patterns first, then search
        if resp.status_code == 404:
            print(f"[Loaded] 404, trying wildcard patterns...")
            wildcard_url = search_loaded_with_wildcards(game_title, platform, drm)
            if wildcard_url:
                resp = requests.get(wildcard_url, headers=HEADERS, timeout=LOADED_TIMEOUT)
                if resp.status_code != 200:
                    return None
            else:
                return None
        
        # 403: Rate limited - increase delays
        if resp.status_code == 403:
            print(f"[Loaded] 403 Forbidden - IP rate limited")
            _handle_rate_limit_error()
            return None
        
        # Other errors
        if resp.status_code != 200:
            print(f"[Loaded] HTTP {resp.status_code}")
            return None
        
        resp.raise_for_status()
        html = resp.text
        
        # Extract prices and title
        current_price, regular_price, page_title, discount_pct = _extract_prices_from_html(html)
        
        if current_price is None:
            print(f"[Loaded] No price found")
            return None
        
        # Use extracted title if available, otherwise use input
        final_title = page_title if page_title else game_title
        
        print(f"[Loaded] [OK] Found: GBP {current_price:.2f} (was GBP {regular_price:.2f}, {discount_pct}% off)")
        if page_title and _similarity(page_title, game_title) < 0.7:
            print(f"[Loaded]   (Title match: '{page_title}' vs '{game_title}')")
        
        # Reset rate limit counters on success
        _reset_rate_limit_on_success()
        
        return {
            "price": current_price,
            "regular_price": regular_price,
            "discount_pct": discount_pct,
            "currency": "GBP",
            "drm": "Steam",  # Could extract from page
            "url": resp.url,  # Use final URL (after redirects)
            "in_stock": True,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
    
    except requests.exceptions.Timeout:
        print(f"[Loaded] Timeout: {game_title}")
        return None
    except requests.exceptions.ConnectionError:
        print(f"[Loaded] Connection error: {game_title}")
        return None
    except Exception as e:
        print(f"[Loaded] Error: {game_title}: {e}")
        return None


def search_loaded_with_wildcards(game_title: str, platform: str = "pc", drm: str = "steam") -> Optional[str]:
    """
    Try to find a game URL using wildcard patterns.
    
    Handles cases like:
    - "Lords of the Fallen" -> /lords-of-the-fallen-*-pc-steam
    - "Cyberpunk 2077" -> /cyberpunk-2077-*-pc-steam
    
    Tries common wildcard patterns:
    1. No suffix: /lords-of-the-fallen-pc-steam
    2. With year: /lords-of-the-fallen-2023-pc-steam
    3. With edition: /lords-of-the-fallen-deluxe-pc-steam
    4. Full search fallback
    
    Args:
        game_title: Game name
        platform: Platform (default "pc")
        drm: DRM type (default "steam")
    
    Returns:
        URL string or None if not found
    """
    
    normalized_title = _normalize_game_title(game_title)
    
    # Try common patterns
    patterns = [
        f"{normalized_title}-{platform}-{drm}",  # Exact
        f"{normalized_title}-2024-{platform}-{drm}",  # With current year
        f"{normalized_title}-2023-{platform}-{drm}",  # Previous year
        f"{normalized_title}-2022-{platform}-{drm}",  # 2 years ago
        f"{normalized_title}-deluxe-{platform}-{drm}",  # Deluxe edition
        f"{normalized_title}-ultimate-{platform}-{drm}",  # Ultimate edition
        f"{normalized_title}-standard-{platform}-{drm}",  # Standard edition
        f"{normalized_title}-{platform}-{drm}-cd-key",  # CD key variant
    ]
    
    print(f"[Loaded] Trying wildcard patterns for: {game_title}")
    
    for pattern in patterns:
        url = f"{LOADED_BASE}/{pattern}"
        try:
            resp = requests.get(url, headers=HEADERS, timeout=LOADED_TIMEOUT, allow_redirects=False)
            if resp.status_code == 200:
                # Verify URL contains -pc- (skip Xbox, PSN, etc.)
                if '-pc-' in url:
                    print(f"[Loaded] Wildcard match found: {url}")
                    return url
        except:
            pass
    
    # If no pattern matched, fall back to search
    print(f"[Loaded] No wildcard match, falling back to search...")
    return search_loaded_for_game(game_title)


def search_loaded_for_game(game_title: str) -> Optional[str]:
    """
    Search Loaded.com for a game and return the product URL.
    
    Useful for when exact title matching fails.
    
    Args:
        game_title: Game name to search for
    
    Returns:
        URL string or None if not found
    """
    
    _enforce_rate_limit()
    
    search_url = f"{LOADED_BASE}/search"
    params = {"q": game_title}
    
    print(f"[Loaded] Searching Loaded.com for: {game_title}")
    
    try:
        resp = requests.get(search_url, params=params, headers=HEADERS, timeout=LOADED_TIMEOUT)
        resp.raise_for_status()
        
        soup = BeautifulSoup(resp.text, 'html.parser')
        
        # Find first product link (try multiple selectors)
        product_link = None
        for selector in ['a.product-name', 'a[data-product]', 'a.product-link']:
            product_link = soup.select_one(selector)
            if product_link:
                break
        
        # If not found with selector, find first 'a' tag that looks like a product
        if not product_link:
            for link in soup.find_all('a'):
                href = link.get('href', '')
                # Look for game URLs on Loaded.com
                if '/pc-steam' in href or '/pc-' in href:
                    product_link = link
                    break
        
        if product_link:
            href = product_link.get('href')
            if href:
                full_url = href if href.startswith('http') else f"{LOADED_BASE}{href}"
                # Verify it's a valid Loaded.com product URL with PC platform
                # (exclude Xbox, PlayStation, Nintendo, etc.)
                if 'loaded.com' in full_url and '-pc-' in full_url:
                    print(f"[Loaded] Search found: {full_url}")
                    return full_url
        
        # If not found with first selector, try multiple links looking for PC only
        for link in soup.find_all('a'):
            href = link.get('href', '')
            # Look for PC game URLs only
            if '/pc-' in href and 'loaded.com' in href:
                full_url = href if href.startswith('http') else f"{LOADED_BASE}{href}"
                # Double-check it's PC (not xbox, psn, etc.)
                if '-pc-' in full_url and not any(x in full_url for x in ['-xbox', '-psn', '-switch']):
                    print(f"[Loaded] Search found: {full_url}")
                    return full_url
        
        print(f"[Loaded] Search found no valid PC results")
        return None
    
    except Exception as e:
        print(f"[Loaded] Search error: {e}")
        return None
