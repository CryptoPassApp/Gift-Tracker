with open("bot/services/market_api.py", "r", encoding="utf-8") as f:
    text = f.read()

idx_start = text.find("    def _parse_giftdataset_floors")
idx_end = text.find("    def get_catalog(self)")

correct_func = '''    def _parse_giftdataset_floors(self, payload: dict[str, Any]) -> dict[str, dict[str, Any]] | None:
        """Парсит каталог NFT-подарков из официального датасета TelegramGifts.

        В датасете 123 upgraded-подарка; первые 8 сопоставляются
        с display-пресетами бота по имени, остальные наследуют
        картинку/fragment-slug/модели автоматически.
        """
        presets_by_dataset: dict[str, tuple[str, dict[str, Any]]] = {}
        for preset_name, preset in GIFT_PRESETS.items():
            key = str(preset.get("dataset_name") or preset_name).strip().lower()
            presets_by_dataset[key] = (preset_name, preset)

        catalog: dict[str, dict[str, Any]] = {}
        for entry in payload.get("upgraded") or []:
            if not isinstance(entry, dict):
                continue
            full_name = str(entry.get("full_name") or "").strip()
            if not full_name:
                continue
            known = presets_by_dataset.get(full_name.lower())
            preset_name, preset = known if known else (full_name, {})
            if preset_name in catalog:
                continue

            prices: dict[str, float] = {}
            for field_name, label in (
                ("getgems_price_ton", "getgems"),
                ("tgmrkt_price_ton", "tgmrkt"),
                ("portal_price_ton", "portals"),
                ("floor_price_ton", "floor"),
            ):
                value = entry.get(field_name)
                if isinstance(value, (int, float)) and value > 0:
                    prices[label] = round(float(value), 2)
            if not prices:
                continue

            short_name = str(entry.get("short_name") or "").strip()
            regular_id = str(entry.get("regular_id") or "").strip()
            image = preset.get("image")
            if not image and regular_id:
                # Fallback на официальный assets-репозиторий.
                image = (
                    "https://raw.githubusercontent.com/ssamy2/TelegramGiftsAssests/"
                    f"main/webp/by_id/{regular_id}.webp"
                )
            address = preset.get("address") if preset.get("address_verified") else None
            if not address:
                # Подтягиваем верифицированный адрес Tonkeeper (из data/gift_collections.json).
                resolved = self._collection_index.get(preset_name)
                if resolved:
                    address = resolved.get("address")
            models_path = str(entry.get("models") or "").strip()
            if not models_path:
                models_path = f"models/{short_name}/prices.json"
            catalog[preset_name] = {
                "name": preset_name,
                "asset": preset.get("asset") or full_name,
                "image": image,
                "address": address,
                "address_verified": bool(address),
                "dataset_name": full_name,
                "short_name": short_name,
                "regular_id": regular_id,
                "fragment_slug": preset.get("fragment_slug") or short_name.replace("_", ""),
                "models_path": models_path,
                "base_floor": min(prices.values()),
                "floor": min(prices.values()),
                # Исторический тренд не отдаём: фейк 24ч-дельты
                # в API ломает floor_snapshots (None = нет замера).
                "change_24h": None,
                "liquidity": _liquidity_for(preset_name),
                "market_floors": prices,
            }

        if not catalog:
            return None

        # Добавляем верифицированные новые коллекции (Resistance Dog, Mirage Lamp, Fine Pen)
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
                }

        self._catalog = catalog
        return {
            name: {
                "floor": info["floor"],
                "change_24h": info["change_24h"],
                "liquidity": info["liquidity"],
                "image": info["image"],
                "address": info["address"],
                "market_floors": info["market_floors"],
            }
            for name, info in catalog.items()
        }

'''

assert idx_start > 0 and idx_end > 0, "Bounds not found"
text = text[:idx_start] + correct_func + text[idx_end:]

with open("bot/services/market_api.py", "w", encoding="utf-8") as f:
    f.write(text)

print("market_api.py restored successfully")
