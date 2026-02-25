"""
loaded_bs4.py
-------------
Scrapes game prices from loaded.com using BeautifulSoup (lightweight & fast).

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


def _to_ascii(text: str) -> str:
    """Convert text to ASCII only, removing non-ASCII characters."""
    if not text:
        return ""
    # Remove non-ASCII characters
    ascii_text = text.encode('ascii', 'ignore').decode('ascii')
    return ascii_text


def _extract_prices_from_html(html: str) -> tuple:
    """
    Extract prices and title from HTML.
    
    Loaded.com structure:
    - Old/full price (strikethrough): <div class="old-price"><span class="price">£64.99</span></div>
    - Current price: <div class="final-price"><span class="price">£25.99</span></div>
    - Meta tag (backup): <meta itemprop="price" content="25.99">
    
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
    
    # Check for stock status badges - if game is Sold Out or Coming Soon, return None
    # Look for badges with red background or status indicators
    page_text = soup.get_text()
    page_text_lower = page_text.lower()
    
    # Check for explicit "Sold Out" or "Coming Soon" text on the page
    if 'sold out' in page_text_lower or 'coming soon' in page_text_lower:
        # Look for the badge/status element
        for elem in soup.find_all(['div', 'span']):
            elem_text = elem.get_text(strip=True).lower()
            if 'sold out' in elem_text:
                print(f"[Loaded] [INFO] Game is SOLD OUT - not returning prices")
                return None, None, title, 0
            elif 'coming soon' in elem_text:
                print(f"[Loaded] [INFO] Game is COMING SOON - not returning prices")
                return None, None, title, 0
    
    # Also check if the page shows "not available" or "unavailable"
    if 'unavailable' in page_text_lower or 'not available' in page_text_lower:
        print(f"[Loaded] [INFO] Game is UNAVAILABLE - not returning prices")
        return None, None, title, 0
    
    current_price = None
    regular_price = None
    
    # Extract current price from .final-price (current/discounted price)
    # Structure: <div class="final-price"><span class="price">£25.99</span></div>
    final_price_elem = soup.find('div', class_='final-price')
    if final_price_elem:
        price_span = final_price_elem.find('span', class_='price')
        if price_span:
            price_text = price_span.get_text(strip=True)
            # Extract number from "£25.99" format
            match = re.search(r'[\u00a3$]?\s*([\d.]+)', price_text)
            if match:
                try:
                    current_price = float(match.group(1))
                except:
                    pass
    
    # Extract regular/full price from .old-price (strikethrough)
    # Structure: <div class="old-price"><span class="price">£64.99</span></div>
    old_price_elem = soup.find('div', class_='old-price')
    if old_price_elem:
        price_span = old_price_elem.find('span', class_='price')
        if price_span:
            price_text = price_span.get_text(strip=True)
            # Extract number from "£64.99" format
            match = re.search(r'[\u00a3$]?\s*([\d.]+)', price_text)
            if match:
                try:
                    regular_price = float(match.group(1))
                except:
                    pass
    
    # Fallback: Extract current price from meta tags if not found in HTML
    if not current_price:
        meta_price = soup.find('meta', {'itemprop': 'price'})
        if meta_price:
            price_str = meta_price.get('content', '')
            if price_str:
                try:
                    current_price = float(price_str)
                except:
                    pass
    
    # If only got current price, use it for both (no discount)
    if current_price and not regular_price:
        regular_price = current_price
    
    # If no prices found at all
    if not current_price:
        return None, None, title, 0
    
    # Calculate discount
    discount_pct = 0
    if regular_price and regular_price > 0 and current_price:
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
            print(f"[Loaded]   (Title match: '{_to_ascii(page_title)}' vs '{_to_ascii(game_title)}')")
        
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
    Search Loaded.com for a game using hash-based search URL.
    
    Uses Selenium to render the page with JavaScript (which loads the search results).
    
    Format: https://www.loaded.com/#q=ratchet%20clank%20rift%20apart
    (spaces are URL-encoded as %20)
    
    Prioritizes worldwide (WW) version, avoids regional variants (EU, UK, etc).
    Verifies the result actually matches the game title we searched for.
    
    Args:
        game_title: Game name to search for
    
    Returns:
        URL string or None if not found
    """
    
    _enforce_rate_limit()
    
    # Convert title: replace special characters with spaces, then URL encode
    search_term = re.sub(r'[^a-z0-9\s]', ' ', game_title.lower())
    search_term = re.sub(r'\s+', ' ', search_term).strip()
    search_term = search_term.replace(' ', '%20')
    
    search_url = f"{LOADED_BASE}/#q={search_term}"
    
    print(f"[Loaded] Searching Loaded.com for: {_to_ascii(game_title)}")
    print(f"[Loaded] Search URL: {search_url}")
    
    try:
        # Try Selenium first (if available)
        try:
            from selenium import webdriver
            from selenium.webdriver.common.by import By
            from selenium.webdriver.support.ui import WebDriverWait
            from selenium.webdriver.support import expected_conditions as EC
            from selenium.webdriver.chrome.options import Options
            
            # Configure headless browser
            chrome_options = Options()
            chrome_options.add_argument('--headless')
            chrome_options.add_argument('--no-sandbox')
            chrome_options.add_argument('--disable-dev-shm-usage')
            chrome_options.add_argument('user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36')
            
            driver = webdriver.Chrome(options=chrome_options)
            
            try:
                # Load the search page
                driver.get(search_url)
                
                # Wait for search results to load (up to 10 seconds)
                wait = WebDriverWait(driver, 10)
                wait.until(EC.presence_of_all_elements_located((By.CLASS_NAME, 'algolia-hit-link')))
                
                # Get the rendered HTML
                html = driver.page_source
                
                # Parse with BeautifulSoup
                soup = BeautifulSoup(html, 'html.parser')
                
                # Find algolia search results
                product_links = soup.find_all('a', class_='algolia-hit-link')
                
                if not product_links:
                    print(f"[Loaded] Search found no results")
                    return None
                
                # Go through each link and verify it matches our search
                search_terms_lower = game_title.lower()
                title_words = re.findall(r'\b[a-z0-9]+\b', search_terms_lower)
                
                # Separate WW and regional versions
                ww_candidates = []
                regional_candidates = []
                
                for link in product_links:
                    href = link.get('href', '')
                    link_text = link.get_text(strip=True).lower()
                    
                    # Check if it's a PC game link
                    if '-pc-' not in href or 'loaded.com' not in href:
                        continue
                    
                    # Exclude non-PC platforms
                    if any(x in href.lower() for x in ['-xbox', '-psn', '-switch']):
                        continue
                    
                    # Verify the link text contains the game title words
                    if title_words:
                        matches = sum(1 for word in title_words if word in link_text)
                        match_ratio = matches / len(title_words)
                        
                        if match_ratio >= 0.5:  # At least 50% of terms match
                            full_url = href if href.startswith('http') else f"{LOADED_BASE}{href}"
                            
                            # Check if it's a WW (worldwide) version or regional
                            # WW versions don't have -eu, -uk, -asia, etc in the URL or link text
                            is_regional = any(region in full_url.lower() for region in ['-eu', '-uk', '-asia', '-jp', '-au'])
                            is_regional = is_regional or any(region in link_text for region in ['eu &', 'uk)', 'asia', 'japan', 'australian'])
                            
                            if is_regional:
                                regional_candidates.append(full_url)
                            else:
                                ww_candidates.append(full_url)
                
                # Prefer WW version, fall back to regional if needed
                if ww_candidates:
                    result_url = ww_candidates[0]
                    print(f"[Loaded] Search found (WW): {result_url}")
                    return result_url
                elif regional_candidates:
                    result_url = regional_candidates[0]
                    print(f"[Loaded] Search found (regional): {result_url}")
                    return result_url
                
                print(f"[Loaded] Search found no matching PC results")
                return None
            
            finally:
                driver.quit()
        
        except ImportError:
            # Selenium not available, fall back to requests (won't work for JS-rendered content)
            print(f"[Loaded] [WARNING] Selenium not installed - search won't work (install: pip install selenium)")
            print(f"[Loaded] [WARNING] Falling back to basic requests (will likely fail)")
            
            resp = requests.get(search_url, headers=HEADERS, timeout=LOADED_TIMEOUT)
            resp.raise_for_status()
            
            soup = BeautifulSoup(resp.text, 'html.parser')
            product_links = soup.find_all('a', class_='algolia-hit-link')
            
            if not product_links:
                print(f"[Loaded] Search found no results")
                return None
            
            # Try first PC link (may not be correct)
            for link in product_links:
                href = link.get('href', '')
                if '-pc-' in href and 'loaded.com' in href:
                    print(f"[Loaded] Search found: {href}")
                    return href
            
            return None
    
    except Exception as e:
        print(f"[Loaded] Search error: {e}")
        return None
