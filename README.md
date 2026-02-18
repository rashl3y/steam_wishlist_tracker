# 🎮 Steam Wishlist Price Tracker v2

Track your Steam wishlist prices across stores — all in **GBP (£)**. Includes a local web UI, price history charts, historic lows, and bundle detection.

---

## What's New in v2

- **All prices in GBP** — ITAD fetches with `country=GB`, Loaded.com is natively £
- **Web GUI** — beautiful dark dashboard running locally in your browser
- **Price history charts** — per-game Chart.js line chart across stores

---

## Setup

### 1. Install dependencies

```bash
pip install -r requirements.txt
```

Python 3.10+ required.

### 2. Get API keys

| Key | Where |
|---|---|
| **Steam API Key** | https://steamcommunity.com/dev/apikey |
| **Steam ID64** | https://www.steamidfinder.com (your 17-digit ID) |
| **ITAD API Key** | https://isthereanydeal.com/dev/app/ (free) |

Your Steam profile must be **Public** for the wishlist API to work.

### 3. Start the web app

```bash
python app.py
```

Open **http://localhost:5000** in your browser.

Enter your keys on the **Sync** page and click **Start Full Sync**.

---

## CLI (alternative to the web UI)

```bash
python main.py sync \
  --steam-id   76561198XXXXXXXXX \
  --steam-key  YOUR_STEAM_KEY \
  --itad-key   YOUR_ITAD_KEY

python main.py report
python main.py report --on-sale
python main.py report --min-discount 50
python main.py game 1091500
```

---

## Project Structure

```
wishlist-tracker/
│
├── app.py               ← Flask web server (start here)
├── main.py              ← CLI entry point
├── requirements.txt
├── data/
│   └── wishlist.db      ← SQLite database (auto-created)
│
├── templates/
│   ├── base.html        ← Shared layout, nav, styles
│   ├── index.html       ← Deals dashboard
│   ├── game.html        ← Game detail + chart + bundles
│   └── settings.html    ← Sync / API key page
│
└── src/
    ├── database.py      ← Schema + all SQL queries
    ├── steam.py         ← Steam API integration
    ├── itad.py          ← ITAD API (prices, lows, bundles) — GBP via country=GB
```

---

## Scheduling Regular Syncs

### Mac/Linux (cron)
```bash
crontab -e
# Run every day at 8am:
0 8 * * * cd /path/to/wishlist-tracker && python main.py sync >> data/sync.log 2>&1
```

### Windows (Task Scheduler)
1. Open Task Scheduler → Create Basic Task
2. Trigger: Daily
3. Action: Start a program → `python`
4. Arguments: `C:\path\to\wishlist-tracker\main.py sync`

---

## Troubleshooting

**Loaded scraper returns 403**
```bash
```

**"No wishlist items found"**
Set Steam → Privacy Settings → Profile & Game Details → **Public**

**ITAD returns no prices**
Verify your key at https://isthereanydeal.com/dev/keys/ — free keys have rate limits but no paywalls.

**Port 5000 in use**
```bash
python app.py  # edit the port in app.py → app.run(port=5001)
```

On macOS, AirPlay Receiver uses port 5000. Disable it in System Settings → General → AirDrop & Handoff, or change the port.
