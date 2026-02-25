# 🎮 Steam Wishlist Price Tracker

Track your Steam wishlist prices across **30+ stores** — all prices in **GBP (£)**. Includes a web UI, price history charts, historic lows, bundle detection, and Loaded.com integration.

## Features

- ✓ **Steam Wishlist Sync** - Import all your wishlist games automatically
- ✓ **Multi-Store Prices** - Compare prices across 30+ stores (ITAD)
- ✓ **Loaded.com Support** - Native GBP prices with Selenium-powered search
- ✓ **Price History** - Track price trends with interactive charts
- ✓ **Historic Lows** - Know when games hit all-time lowest prices
- ✓ **Bundle Detection** - Find games in bundle deals (Humble, Fanatical, etc)
- ✓ **Web Dashboard** - Dark-themed UI for browsing deals
- ✓ **CLI Tools** - Command-line interface for scripting and automation
- ✓ **Stock Detection** - Skip sold-out and coming-soon games
- ✓ **Filters & Search** - Filter by discount %, on-sale status, bundles

## System Architecture

```
┌─────────────────────────────────────────────────────────────┐
│                    Steam Wishlist Tracker                   │
├─────────────────────────────────────────────────────────────┤
│                                                             │
│  ┌──────────────┐      ┌──────────────┐    ┌──────────────┐ │
│  │   Flask UI   │      │  CLI Tools   │    │   Database   │ │
│  │  (Web)       │      │  (main.py)   │    │  (SQLite)    │ │
│  └──────────────┘      └──────────────┘    └──────────────┘ │
│         ↓                      ↓                     ↓      │
│  ┌─────────────────────────────────────────────────────┐    │
│  │           Data Layer (database.py)                  │    │
│  │  - Games table                                      │    │
│  │  - Prices table (current prices per store)          │    │
│  │  - Price History (append-only log)                  │    │
│  │  - Historic Lows (all-time lowest per store)        │    │
│  │  - Bundles (bundle appearances)                     │    │
│  └─────────────────────────────────────────────────────┘    │
│         ↓              ↓              ↓                     │
│  ┌──────────────┐ ┌──────────────┐ ┌──────────────┐         │
│  │ Steam API    │ │ ITAD API     │ │ Loaded.com   │         │
│  │ (steam.py)   │ │ (itad.py)    │ │ (loaded_bs4) │         │
│  │              │ │              │ │              │         │
│  │ - Wishlist   │ │ - Prices     │ │ - Scraping   │         │
│  │ - Game Info  │ │ - Low prices │ │ - Search     │         │
│  │              │ │ - Bundles    │ │ - Stock info │         │
│  └──────────────┘ └──────────────┘ └──────────────┘         │
│         ↑              ↑              ↑                     │
└─────────────────────────────────────────────────────────────┘
         API Keys (see Setup below)
```

## Quick Start

### 1. Install Dependencies

```bash
pip install -r requirements.txt
```

### 2. Get API Keys

| Service | Key | Where | Required |
|---------|-----|-------|----------|
| **Steam** | API Key | https://steamcommunity.com/dev/apikey | ✓ Yes |
| **Steam** | ID64 | https://steamdb.info/calculator/ | ✓ Yes |
| **ITAD** | API Key | https://isthereanydeal.com/apps/my/ | Optional |
| **ChromeDriver** | (auto) | `pip install webdriver-manager` | For Loaded.com |

### 3. Start the Web UI

```bash
python app.py
```

Open **http://localhost:8080** in your browser.

Go to **Settings** → Enter your Steam credentials → Click **Start Full Sync**

### 4. (Optional) Use CLI Instead

```bash
# Sync wishlist + fetch prices
python main.py sync \
  --steam-id 76561198XXXXXXXXX \
  --steam-key YOUR_STEAM_KEY \
  --itad-key YOUR_ITAD_KEY

# Show all deals sorted by discount
python main.py report

# Show only games on sale
python main.py report --on-sale

# Show games with 50%+ discount
python main.py report --min-discount 50

# Show details for one game
python main.py game 1091500
```

## Installation Details

### Prerequisites

- Python 3.7+
- pip (Python package manager)
- Internet connection
- Steam account (public profile)

### Step 1: Clone or Download Project

```bash
cd /path/to/wishlist-tracker
```

### Step 2: Install Python Packages

```bash
pip install -r requirements.txt
```

This installs:
- **Flask** - Web framework
- **Requests** - HTTP client
- **BeautifulSoup4** - HTML parser
- **Selenium** - Browser automation
- **python-dotenv** - Environment variables (optional)

### Step 3: Install ChromeDriver (For Loaded.com Support)

**Option A: Automatic (Recommended)**

```bash
pip install webdriver-manager
```

The scraper will auto-download the correct ChromeDriver version.

**Option B: Manual**

1. Download: https://chromedriver.chromium.org/
2. Extract to `/usr/local/bin/chromedriver` or add to PATH
3. Verify: `chromedriver --version`

### Step 4: Create Data Directory

The tracker creates a SQLite database automatically:

```bash
mkdir -p data/
```

## Configuration

### Environment Variables (Optional)

Create a `.env` file in the project root:

```env
STEAM_ID=76561198XXXXXXXXX
STEAM_API_KEY=YOUR_STEAM_KEY
ITAD_API_KEY=YOUR_ITAD_API_KEY
FLASK_ENV=production
```

Then run without command-line arguments:

```bash
python main.py sync  # Uses .env file
python app.py        # Uses .env file
```

### Database Location

Default: `data/wishlist.db`

To change, edit `database.py`:

```python
DB_PATH = Path("/custom/path/wishlist.db")
```

## Project Structure

```
wishlist-tracker/
│
├── README.md                    ← This file
├── requirements.txt             ← Python dependencies
├── .env.example                 ← Copy to .env
│
├── app.py                       ← Flask web server (START HERE for UI)
├── main.py                      ← CLI entry point (for command line)
│
├── src/
│   ├── database.py              ← SQLite schema + queries
│   ├── steam.py                 ← Steam API (wishlist + game info)
│   ├── itad.py                  ← IsThereAnyDeal API (prices, lows, bundles)
│   └── loaded_bs4.py            ← Loaded.com scraper (with Selenium)
│
├── templates/
│   ├── base.html                ← Shared layout, nav, styles
│   ├── index.html               ← Dashboard (all deals)
│   ├── game.html                ← Game detail page
│   └── settings.html            ← API keys + sync controls
│
└── data/
    └── wishlist.db              ← SQLite database (auto-created)
```

## Usage

### Web UI (Recommended for Most Users)

```bash
python app.py
```

Then open http://localhost:8080 in your browser.

**Features:**
- 📊 Dashboard with stats (games, on sale, prices tracked, bundles)
- 🔍 Search by game name
- 🏷️ Filter by discount (50%+, 75%+, on sale only)
- ⬡ Filter by bundles
- 🗑️ Exclude specific stores
- 💹 Price history charts per game
- 📦 Bundle history
- ⏱️ Real-time sync status

**Workflow:**
1. Go to **Settings** tab
2. Enter Steam ID64, Steam API Key, ITAD API Key (optional)
3. Click **"Start Full Sync"** or **"Steam Only"**
4. Wait for sync to complete
5. Browse deals on **Deals** tab

### Command Line (For Scripting)

```bash
# Full sync (Steam + ITAD + Loaded)
python main.py sync \
  --steam-id 76561198XXXXXXXXX \
  --steam-key YOUR_STEAM_KEY \
  --itad-key YOUR_ITAD_KEY

# Steam only
python main.py sync --steam-id ... --steam-key ...

# Show report
python main.py report
python main.py report --on-sale
python main.py report --min-discount 75

# Game details
python main.py game 1091500           # By App ID
python main.py game "Baldur's Gate 3" # By name

# List all games
python main.py list

# Clear database (careful!)
python main.py clear
```

### Schedule Automatic Syncs

#### Mac/Linux (Cron)

```bash
crontab -e
```

Add:

```cron
# Daily at 8:00 AM
0 8 * * * cd /path/to/wishlist-tracker && python main.py sync --steam-id YOUR_ID --steam-key YOUR_KEY --itad-key YOUR_ITAD_KEY >> data/sync.log 2>&1

# Every 6 hours
0 */6 * * * cd /path/to/wishlist-tracker && python main.py sync ... >> data/sync.log 2>&1
```

#### Windows (Task Scheduler)

1. Open **Task Scheduler**
2. Click **Create Basic Task**
3. **Name:** "Wishlist Tracker Sync"
4. **Trigger:** Daily (choose time)
5. **Action:**
   - Program: `C:\Python311\python.exe`
   - Arguments: `C:\path\to\wishlist-tracker\main.py sync --steam-id YOUR_ID --steam-key YOUR_KEY`
   - Start in: `C:\path\to\wishlist-tracker`
6. Click **Create**

## API Keys

### Steam API Key & ID

1. **Get Steam API Key:**
   - Go to: https://steamcommunity.com/dev/apikey
   - Log in with your Steam account
   - Register any domain name (can be fake like `localhost`)
   - Copy the 32-character key

2. **Get Steam ID64:**
   - Go to: https://steamdb.info/calculator/
   - Enter your Steam username/profile URL
   - Copy the 17-digit number under "SteamID64"

3. **Make Profile Public:**
   - Steam → Profile → Edit Profile
   - Privacy Settings:
     - Profile visibility: **Public**
     - Game details: **Public**
   - Save

### ITAD API Key (Optional)

1. Go to: https://isthereanydeal.com/apps/my/
2. Log in or create account (free)
3. Click **Register an app**
4. Fill form (any app name/website)
5. Copy the **API Key**

This enables:
- ✓ Prices from 30+ stores
- ✓ Historic low tracking
- ✓ Bundle detection
- ✓ Better deal analysis

Without it, only Loaded.com prices are available.

## Data & Storage

### SQLite Database

File: `data/wishlist.db`

**Tables:**
- `games` - Your Steam wishlist items
- `prices` - Current prices per store
- `price_history` - All historical prices (append-only)
- `historic_lows` - All-time lowest per store
- `bundles` - Bundle appearances

### What Gets Stored

- Game title, Steam URL, header image
- Store prices (current + regular)
- Discount percentages
- Bundle information
- Price history (for charts)
- Last sync timestamps

**Privacy:**
- Keys are **never** stored (only used during sync)
- All data stored locally in SQLite
- No data sent to external servers except API calls
- Can delete `data/wishlist.db` to start fresh

## Syncing Explained

### Steam Sync

1. Fetches your wishlist from Steam API
2. For each game, gets: title, App ID, header image, store URL
3. Saves to `games` table
4. ~1 minute for 100 games (with rate limiting)

### ITAD Sync

1. Converts Steam App IDs → ITAD UUIDs
2. For each game, fetches:
   - Current prices across 30+ stores
   - All-time lowest price per store
   - Bundle history
3. Saves to `prices`, `price_history`, `bundles` tables
4. ~2-5 minutes for 100 games

### Loaded.com Sync

1. For each game, searches Loaded.com
2. Extracts GBP prices
3. Detects stock status (in stock/sold out/coming soon)
4. Saves to `prices` table
5. ~1-2 seconds per game (direct URL) + 3-5 seconds per search

## Troubleshooting

### "No wishlist items found"

**Problem:** Steam API returns empty wishlist

**Solution:**
1. Check Steam profile is **Public** (see API Keys section)
2. Verify Steam ID64 is correct (use https://steamdb.info/calculator/)
3. Make sure you have items on your wishlist
4. Check Steam API key is valid

### "403 Forbidden" from Loaded.com

**Problem:** Loaded.com blocked the request

**Solution:**
1. Wait 5-10 minutes (rate limit backoff)
2. Check your internet connection
3. Verify Loaded.com is online: https://www.loaded.com
4. Try using a VPN

### "chromedriver not found"

**Problem:** Selenium can't find ChromeDriver

**Solution:**
1. Install webdriver-manager: `pip install webdriver-manager`
2. Or download manually: https://chromedriver.chromium.org/
3. Add to PATH or specify location

### "Port 8080 in use"

**Problem:** Another app is using port 8080

**Solution:**

Edit `app.py`:

```python
if __name__ == "__main__":
    app.run(host="0.0.0.0", port=9000, debug=False)  # Change 8080 to 9000
```

Then run: `python app.py`

### "ITAD returns no prices"

**Problem:** ITAD API returned no data

**Possible causes:**
1. API key not working - verify at https://isthereanydeal.com/dev/keys/
2. Game not on ITAD - not all games are tracked
3. Regional restrictions - some games regional locked
4. Rate limited - ITAD free tier has limits

**Solution:**
- Use Loaded.com only (no ITAD key needed)
- Or upgrade ITAD to paid plan

### "ModuleNotFoundError"

**Problem:** Missing Python package

**Solution:**

```bash
pip install -r requirements.txt
```

Or install specific package:

```bash
pip install beautifulsoup4
pip install selenium
pip install flask
```

### Search returns wrong game

**Problem:** Loaded.com search found incorrect game

**Solution:**
1. Use full official title (e.g., "Final Fantasy VII Remake Intergrade")
2. Check game exists on Loaded.com manually
3. Some games may not be available on Loaded.com

## Performance

### Sync Times

| Operation | Time | Notes |
|-----------|------|-------|
| Steam sync (100 games) | ~2-3 min | Fetches wishlist + game info |
| ITAD sync (100 games) | ~5-10 min | Hits 30+ stores, gets history |
| Loaded search (100 games) | ~5-15 min | Selenium browser per search |
| Total for 100 games | ~15-30 min | Depends on network + store availability |

### Database Size

- 100 games: ~2-5 MB
- 1000 games: ~20-50 MB
- SQLite can handle 100K+ games easily

## Integration Examples

### Add to Your App

```python
from src.database import get_deals_report

# Get all deals sorted by discount
deals = get_deals_report()

for deal in deals:
    print(f"{deal['title']}: £{deal['best_price']} at {deal['best_store']}")
```

### Export to CSV

```python
import csv
from src.database import get_deals_report

deals = get_deals_report()

with open('deals.csv', 'w') as f:
    writer = csv.DictWriter(f, fieldnames=deals[0].keys())
    writer.writeheader()
    writer.writerows(deals)
```

### Email Notifications

```python
from src.database import get_deals_report

deals = get_deals_report()

# Games with 50%+ off
hot_deals = [d for d in deals if d['best_discount'] >= 50]

# Email to yourself with hot_deals
```

## Contributing

To extend the tracker:

1. **Add a new store:**
   - Create `src/newstore.py`
   - Implement `scrape_game_price(title)` function
   - Return dict: `{price, regular_price, discount_pct, url}`
   - Add to main sync in `itad.py`

2. **Modify UI:**
   - Edit templates in `templates/`
   - Styles in `base.html` (Tailwind CSS)

3. **Add features:**
   - New filters in `index.html`
   - New API endpoints in `app.py`

## Support & Issues

If you encounter problems:

1. **Check logs:**
   ```bash
   tail -f data/sync.log  # If scheduled with cron
   ```

2. **Test manually:**
   ```bash
   python main.py sync --steam-id YOUR_ID --steam-key YOUR_KEY
   ```

3. **Check dependencies:**
   ```bash
   pip list | grep -E "requests|beautifulsoup4|selenium|flask"
   ```

4. **Verify API keys:**
   - Steam: https://steamcommunity.com/dev/apikey
   - ITAD: https://isthereanydeal.com/dev/keys/

## License

MIT License - Use freely for personal use.

## Notes

- All prices in **GBP (British Pounds)**
- Works worldwide but game availability varies by region
- Some games may not be available on all stores
- Prices update when you run sync
- Historic lows tracked across all time you've used the tracker
- Database stays local - no cloud sync

## Roadmap

Future features:
- [ ] Email/webhook alerts for deals
- [ ] Price drop notifications
- [ ] Multi-region support (USD, EUR, etc)
- [ ] More store integrations

## License & Attribution

- Uses **Steam API** (Valve) - See terms at https://steamcommunity.com/dev
- Uses **IsThereAnyDeal API** - See https://isthereanydeal.com
- Scrapes **Loaded.com** - See their terms

---

**Last Updated:** February 2026  
**Version:** 2.0  
**Python:** 3.7+  
**Status:** ✓ Stable

**Get help:** Check the Troubleshooting section above or run `python main.py --help`
