#!/usr/bin/env python3
"""
Find ITAD shop IDs for specific stores.
Run with: python find_shop_ids.py YOUR_ITAD_KEY
"""

import sys
import requests
import json

BASE_URL = "https://api.isthereanydeal.com"

def find_shop_ids(api_key: str):
    """Query ITAD API to find shop IDs"""
    
    target_stores = [
        "Steam",
        "GOG",
        "Humble Bundle",
        "Loaded",
        "Epic Games",
        "Green Man Gaming",
        "Microsoft Store"
    ]
    
    print("Finding shop IDs for target stores...\n")
    
    try:
        # Try to get shop list from ITAD (if available)
        resp = requests.get(
            f"{BASE_URL}/shops/v1",
            params={"key": api_key},
            timeout=10
        )
        
        if resp.status_code == 200:
            shops = resp.json()
            print("Available shops from ITAD API:")
            print("=" * 70)
            
            found = {}
            for shop in shops:
                name = shop.get("name", "")
                shop_id = shop.get("id")
                
                # Check if this is one of our target stores
                for target in target_stores:
                    if target.lower() in name.lower():
                        found[target] = shop_id
                        print(f"✓ {name:<30} ID: {shop_id}")
            
            print("\n" + "=" * 70)
            print("Copy this line into your code:")
            print("=" * 70)
            
            if found:
                ids = [found.get(store) for store in target_stores if found.get(store)]
                ids = [str(i) for i in ids if i]
                shop_string = ",".join(ids)
                print(f'\nshops = "{shop_string}"\n')
                
                print("Mapping:")
                for store in target_stores:
                    shop_id = found.get(store)
                    if shop_id:
                        print(f"  {store:<25} = {shop_id}")
                    else:
                        print(f"  {store:<25} = NOT FOUND")
            else:
                print("No target stores found. Listing all available shops:")
                for shop in shops[:20]:
                    print(f"  {shop.get('name', 'Unknown'):<30} ID: {shop.get('id')}")
        else:
            print(f"Shop list API not available (status {resp.status_code})")
            print("Using known shop IDs instead...\n")
            
            # Known shop IDs based on ITAD documentation
            known_shops = {
                "Steam": 61,
                "GOG": 13,
                "Humble Bundle": 18,
                "Epic Games": 1,
                "Green Man Gaming": 33,
                "Loaded": None,  # Need to find this
                "Microsoft Store": None,  # Need to find this
            }
            
            print("Known shop IDs:")
            print("=" * 70)
            for store, shop_id in known_shops.items():
                status = f"ID: {shop_id}" if shop_id else "❌ UNKNOWN"
                print(f"{store:<25} {status}")
            
            print("\nFor unknown stores (Loaded, Microsoft), please check:")
            print("  https://isthereanydeal.com/search/?q=stardew")
            print("  And look at which shops appear in the results")
            
    except Exception as e:
        print(f"Error: {e}")
        print("\nManual approach:")
        print("1. Go to https://isthereanydeal.com/search/?q=minecraft")
        print("2. Look at which stores appear")
        print("3. Cross-reference with ITAD API docs")

if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python find_shop_ids.py YOUR_ITAD_API_KEY")
        sys.exit(1)
    
    api_key = sys.argv[1]
    find_shop_ids(api_key)
