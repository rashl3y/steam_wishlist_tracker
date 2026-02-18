#!/usr/bin/env python3
"""
Diagnostic script to debug ITAD sync issues
"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent / "src"))

from database import get_connection

def diagnose():
    """Run diagnostics on the database to identify issues"""
    conn = get_connection()
    
    print("=" * 80)
    print("STEAM WISHLIST PRICE TRACKER - DIAGNOSTICS")
    print("=" * 80)
    print()
    
    # Issue #1: Check for games with price_regular = 0
    print("1. Checking for missing regular prices...")
    rows = conn.execute("""
        SELECT COUNT(*) as count 
        FROM prices 
        WHERE (price_regular IS NULL OR price_regular = 0) 
        AND store NOT LIKE 'Historic Low%'
    """).fetchone()
    print(f"   Found {rows['count']} prices with missing/zero regular price")
    
    if rows['count'] > 0:
        examples = conn.execute("""
            SELECT g.title, p.store, p.price_current, p.price_regular
            FROM prices p
            JOIN games g ON p.app_id = g.app_id
            WHERE (p.price_regular IS NULL OR p.price_regular = 0)
            AND p.store NOT LIKE 'Historic Low%'
            LIMIT 5
        """).fetchall()
        print("   Examples:")
        for ex in examples:
            print(f"     - {ex['title']}: {ex['store']} £{ex['price_current']} (regular: £{ex['price_regular']})")
    print()
    
    # Issue #2: Check for stores that appear in both current and historic
    print("2. Checking for store name conflicts between current and historic...")
    conflicts = conn.execute("""
        SELECT 
            g.title,
            g.app_id,
            GROUP_CONCAT(DISTINCT p.store) as all_stores
        FROM games g
        JOIN prices p ON g.app_id = p.app_id
        GROUP BY g.app_id
        HAVING 
            SUM(CASE WHEN p.store LIKE 'Historic Low%' THEN 1 ELSE 0 END) > 0
            AND SUM(CASE WHEN p.store NOT LIKE 'Historic Low%' THEN 1 ELSE 0 END) = 0
    """).fetchall()
    
    print(f"   Found {len(conflicts)} games with ONLY historic lows (no current prices)")
    if conflicts:
        print("   Examples of games missing current prices:")
        for game in conflicts[:5]:
            print(f"     - {game['title']} (App ID: {game['app_id']})")
    print()
    
    # Issue #3: Check store distribution for specific game
    print("3. Checking store distribution (looking for games with limited stores)...")
    limited = conn.execute("""
        SELECT 
            g.title,
            g.app_id,
            COUNT(DISTINCT p.store) as store_count,
            GROUP_CONCAT(DISTINCT p.store) as stores
        FROM games g
        LEFT JOIN prices p ON g.app_id = p.app_id AND p.store NOT LIKE 'Historic Low%'
        GROUP BY g.app_id
        HAVING store_count > 0 AND store_count <= 2
        ORDER BY store_count ASC
        LIMIT 10
    """).fetchall()
    
    print(f"   Found {len(limited)} games with 1-2 stores only")
    for game in limited:
        print(f"     - {game['title']}: {game['store_count']} store(s) - {game['stores']}")
    print()
    
    # Check Kingdom Hearts specifically if present
    kh = conn.execute("""
        SELECT 
            g.title,
            COUNT(DISTINCT p.store) as store_count,
            GROUP_CONCAT(p.store || ' (£' || ROUND(p.price_current, 2) || ')') as stores
        FROM games g
        LEFT JOIN prices p ON g.app_id = p.app_id AND p.store NOT LIKE 'Historic Low%'
        WHERE g.title LIKE '%Kingdom Hearts%'
        GROUP BY g.app_id
    """).fetchall()
    
    if kh:
        print("4. Kingdom Hearts games analysis:")
        for game in kh:
            print(f"   - {game['title']}")
            print(f"     Stores: {game['store_count']}")
            print(f"     Prices: {game['stores']}")
        print()
    
    # General stats
    print("=" * 80)
    print("GENERAL STATISTICS")
    print("=" * 80)
    stats = conn.execute("""
        SELECT 
            COUNT(DISTINCT g.app_id) as total_games,
            COUNT(DISTINCT CASE WHEN p.store NOT LIKE 'Historic Low%' THEN p.store END) as unique_stores,
            COUNT(CASE WHEN p.store NOT LIKE 'Historic Low%' THEN 1 END) as total_prices,
            COUNT(CASE WHEN p.store LIKE 'Historic Low%' THEN 1 END) as historic_entries
        FROM games g
        LEFT JOIN prices p ON g.app_id = p.app_id
    """).fetchone()
    
    print(f"Total games: {stats['total_games']}")
    print(f"Unique stores: {stats['unique_stores']}")
    print(f"Total current prices: {stats['total_prices']}")
    print(f"Historic low entries: {stats['historic_entries']}")
    print()
    
    conn.close()

if __name__ == "__main__":
    try:
        diagnose()
    except Exception as e:
        print(f"Error running diagnostics: {e}")
        import traceback
        traceback.print_exc()
