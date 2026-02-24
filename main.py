"""
main.py
-------
Command-line interface for the Steam Wishlist Price Tracker.

Run this file to interact with the tool:
  python main.py sync fetch wishlist + all prices
  python main.py report show deals table
  python main.py game 570 detailed view for one game

Uses Python's built-in 'argparse' for CLI argument parsing.
No external frameworks needed.
"""

import argparse
import os
import sys
from pathlib import Path

# Load environment variables from .env file if it exists
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    # python-dotenv not installed, fall back to system env vars only
    pass

# Add src/ to the Python path so we can import our modules
sys.path.insert(0, str(Path(__file__).parent / "src"))

from src.database import (
    init_db,
    get_all_games,
    get_deals_report,
    get_game_price_history,
    get_game_bundles,
    get_stats,
    delete_game,
    clear_database,
)
from steam import sync_wishlist
from itad import sync_prices
from sync_loaded_helper import sync_loaded


# ANSI colour codes for terminal output
# These are escape codes that terminals interpret as colours.
# "\033[" starts the sequence, "m" ends it, "0m" resets to default.
GREEN  = "\033[92m"
YELLOW = "\033[93m"
RED    = "\033[91m"
CYAN   = "\033[96m"
BOLD   = "\033[1m"
DIM    = "\033[2m"
RESET  = "\033[0m"

# Width of columns in the report table
COL_TITLE   = 36
COL_STORE   = 14
COL_PRICE   = 10
COL_DISC    = 6
COL_HIST    = 12
COL_BUNDLES = 8


def colour_discount(pct: int) -> str:
    """Colour-code a discount percentage: green=great, yellow=ok, dim=none."""
    if pct is None or pct == 0:
        return f"{DIM}  0%{RESET}"
    elif pct >= 75:
        return f"{GREEN}{BOLD}{pct:3d}%{RESET}"
    elif pct >= 40:
        return f"{YELLOW}{pct:3d}%{RESET}"
    else:
        return f"{pct:3d}%"


def colour_vs_historic(current: float, historic: float) -> str:
    """
    Compare current price to historic low.
    Green = at or near historic low. Red = far above it.
    """
    if current is None or historic is None or historic == 0:
        return ""

    ratio = current / historic  # 1.0 = at historic low, 2.0 = double the low
    if ratio <= 1.05:
        return f" {GREEN}HISTORIC LOW{RESET}"
    elif ratio <= 1.25:
        return f" {YELLOW}near low{RESET}"
    else:
        pct_above = int((ratio - 1) * 100)
        return f" {DIM}+{pct_above}% above low{RESET}"


def fmt_price(price, currency="GBP") -> str:
    """Format a price for display. Handles None gracefully."""
    if price is None:
        return f"{DIM}N/A{RESET}"
    symbol = "Â£" if currency == "GBP" else "$"
    return f"{symbol}{price:,.2f}"


def cmd_sync(args) -> None:
    """
    Full sync: Steam wishlist ITAD prices.
    Reads credentials from args or falls back to environment variables.
    """
    steam_id  = args.steam_id  or os.getenv("STEAM_ID")
    steam_key = args.steam_key or os.getenv("STEAM_API_KEY")
    itad_key  = args.itad_key  or os.getenv("ITAD_API_KEY")

    if not steam_id or not steam_key:
        print(f"{RED}Error:{RESET} Steam ID and Steam API key are required.")
        print("  Pass them as --steam-id / --steam-key, or set environment variables:")
        print("    export STEAM_ID=76561198000000000")
        print("    export STEAM_API_KEY=your_key_here")
        sys.exit(1)

    print(f"\n{BOLD}{CYAN}Steam Wishlist Tracker{RESET}\n")

    # Step 1: Steam wishlist
    print(f"{BOLD}[1/2] Syncing Steam wishlist...{RESET}")
    sync_wishlist(steam_id, steam_key)

    # Step 2: ITAD prices
    if itad_key:
        print(f"\n{BOLD}[2/2] Fetching ITAD prices...{RESET}")
        sync_prices(itad_key)  # Removed country parameter
    else:
        print(f"\n{YELLOW}[2/2] Skipping ITAD (no --itad-key provided){RESET}")
        print("      Get a free key at https://isthereanydeal.com/dev/app/")

    # Step 3: Loaded.com prices
    print(f"\n{BOLD}[3/3] Syncing Loaded.com prices (this will take a few minutes)...{RESET}")
    try:
        sync_loaded()
    except Exception as loaded_err:
        print(f"{YELLOW}Warning: Loaded.com sync failed{RESET}")
        print(f"      {loaded_err}")

    print(f"\n{GREEN}“ Sync complete!{RESET} Run `python main.py report` to see deals.\n")


def cmd_report(args) -> None:
    """
    Print the deals report a formatted table of all wishlist games
    sorted by best current deal.
    """
    rows = get_deals_report()

    if not rows:
        print(f"\n{YELLOW}No data yet.{RESET} Run `python main.py sync` first.\n")
        return

    # Filter: only show games currently on sale, if requested
    if args.on_sale:
        rows = [r for r in rows if r.get("best_discount", 0) and r["best_discount"] > 0]

    # Filter: minimum discount threshold
    if args.min_discount:
        rows = [r for r in rows if (r.get("best_discount") or 0) >= args.min_discount]

    if not rows:
        print(f"\n{YELLOW}No games match your filters.{RESET}\n")
        return

    print(f"\n{BOLD}{CYAN}Wishlist Deals Report{RESET}")
    print(f"{DIM}{len(rows)} games | sorted by discount then price{RESET}\n")

    # Table header
    print(f"  {'GAME':<45} {'STORE':<20} {'PRICE':>10} {'DISC':>6} {'LOW':>10}")

    for r in rows:
        title = r["title"][:44] if r["title"] else "Unknown"
        store = r["best_store"][:19] if r.get("best_store") else "N/A"
        price = fmt_price(r["best_price"], r["currency"]) if r.get("best_price") else "N/A"
        disc  = f"{r['best_discount']}%" if r.get("best_discount") else "â€”"
        low   = fmt_price(r["historic_low"], r["currency"]) if r.get("historic_low") else "â€”"

        print(f"  {title:<45} {store:<20} {price:>10} {disc:>5}  {low:>10}")

    # Summary footer
    stats = get_stats()
    on_sale_count = sum(1 for r in rows if r.get("best_discount", 0) and r["best_discount"] > 0)
    print(f"{DIM}Summary: {on_sale_count}/{len(rows)} games on sale | {stats['total_bundles']} bundles tracked{RESET}")
    print()


def cmd_game(args) -> None:
    """Detailed view for a single game: price history + bundles.
    Accepts either Steam App ID (numeric) or game name (partial match, case-insensitive).
    """
    games = get_all_games()
    if not games:
        print(f"\n{YELLOW}No games in database.{RESET} Run `python main.py sync` first.\n")
        sys.exit(1)
    
    game = None

    # Check if input is numeric (App ID)
    try:
        app_id = int(args.game_id)
        game = next((g for g in games if g["app_id"] == app_id), None)
    except ValueError:
        # Not a number, search by game name (case-insensitive, partial match)
        search_term = str(args.game_id).lower()
        matches = [g for g in games if search_term in g["title"].lower()]
        
        if len(matches) == 1:
            game = matches[0]
        elif len(matches) > 1:
            print(f"\n{YELLOW}Multiple games found matching '{args.game_id}':{RESET}")
            for g in matches[:10]:  # Show first 10 matches
                print(f"{g['title']} (App ID: {g['app_id']})")
            if len(matches) > 10:
                print(f"  {DIM}... and {len(matches) - 10} more{RESET}")
            print(f"\n{DIM}Be more specific or use the App ID.{RESET}\n")
            sys.exit(1)

    if not game:
        print(f"\n{RED}Game '{args.game_id}' not found in database.{RESET}")
        print("Run `python main.py sync` first, or check the App ID / game name.\n")
        sys.exit(1)

    app_id = game["app_id"]
    print(f"\n{BOLD}{CYAN}{game['title']}{RESET}")
    print(f"{DIM}Steam App ID: {app_id}{RESET}")
    print(f"{DIM}URL: {game.get('steam_url', 'N/A')}{RESET}")
    print(f"{DIM}Last checked: {game.get('last_checked', 'Never')}{RESET}\n")

    # Price history
    history = get_game_price_history(app_id)
    if history:
        print(f"{BOLD}Price History ({len(history)} records):{RESET}")
        print(f"  {'DATE':<20} {'STORE':<20} {'PRICE':>10} {'DISC':>5}")
        for h in history[-20:]:  # show last 20 entries
            print(
                f"  {h['recorded_at'][:16]:<20} "
                f"{h['store'][:19]:<20} "
                f"{fmt_price(h['price'], h['currency']):>10} "
                f"{h['discount_pct']:>4}%"
            )
        if len(history) > 20:
            print(f"  {DIM}... and {len(history) - 20} older entries{RESET}")
    else:
        print(f"{DIM}No price history yet.{RESET}")

    # Bundles
    bundles = get_game_bundles(app_id)
    print()
    if bundles:
        print(f"{BOLD}Bundle History ({len(bundles)} bundles):{RESET}")
        for b in bundles:
            price_str = f"${b['tier_price']:.2f}" if b.get("tier_price") else "price N/A"
            expires   = f" | expires {b['expires_at']}" if b.get("expires_at") else ""
            print(f"{b['bundle_title']} ({b.get('store', '?')}){price_str}{expires}")
            if b.get("bundle_url"):
                print(f"    {DIM}{b['bundle_url']}{RESET}")
    else:
        print(f"{DIM}No bundle history found.{RESET}")
    print()


def cmd_list(args) -> None:
    """List all games in the database (quick overview)."""
    games = get_all_games()
    if not games:
        print(f"\n{YELLOW}Database is empty.{RESET} Run `python main.py sync` first.\n")
        return
    print(f"\n{BOLD}{CYAN}Wishlist Games ({len(games)} total){RESET}\n")
    for g in games:
        checked = g.get("last_checked", "never checked")[:16] if g.get("last_checked") else "never checked"
        print(f"  {g['app_id']:>10}  {g['title']:<45} {DIM}{checked}{RESET}")
    print()


def cmd_clear(args) -> None:
    """Clear all data from database and reinitialize."""
    confirm = input(f"{RED} This will delete ALL data. Type 'yes' to confirm: {RESET}")
    if confirm.lower() == "yes":
        from src.database import clear_database
        clear_database()
        print(f"{GREEN} Database cleared and reinitialized.{RESET}\n")
    else:
        print("Cancelled.\n")


# CLI setup

def main():
    # init_db() is safe to call every run creates tables only if missing
    init_db()

    parser = argparse.ArgumentParser(
        prog="wishlist-tracker",
        description="Steam Wishlist Price Tracker â€” compare prices across stores",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python main.py sync --steam-id 76561198000000000 --steam-key ABC123 --itad-key XYZ789
  python main.py report
  python main.py report --on-sale
  python main.py report --min-discount 50
  python main.py game 570
  python main.py list

Environment variables (alternative to flags):
  STEAM_ID          Your SteamID64
  STEAM_API_KEY     From https://steamcommunity.com/dev/apikey
  ITAD_API_KEY      From https://isthereanydeal.com/dev/app/
        """
    )

    subparsers = parser.add_subparsers(dest="command", help="Command to run")

    # sync subcommand
    sync_p = subparsers.add_parser("sync", help="Sync wishlist and fetch prices")
    sync_p.add_argument("--steam-id",   help="Your SteamID64 (17-digit number)")
    sync_p.add_argument("--steam-key",  help="Steam Web API key")
    sync_p.add_argument("--itad-key",   help="IsThereAnyDeal API key")
    # Removed: --country (not used)
    sync_p.set_defaults(func=cmd_sync)

    # eport subcommand
    report_p = subparsers.add_parser("report", help="Show deals report")
    report_p.add_argument("--on-sale", action="store_true", help="Only show games currently on sale")
    report_p.add_argument("--min-discount", type=int, metavar="PCT",
                          help="Only show games with at least PCT%% off (e.g. 50)")
    report_p.set_defaults(func=cmd_report)

    # game subcommand
    game_p = subparsers.add_parser("game", help="Detailed info for one game")
    game_p.add_argument("game_id", help="Steam App ID (e.g. 570) or game name (e.g. 'Dota 2')")
    game_p.set_defaults(func=cmd_game)

    # ist subcommand
    list_p = subparsers.add_parser("list", help="List all games in database")
    list_p.set_defaults(func=cmd_list)

    # clear subcommand
    clear_p = subparsers.add_parser("clear", help="Clear all database data")
    clear_p.set_defaults(func=cmd_clear)

    args = parser.parse_args()

    if not args.command:
        parser.print_help()
        sys.exit(0)

    args.func(args)


if __name__ == "__main__":
    main()
