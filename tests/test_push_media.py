"""Пуши с фото NFT: конвертация картинок, t.me-ссылка подарка, кнопки, фолбэк.

Формат пуша — как у маркет-парсеров: фото конкретного NFT + подпись
(цена/флор/профит) + кнопки. Проверяем, что sendPhoto уходит с верной
картинкой, а при недоступной картинке сообщение деградирует в текст.
"""
from __future__ import annotations

from aiogram.exceptions import TelegramBadRequest

from bot.config import Settings
from bot.models import User
from bot.services.notifier import (
    Notifier,
    _snipe_keyboard,
    market_label,
    push_photo_url,
    telegram_gift_url,
)
from tests.test_monetization import FakeBot, make_signal


def _settings() -> Settings:
    return Settings(
        _env_file=None,
        FREE_PUSH_DELAY_SEC=0,
        BROADCAST_DELAY_SEC=0,
        MINI_APP_URL="",
    )


class TestPhotoUrl:
    def test_fragment_webp_becomes_medium_jpg(self):
        assert (
            push_photo_url("https://nft.fragment.com/gift/plushpepe-77.webp")
            == "https://nft.fragment.com/gift/plushpepe-77.medium.jpg"
        )

    def test_portals_photo_stays_as_is(self):
        url = "https://cdn4.telesco.pe/file/photo.jpg"
        assert push_photo_url(url) == url

    def test_collection_cover_stays_as_is(self):
        url = "https://nft.fragment.com/collection/plushpepe.webp"
        assert push_photo_url(url) == url

    def test_none_and_non_http_give_none(self):
        assert push_photo_url(None) is None
        assert push_photo_url("ftp://example.com/x.webp") is None


class TestTelegramGiftUrl:
    def test_multiword_name(self):
        assert (
            telegram_gift_url("Resistance Dog", "#7547")
            == "https://t.me/nft/ResistanceDog-7547"
        )

    def test_apostrophes_stripped(self):
        assert telegram_gift_url("Durov's Cap", "#12") == "https://t.me/nft/DurovsCap-12"

    def test_no_number_no_link(self):
        assert telegram_gift_url("Plush Pepe", "#----") is None
        assert telegram_gift_url("Plush Pepe", None) is None


class TestMarketLabel:
    def test_known_sources(self):
        assert market_label("getgems") == "Getgems"
        assert market_label("portals") == "Portals"
        assert market_label("mrkt") == "MRKT"

    def test_unknown_falls_back_to_getgems(self):
        assert market_label(None) == "Getgems"
        assert market_label("mystery") == "Getgems"


class TestKeyboard:
    def test_buy_gift_terminal_rows(self):
        signal = make_signal(deal_price=11.0, source="getgems")
        kb = _snipe_keyboard(signal, mini_app_url="https://app.example.com")
        texts = [btn.text for row in kb.inline_keyboard for btn in row]
        assert texts[0] == "🛒 Купить на Getgems · 11.00 TON"
        assert "🎁 Подарок в Telegram" in texts
        assert "📱 Открыть Sniper Terminal" in texts
        urls = [btn.url for row in kb.inline_keyboard for btn in row if btn.url]
        assert "https://t.me/nft/PlushPepe-42" in urls

    def test_portals_button_uses_correct_preposition(self):
        signal = make_signal(source="portals", deal_price=5.28)
        kb = _snipe_keyboard(signal, mini_app_url=None)
        assert kb.inline_keyboard[0][0].text == "🛒 Купить в Portals · 5.28 TON"

    def test_lot_without_number_has_no_gift_button(self):
        signal = make_signal(lot_number="#----")
        kb = _snipe_keyboard(signal, mini_app_url=None)
        texts = [btn.text for row in kb.inline_keyboard for btn in row]
        assert "🎁 Подарок в Telegram" not in texts


class TestDelivery:
    async def test_push_uses_photo_with_caption(self):
        notifier = Notifier(_settings())
        fake = FakeBot()
        notifier.bind(fake)  # type: ignore[arg-type]

        signal = make_signal(image_url="https://nft.fragment.com/gift/plushpepe-42.webp")
        await notifier.push_signal(signal, [User(telegram_id=1)])
        await notifier.wait_pending()

        assert fake.sent == [], "при живой картинке текстовая отправка не нужна"
        assert len(fake.photos) == 1
        chat_id, photo, caption = fake.photos[0]
        assert chat_id == 1
        assert photo == "https://nft.fragment.com/gift/plushpepe-42.medium.jpg"
        assert "Plush Pepe #42" in caption
        assert "11.00 TON" in caption
        assert "16.5 TON" in caption
        assert "+4.80 TON" in caption
        assert "−33.3% от флора" in caption

    async def test_lot_without_image_sends_text(self):
        notifier = Notifier(_settings())
        fake = FakeBot()
        notifier.bind(fake)  # type: ignore[arg-type]

        signal = make_signal(image_url=None)
        await notifier.push_signal(signal, [User(telegram_id=2)])
        await notifier.wait_pending()

        assert fake.photos == []
        assert [chat for chat, _ in fake.sent] == [2]

    async def test_bad_photo_falls_back_to_text(self):
        notifier = Notifier(_settings())
        fake = FakeBot()
        fake.photo_error = TelegramBadRequest(
            method="sendPhoto", message="failed to get HTTP URL content"
        )
        notifier.bind(fake)  # type: ignore[arg-type]

        signal = make_signal(image_url="https://nft.fragment.com/gift/plushpepe-42.webp")
        await notifier.push_signal(signal, [User(telegram_id=5)])
        await notifier.wait_pending()

        assert fake.photos == [], "неудавшаяся картинка не должна ретраиться"
        assert [chat for chat, _ in fake.sent] == [5], "пуш должен уйти текстом"

    async def test_alert_push_uses_photo_and_trigger_line(self):
        notifier = Notifier(_settings())
        fake = FakeBot()
        notifier.bind(fake)  # type: ignore[arg-type]

        from bot.models import Alert

        alert = Alert(gift_name="Plush Pepe", max_price=12.0, min_discount=15.0)
        alert.user = User(telegram_id=7)
        signal = make_signal(image_url="https://nft.fragment.com/gift/plushpepe-42.webp")
        await notifier.push_alert_matches(signal, [alert])
        await notifier.wait_pending()

        assert len(fake.photos) == 1
        caption = fake.photos[0][2]
        assert "Ваш алерт сработал" in caption
        assert "≤ 12 TON" in caption
