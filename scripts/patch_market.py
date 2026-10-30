with open("bot/services/market_api.py", "r", encoding="utf-8") as f:
    text = f.read()

# 1. Separate EXTRA_GIFT_COLLECTIONS
target1 = """    "Resistance Dog": {
        "base_floor": 465.0,
        "asset": "Resistance Dogs",
        "image": "https://nft.fragment.com/collection/resistancedog.webp",
        "address": "0:075827023de994693139142aec3240f4ecb7582258ad209aa2f8860d1e574a12",
        "address_verified": True,
        "dataset_name": "Resistance Dog",
        "models_path": "models/resistance_dog/prices.json",
        "fragment_slug": "resistancedog", "supply": 20000,
    },
    "Mirage Lamp": {
        "base_floor": 4.08,
        "asset": "Mirage Lamps",
        "image": "https://nft.fragment.com/collection/miragelamp.webp",
        "address": "0:3d3a771621bf604600fb2c397f25ce44df8471e03786532403f8b4784cd2844a",
        "address_verified": True,
        "dataset_name": "Mirage Lamp",
        "models_path": "models/mirage_lamp/prices.json",
        "fragment_slug": "miragelamp", "supply": 5000,
    },
    "Fine Pen": {
        "base_floor": 8.44,
        "asset": "Fine Pens",
        "image": "https://nft.fragment.com/collection/finepen.webp",
        "address": "0:42c059da94dd1a1b3a098158a1b4c2d3797b8a3bfb47231abb6dd74d9671f626",
        "address_verified": True,
        "dataset_name": "Fine Pen",
        "models_path": "models/fine_pen/prices.json",
        "fragment_slug": "finepen", "supply": 5000,
    },
}"""

replacement1 = """}

EXTRA_GIFT_COLLECTIONS: dict[str, dict[str, Any]] = {
    "Resistance Dog": {
        "base_floor": 465.0,
        "asset": "Resistance Dogs",
        "image": "https://nft.fragment.com/collection/resistancedog.webp",
        "address": "0:075827023de994693139142aec3240f4ecb7582258ad209aa2f8860d1e574a12",
        "address_verified": True,
        "dataset_name": "Resistance Dog",
        "models_path": "models/resistance_dog/prices.json",
        "fragment_slug": "resistancedog", "supply": 20000,
    },
    "Mirage Lamp": {
        "base_floor": 4.08,
        "asset": "Mirage Lamps",
        "image": "https://nft.fragment.com/collection/miragelamp.webp",
        "address": "0:3d3a771621bf604600fb2c397f25ce44df8471e03786532403f8b4784cd2844a",
        "address_verified": True,
        "dataset_name": "Mirage Lamp",
        "models_path": "models/mirage_lamp/prices.json",
        "fragment_slug": "miragelamp", "supply": 50000,
    },
    "Fine Pen": {
        "base_floor": 8.44,
        "asset": "Fine Pens",
        "image": "https://nft.fragment.com/collection/finepen.webp",
        "address": "0:42c059da94dd1a1b3a098158a1b4c2d3797b8a3bfb47231abb6dd74d9671f626",
        "address_verified": True,
        "dataset_name": "Fine Pen",
        "models_path": "models/fine_pen/prices.json",
        "fragment_slug": "finepen", "supply": 25000,
    },
}"""

assert target1 in text, "target1 not found"
text = text.replace(target1, replacement1, 1)

# 2. Update _parse_giftdataset_floors
idx_parse = text.find("for preset_name, preset in GIFT_PRESETS.items():")
idx_ret = text.find("if not catalog:\n            return None")
sub = text[idx_parse:idx_ret + len("if not catalog:\n            return None")]

replacement_parse = """if not catalog:
            return None

        # Add extra verified collections not present in the community json (Resistance Dog, Mirage Lamp, Fine Pen)
        for extra_name, preset in EXTRA_GIFT_COLLECTIONS.items():
            if extra_name not in catalog:
                bf = float(preset.get("base_floor", 10.0))
                prices = {"floor": bf, "portals": bf, "getgems": bf}
                catalog[extra_name] = {
                    "name": extra_name,
                    "asset": preset.get("asset") or extra_name,
                    "image": preset.get("image"),
                    "address": preset.get("address"),
                    "address_verified": bool(preset.get("address_verified")),
                    "dataset_name": extra_name,
                    "short_name": preset.get("fragment_slug") or extra_name.lower().replace(" ", "_"),
                    "regular_id": "",
                    "fragment_slug": preset.get("fragment_slug") or extra_name.lower().replace(" ", ""),
                    "models_path": preset.get("models_path") or f"models/{extra_name.lower().replace(' ', '_')}/prices.json",
                    "base_floor": bf,
                    "floor": bf,
                    "change_24h": None,
                    "liquidity": _liquidity_for(extra_name),
                    "market_floors": prices,
                }"""

assert idx_parse > 0 and idx_ret > 0, "parse block not found"
text = text[:idx_parse] + replacement_parse + text[idx_ret + len("if not catalog:\n            return None"):]

# 3. Update _gift_names
target_names = "return {name for name in (set(GIFT_PRESETS) | set(self._catalog)) if name}"
replacement_names = "return {name for name in (set(GIFT_PRESETS) | set(EXTRA_GIFT_COLLECTIONS) | set(self._catalog)) if name}"
assert target_names in text, "target_names not found"
text = text.replace(target_names, replacement_names, 1)

with open("bot/services/market_api.py", "w", encoding="utf-8") as f:
    f.write(text)

print("market_api.py successfully patched")
