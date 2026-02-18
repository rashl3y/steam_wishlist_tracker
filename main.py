"""
main.py
-------
Command-line interface for the Steam Wishlist Price Tracker.

Run this file to interact with the tool:
  python main.py sync    → fetch wishlist + all prices
  python main.py report  → show deals table
  python main.py game 570 → detailed view for one game

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

from database import init_db, get_deals_report, get_game_price_history, get_game_bundles, get_all_games
from steam import sync_wishlist
from itad import sync_prices


# ── ANSI colour codes for terminal output ─────────────────────────────────────
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
        return f" {GREEN}★ HISTORIC LOW{RESET}"
    elif ratio <= 1.25:
        return f" {YELLOW}↓ near low{RESET}"
    else:
        pct_above = int((ratio - 1) * 100)
        return f" {DIM}+{pct_above}% above low{RESET}"


def fmt_price(price, currency="GBP") -> str:
    """Format a price for display. Handles None gracefully."""
    if price is None:
        return f"{DIM}N/A{RESET}"
    symbol = "£" if currency == "GBP" else "$"
    return f"{symbol}{price:,.2f}"


def cmd_sync(args) -> None:
    """
    Full sync: Steam wishlist → ITAD prices.
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

    print(f"\n{BOLD}{CYAN}═══ Steam Wishlist Tracker ═══{RESET}\n")

    # Step 1: Steam wishlist
    print(f"{BOLD}[1/2] Syncing Steam wishlist...{RESET}")
    sync_wishlist(steam_id, steam_key)

    # Step 2: ITAD prices
    if itad_key:
        print(f"\n{BOLD}[2/2] Fetching ITAD prices...{RESET}")
        country = args.country or "GB"
        sync_prices(itad_key, country)
    else:
        print(f"\n{YELLOW}[2/2] Skipping ITAD (no --itad-key provided){RESET}")
        print("      Get a free key at https://isthereanydeal.com/dev/app/")



    print(f"\n{GREEN}✓ Sync complete!{RESET} Run `python main.py report` to see deals.\n")


def cmd_report(args) -> None:
    """
    Print the deals report — a formatted table of all wishlist games
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

    print(f"\n{BOLD}{CYAN}═══ Wishlist Deals Report ═══{RESET}")
    print(f"{DIM}{len(rows)} games | sorted by discount then price{RESET}\n")

    # ── Table header ──────────────────────────────────────────────────────────
    header = (
        f"{'GAME':<{COL_TITLE}} "
        f"{'STORE':<{COL_STORE}} "
        f"{'PRICE':>{COL_PRICE}} "
        f"{'OFF':>{COL_DISC}} "
        f"{'HIST. LOW':>{COL_HIST}} "
        f"{'BUNDLES':>{COL_BUNDLES}}"
    )
    print(f"{BOLD}{header}{RESET}")
    print("─" * (COL_TITLE + COL_STORE + COL_PRICE + COL_DISC + COL_HIST + COL_BUNDLES + 10))

    for row in rows:
        title = row["title"][:COL_TITLE - 1] if row["title"] else "Unknown"
        store = (row.get("best_store") or "—")[:COL_STORE - 1]
        price = fmt_price(row.get("best_price"), row.get("currency") or "GBP")
        disc  = colour_discount(row.get("best_discount") or 0)
        hist  = fmt_price(row.get("historic_low"), row.get("currency") or "GBP")
        bundles = row.get("num_bundles", 0)
        bundle_str = f"{GREEN}{bundles}{RESET}" if bundles > 0 else f"{DIM}{bundles}{RESET}"

        # Append indicator if at/near historic low
        vs_hist = colour_vs_historic(row.get("best_price"), row.get("historic_low"))

        print(
            f"{title:<{COL_TITLE}} "
            f"{store:<{COL_STORE}} "
            f"{price:>{COL_PRICE}} "
            f"{disc:>{COL_DISC + 12}} "  # extra padding for ANSI codes
            f"{hist:>{COL_HIST}} "
            f"{bundle_str:>{COL_BUNDLES}}"
            f"{vs_hist}"
        )

    print()

    # Summary stats
    with_price = [r for r in rows if r.get("best_price") is not None]
    on_sale = [r for r in with_price if (r.get("best_discount") or 0) > 0]
    at_low  = [r for r in with_price if r.get("historic_low") and
               r["best_price"] and r["best_price"] <= r["historic_low"] * 1.05]

    print(f"{DIM}Summary: {len(with_price)} priced | {len(on_sale)} on sale | "
          f"{len(at_low)} at/near historic low{RESET}\n")


def cmd_game(args) -> None:
    """Detailed view for a single game: price history + bundles."""
    app_id = args.app_id

    games = get_all_games()
    game = next((g for g in games if g["app_id"] == app_id), None)

    if not game:
        print(f"{RED}Game {app_id} not found in database.{RESET}")
        print("Run `python main.py sync` first, or check the App ID.")
        sys.exit(1)

    print(f"\n{BOLD}{CYAN}═══ {game['title']} ═══{RESET}")
    print(f"{DIM}Steam App ID: {app_id}{RESET}")
    print(f"{DIM}URL: {game.get('steam_url', 'N/A')}{RESET}")
    print(f"{DIM}Last checked: {game.get('last_checked', 'Never')}{RESET}\n")

    # ── Price history ─────────────────────────────────────────────────────────
    history = get_game_price_history(app_id)
    if history:
        print(f"{BOLD}Price History ({len(history)} records):{RESET}")
        print(f"  {'DATE':<20} {'STORE':<20} {'PRICE':>10} {'DISC':>5}")
        print("  " + "─" * 60)
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

    # ── Bundles ────────────────────────────────────────────────────────────────
    bundles = get_game_bundles(app_id)
    print()
    if bundles:
        print(f"{BOLD}Bundle History ({len(bundles)} bundles):{RESET}")
        for b in bundles:
            price_str = f"${b['tier_price']:.2f}" if b.get("tier_price") else "price N/A"
            expires   = f" | expires {b['expires_at']}" if b.get("expires_at") else ""
            print(f"  • {b['bundle_title']} ({b.get('store', '?')}) — {price_str}{expires}")
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
    print(f"\n{BOLD}{CYAN}═══ Wishlist Games ({len(games)} total) ═══{RESET}\n")
    for g in games:
        checked = g.get("last_checked", "never checked")[:16] if g.get("last_checked") else "never checked"
        print(f"  {g['app_id']:>10}  {g['title']:<45} {DIM}{checked}{RESET}")
    print()


# ── CLI setup ─────────────────────────────────────────────────────────────────

def main():
    # init_db() is safe to call every run — creates tables only if missing
    init_db()

    parser = argparse.ArgumentParser(
        prog="wishlist-tracker",
        description="Steam Wishlist Price Tracker — compare prices across stores",
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

    # ── sync subcommand ────────────────────────────────────────────────────────
    sync_p = subparsers.add_parser("sync", help="Sync wishlist and fetch prices")
    sync_p.add_argument("--steam-id",   help="Your SteamID64 (17-digit number)")
    sync_p.add_argument("--steam-key",  help="Steam Web API key")
    sync_p.add_argument("--itad-key",   help="IsThereAnyDeal API key")
    sync_p.add_argument("--country",    default="GB", help="Country code for prices (default: GB)")
    sync_p.set_defaults(func=cmd_sync)

    # ── report subcommand ──────────────────────────────────────────────────────
    report_p = subparsers.add_parser("report", help="Show deals report")
    report_p.add_argument("--on-sale", action="store_true", help="Only show games currently on sale")
    report_p.add_argument("--min-discount", type=int, metavar="PCT",
                          help="Only show games with at least PCT%% off (e.g. 50)")
    report_p.set_defaults(func=cmd_report)

    # ── game subcommand ────────────────────────────────────────────────────────
    game_p = subparsers.add_parser("game", help="Detailed info for one game")
    game_p.add_argument("app_id", type=int, help="Steam App ID (e.g. 570 for Dota 2)")
    game_p.set_defaults(func=cmd_game)

    # ── list subcommand ────────────────────────────────────────────────────────
    list_p = subparsers.add_parser("list", help="List all games in database")
    list_p.set_defaults(func=cmd_list)

    args = parser.parse_args()

    if not args.command:
        parser.print_help()
        sys.exit(0)

    args.func(args)


if __name__ == "__main__":
    main()
