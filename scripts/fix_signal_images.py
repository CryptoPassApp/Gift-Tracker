import sys
import os
sys.path.insert(0, os.path.abspath("."))
import sqlite3
import re
from bot.services.market_api import GIFT_PRESETS, EXTRA_GIFT_COLLECTIONS

ALL_PRESETS = {**GIFT_PRESETS, **EXTRA_GIFT_COLLECTIONS}

def get_slug(gift_name: str) -> str:
    preset = ALL_PRESETS.get(gift_name) or {}
    slug = preset.get("fragment_slug")
    if slug:
        return slug
    return gift_name.lower().replace(" ", "").replace("'", "")

conn = sqlite3.connect("data/gift_tracker.db")
c = conn.cursor()

# 1. Fix Resistance Dog lot 428 -> 420
c.execute("""
    UPDATE snipe_signals 
    SET lot_number = '#420', 
        marketplace_url = 'https://portals-market.com/nft/0:075827023de994693139142aec3240f4ecb7582258ad209aa2f8860d1e574a12:420',
        image_url = 'https://nft.fragment.com/gift/resistancedog-420.webp'
    WHERE gift_name = 'Resistance Dog' AND lot_number = '#428'
""")

# 2. Fix all other rows with /collection/ in image_url
c.execute("SELECT id, gift_name, lot_number, image_url FROM snipe_signals WHERE image_url LIKE '%/collection/%'")
rows = c.fetchall()

updated = 0
for row_id, gift_name, lot_number, img in rows:
    slug = get_slug(gift_name)
    num_match = re.search(r"(\d+)", lot_number or "")
    if num_match:
        num = num_match.group(1)
        new_url = f"https://nft.fragment.com/gift/{slug}-{num}.webp"
        c.execute("UPDATE snipe_signals SET image_url = ? WHERE id = ?", (new_url, row_id))
        updated += 1
        print(f"Updated row {row_id} ({gift_name} {lot_number}): {new_url}")

conn.commit()
print(f"Successfully updated {updated} rows in snipe_signals.")
conn.close()
