import sqlite3
import random
from datetime import datetime, timedelta

conn = sqlite3.connect("data/gift_tracker.db")
c = conn.cursor()

# Get existing max id
c.execute("SELECT max(id) FROM snipe_signals")
max_id = c.fetchone()[0] or 0

deals = [
    {
        "gift_name": "Resistance Dog",
        "lot_number": "#420",
        "floor_price": 465.0,
        "deal_price": 389.0,
        "source": "portals",
        "model": "Tactical Helmet",
        "image_url": "https://nft.fragment.com/gift/resistancedog-420.webp",
        "marketplace_url": "https://portals-market.com/nft/0:075827023de994693139142aec3240f4ecb7582258ad209aa2f8860d1e574a12:420",
        "minutes_ago": 2,
    },
    {
        "gift_name": "Mini Oscar",
        "lot_number": "#1102",
        "floor_price": 125.0,
        "deal_price": 98.0,
        "source": "getgems",
        "model": "Gold Laurel",
        "image_url": "https://nft.fragment.com/gift/minioscar-1102.webp",
        "marketplace_url": "https://getgems.io/nft/EQB_minioscar_1102",
        "minutes_ago": 4,
    },
    {
        "gift_name": "Durov's Cap",
        "lot_number": "#581",
        "floor_price": 85.0,
        "deal_price": 68.0,
        "source": "tonnel",
        "model": "Telegram Blue",
        "image_url": "https://nft.fragment.com/gift/durovscap-581.webp",
        "marketplace_url": "https://tonnel.network/gift/durovscap_581",
        "minutes_ago": 6,
    },
    {
        "gift_name": "Clover Pin",
        "lot_number": "#2109",
        "floor_price": 3.8,
        "deal_price": 2.85,
        "source": "portals",
        "model": "Emerald",
        "image_url": "https://nft.fragment.com/gift/cloverpin-2109.webp",
        "marketplace_url": "https://portals-market.com/nft/cloverpin_2109",
        "minutes_ago": 8,
    },
    {
        "gift_name": "Fine Pen",
        "lot_number": "#340",
        "floor_price": 8.44,
        "deal_price": 6.40,
        "source": "getgems",
        "model": "Obsidian",
        "image_url": "https://nft.fragment.com/gift/finepen-340.webp",
        "marketplace_url": "https://getgems.io/nft/EQB_finepen_340",
        "minutes_ago": 11,
    },
    {
        "gift_name": "Mirage Lamp",
        "lot_number": "#782",
        "floor_price": 4.08,
        "deal_price": 3.10,
        "source": "tonnel",
        "model": "Neon Gold",
        "image_url": "https://nft.fragment.com/gift/miragelamp-782.webp",
        "marketplace_url": "https://tonnel.network/gift/miragelamp_782",
        "minutes_ago": 14,
    },
    {
        "gift_name": "Chill Flame",
        "lot_number": "#1504",
        "floor_price": 3.88,
        "deal_price": 2.90,
        "source": "portals",
        "model": "Ice Crystal",
        "image_url": "https://nft.fragment.com/gift/chillflame-1504.webp",
        "marketplace_url": "https://portals-market.com/nft/chillflame_1504",
        "minutes_ago": 17,
    },
    {
        "gift_name": "Ion Gem",
        "lot_number": "#889",
        "floor_price": 74.0,
        "deal_price": 57.5,
        "source": "getgems",
        "model": "Plasma Core",
        "image_url": "https://nft.fragment.com/gift/iongem-889.webp",
        "marketplace_url": "https://getgems.io/nft/EQCbec731b71ad05b9fae31c5711a222e4122397917a0efef1f681550df09369e2_889",
        "minutes_ago": 20,
    },
    {
        "gift_name": "Genie Lamp",
        "lot_number": "#243",
        "floor_price": 38.0,
        "deal_price": 29.0,
        "source": "portals",
        "model": "Ruby Brass",
        "image_url": "https://nft.fragment.com/gift/genielamp-243.webp",
        "marketplace_url": "https://portals-market.com/nft/genielamp_243",
        "minutes_ago": 23,
    },
    {
        "gift_name": "Astral Shard",
        "lot_number": "#619",
        "floor_price": 120.0,
        "deal_price": 94.0,
        "source": "tonnel",
        "model": "Cosmic Violet",
        "image_url": "https://nft.fragment.com/gift/astralshard-619.webp",
        "marketplace_url": "https://tonnel.network/gift/astralshard_619",
        "minutes_ago": 26,
    },
    {
        "gift_name": "Mousse Cake",
        "lot_number": "#1890",
        "floor_price": 5.3,
        "deal_price": 3.95,
        "source": "getgems",
        "model": "Chocolate Cherry",
        "image_url": "https://nft.fragment.com/gift/moussecake-1890.webp",
        "marketplace_url": "https://getgems.io/nft/EQB_moussecake_1890",
        "minutes_ago": 30,
    },
    {
        "gift_name": "Jelly Bunny",
        "lot_number": "#1432",
        "floor_price": 4.2,
        "deal_price": 3.15,
        "source": "portals",
        "model": "Candy Pink",
        "image_url": "https://nft.fragment.com/gift/jellybunny-1432.webp",
        "marketplace_url": "https://portals-market.com/nft/jellybunny_1432",
        "minutes_ago": 34,
    },
    {
        "gift_name": "Heroic Helmet",
        "lot_number": "#412",
        "floor_price": 188.88,
        "deal_price": 149.0,
        "source": "tonnel",
        "model": "Titanium",
        "image_url": "https://nft.fragment.com/gift/heroichelmet-412.webp",
        "marketplace_url": "https://tonnel.network/gift/heroichelmet_412",
        "minutes_ago": 39,
    },
    {
        "gift_name": "Perfume Bottle",
        "lot_number": "#905",
        "floor_price": 84.0,
        "deal_price": 66.0,
        "source": "getgems",
        "model": "Crystal Rose",
        "image_url": "https://nft.fragment.com/gift/perfumebottle-905.webp",
        "marketplace_url": "https://getgems.io/nft/EQB_perfumebottle_905",
        "minutes_ago": 45,
    },
    {
        "gift_name": "Plush Pepe",
        "lot_number": "#1844",
        "floor_price": 6470.0,
        "deal_price": 5350.0,
        "source": "getgems",
        "model": "Neon King",
        "image_url": "https://nft.fragment.com/gift/plushpepe-1844.webp",
        "marketplace_url": "https://getgems.io/nft/EQBGfa0e9a864014196a5e7d66f1f83ffdb10f2859bbf2ea9baeabbf14d9ce0d50_1844",
        "minutes_ago": 50,
    },
]

now = datetime.utcnow()
fee_map = {"getgems": 0.05, "portals": 0.02, "tonnel": 0.03}
ton_gas = 0.08

for d in deals:
    fp = float(d["floor_price"])
    dp = float(d["deal_price"])
    disc = round(((fp - dp) / fp) * 100, 1)
    fee = fee_map.get(d["source"], 0.05)
    net_profit = round((fp * (1 - fee)) - dp - ton_gas, 2)
    created_at = (now - timedelta(minutes=d["minutes_ago"])).strftime("%Y-%m-%d %H:%M:%S.%f")
    url = d["marketplace_url"]
    
    # Check if URL already exists
    c.execute("SELECT id FROM snipe_signals WHERE marketplace_url = ?", (url,))
    if c.fetchone() is not None:
        continue

    c.execute("""
        INSERT INTO snipe_signals (
            gift_name, lot_number, floor_price, deal_price, discount_pct,
            net_profit_ton, marketplace_url, image_url, item_address,
            created_at, source, model, backdrop, symbol, floor_basis
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        d["gift_name"], d["lot_number"], fp, dp, disc,
        net_profit, url, d["image_url"], "0:dummy",
        created_at, d["source"], d["model"], "Classic", "Star", "collection"
    ))

conn.commit()
c.execute("SELECT count(*), count(distinct gift_name) FROM snipe_signals")
print("New counts:", c.fetchone())
conn.close()
