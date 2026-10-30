"""Тесты математики снайпера (ЧАСТЬ 2 ТЗ).

Net Profit = (Floor * 0.95) - Listing - 0.08
Discount   = round(((Floor - Listing) / Floor) * 100, 1)
>= 15% -> Snipe Signal; >= 30% -> Hot Liquidation.
"""
from __future__ import annotations

import math

from bot.config import Settings
from bot.services.market_api import Lot
from bot.services.sniper_engine import evaluate_lot


def make_settings() -> Settings:
    return Settings(_env_file=None)


def make_lot(price: float, gift: str = "Plush Pepe") -> Lot:
    return Lot(
        gift_name=gift,
        lot_number="#1234",
        price=price,
        marketplace_url="https://getgems.io/collection/test",
    )


class TestEvaluateLot:
    def test_discount_below_threshold_is_rejected(self):
        # Floor 16.5, lot 14.5 -> discount 12.1% < 15 -> None
        assert evaluate_lot(make_lot(14.5), 16.5, make_settings()) is None

    def test_discount_15_is_snipe_signal(self):
        # (16.5 - 14.0) / 16.5 = 15.15% -> сигнал
        signal = evaluate_lot(make_lot(14.0), 16.5, make_settings())
        assert signal is not None
        assert signal.is_hot is False
        assert signal.discount_pct == 15.2

    def test_discount_30_is_hot_liquidation(self):
        # (16.5 - 11.5) / 16.5 = 30.3% -> hot
        signal = evaluate_lot(make_lot(11.5), 16.5, make_settings())
        assert signal is not None
        assert signal.is_hot is True
        assert signal.discount_pct == 30.3

    def test_net_profit_formula_exact(self):
        # Floor 20.0, Deal 12.0: 20*0.95 - 12 - 0.08 = 6.92
        signal = evaluate_lot(make_lot(12.0), 20.0, make_settings())
        assert signal is not None
        assert math.isclose(signal.net_profit_ton, 6.92, abs_tol=0.001)

    def test_net_profit_portals_uses_2_percent_fee(self):
        # Floor 20.0, Deal 12.0 on Portals: 20 * 0.98 - 12 - 0.08 = 7.52
        lot = Lot(
            gift_name="Plush Pepe",
            lot_number="#1234",
            price=12.0,
            marketplace_url="https://t.me/portals/market",
            source="portals",
        )
        signal = evaluate_lot(lot, 20.0, make_settings())
        assert signal is not None
        assert math.isclose(signal.net_profit_ton, 7.52, abs_tol=0.001)

    def test_fee_for_source_settings(self):
        s = make_settings()
        assert s.fee_for_source("portals") == 0.02
        assert s.fee_for_source("Portals") == 0.02
        assert s.fee_for_source("getgems") == 0.05
        assert s.fee_for_source(None) == 0.05

    def test_net_profit_can_be_negative(self):
        # Дисконт проходит порог, но после комиссий профита нет (микро-флор).
        # Floor 1.0, Deal 0.84 -> discount 16% ; net = 0.95 - 0.84 - 0.08 = 0.03
        signal = evaluate_lot(make_lot(0.84), 1.0, make_settings())
        assert signal is not None
        assert math.isclose(signal.net_profit_ton, 0.03, abs_tol=0.001)

    def test_zero_floor_is_rejected(self):
        assert evaluate_lot(make_lot(1.0), 0.0, make_settings()) is None

    def test_zero_price_is_rejected(self):
        assert evaluate_lot(make_lot(0.0), 16.5, make_settings()) is None

    def test_lot_above_floor_is_rejected(self):
        # Лот дороже флора -> дисконт отрицательный -> None
        assert evaluate_lot(make_lot(18.0), 16.5, make_settings()) is None

    def test_media_fields_are_passed_through(self):
        lot = Lot(
            gift_name="Heroic Helmet",
            lot_number="#777",
            price=20.0,
            marketplace_url="https://getgems.io/nft/0:abc",
            item_address="0:abc",
            image_url="https://nft.fragment.com/collection/heroichelmet.webp",
            model="Golden Hoplite",
            backdrop="Obsidian",
            symbol="Owl",
        )
        signal = evaluate_lot(lot, 38.5, make_settings())
        assert signal is not None
        assert signal.image_url == "https://nft.fragment.com/collection/heroichelmet.webp"
        assert signal.item_address == "0:abc"
        assert signal.marketplace_url == "https://getgems.io/nft/0:abc"
        # Атрибуты конкретного NFT проходят из лота в сигнал без потерь.
        assert signal.model == "Golden Hoplite"
        assert signal.backdrop == "Obsidian"
        assert signal.symbol == "Owl"

    def test_floor_basis_is_recorded(self):
        from bot.services.sniper_engine import build_signal

        lot = make_lot(10.0)
        signal = build_signal(lot, 16.5, make_settings(), floor_basis="model:Barcelona")
        assert signal is not None
        assert signal.floor_basis == "model:Barcelona"

    def test_as_dict_shape(self):
        lot = Lot(
            gift_name="Plush Pepe",
            lot_number="#1",
            price=10.0,
            marketplace_url="https://getgems.io/collection/x",
            image_url="https://nft.fragment.com/collection/plushpepe.webp",
        )
        payload = evaluate_lot(lot, 16.5, make_settings()).as_dict()
        assert {
            "gift_name", "lot_number", "floor_price", "deal_price",
            "discount_pct", "net_profit_ton", "marketplace_url",
            "image_url", "item_address", "source", "is_hot", "created_at",
            "model", "backdrop", "symbol", "floor_basis",
        } == set(payload.keys())
        assert payload["created_at"].endswith("Z")


class TestCustomFees:
    """Формула уважает GETGEMS_FEE/TON_GAS из конфигурации."""

    def test_custom_fee_and_gas(self):
        settings = Settings(_env_file=None, GETGEMS_FEE=0.10, TON_GAS=0.5)
        # Floor 100, Deal 50: 100*0.90 - 50 - 0.5 = 39.5
        signal = evaluate_lot(make_lot(50.0), 100.0, settings)
        assert math.isclose(signal.net_profit_ton, 39.5, abs_tol=0.001)
