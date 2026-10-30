"""Тесты рефералки: начисление за оплату, идемпотентность, защита от абьюза."""
from __future__ import annotations

from datetime import datetime, timedelta

from sqlalchemy import select

from bot.database import award_referral, get_or_create_user, referral_stats
from bot.keyboards import main_menu_kb, referral_kb
from bot.models import ReferralReward, User
from tests.conftest import make_settings


async def _two_users(db) -> tuple[User, User]:
    """referrer + referred (связка проставлена, как делает /start ref_)."""
    async with db.session() as session:
        referrer = await get_or_create_user(session, {"id": 1001, "username": "referrer"})
        referred = await get_or_create_user(session, {"id": 2002, "username": "referred"})
        referred.referred_by = referrer.id
        await session.flush()
        referrer_id, referred_id = referrer.id, referred.id
    async with db.session() as session:
        referrer = await session.get(User, referrer_id)
        referred = await session.get(User, referred_id)
        session.expunge(referrer)
        session.expunge(referred)
    return referrer, referred


async def test_award_gives_days_to_referrer(db, settings):
    referrer, referred = await _two_users(db)
    async with db.session() as session:
        current = await session.get(User, referred.id)
        current.referred_by = referrer.id
        result = await award_referral(session, current, "stars:charge_1", "stars", settings)
        assert result is not None
        referrer_fresh, days = result
        assert days == 5.0  # 30 дней * 15% = 4.5 -> округление вверх
        assert referrer_fresh.is_pro is True
        assert referrer_fresh.pro_expires_at is not None
        assert referrer_fresh.pro_expires_at > datetime.utcnow() + timedelta(days=4)
        session.expunge(referrer_fresh)

    async with db.session() as session:
        rewards = list((await session.execute(select(ReferralReward))).scalars())
        assert len(rewards) == 1
        assert rewards[0].payment_ref == "stars:charge_1"
        assert rewards[0].referrer_user_id == referrer.id


async def test_award_idempotent_per_payment(db, settings):
    referrer, referred = await _two_users(db)
    async with db.session() as session:
        current = await session.get(User, referred.id)
        current.referred_by = referrer.id
        first = await award_referral(session, current, "stars:charge_1", "stars", settings)
        assert first is not None
        exp1 = first[0].pro_expires_at

    # Повторная доставка того же платежа — награды нет, дни не растут.
    async with db.session() as session:
        current = await session.get(User, referred.id)
        current.referred_by = referrer.id
        second = await award_referral(session, current, "stars:charge_1", "stars", settings)
        assert second is None
        ref_now = await session.get(User, referrer.id)
        assert ref_now.pro_expires_at == exp1


async def test_award_skips_self_referral_and_orphans(db, settings):
    # Без пригласившего.
    async with db.session() as session:
        loner = await get_or_create_user(session, {"id": 3003, "username": "loner"})
        assert await award_referral(session, loner, "stars:x", "stars", settings) is None

    # Самореферал: referred_by = свой id.
    async with db.session() as session:
        sneaky = await get_or_create_user(session, {"id": 4004, "username": "sneaky"})
        sneaky.referred_by = sneaky.id
        assert await award_referral(session, sneaky, "stars:y", "stars", settings) is None


async def test_referral_stats_counts_invited_and_days(db, settings):
    referrer, referred = await _two_users(db)
    async with db.session() as session:
        second_friend = await get_or_create_user(session, {"id": 5005, "username": "friend2"})
        second_friend.referred_by = referrer.id
        for i, usr in enumerate((referred, second_friend)):
            current = await session.get(User, usr.id)
            current.referred_by = referrer.id
            await award_referral(session, current, f"crypto:inv_{i}", "crypto", settings)

    async with db.session() as session:
        ref_now = await session.get(User, referrer.id)
        invited, earned = await referral_stats(session, ref_now)
    assert invited == 2
    assert earned == 10.0  # 2 платежа * 5 дней


async def test_reward_pct_is_configurable(tmp_path, db):
    settings = make_settings(tmp_path, REFERRAL_REWARD_PCT=50)
    referrer, referred = await _two_users(db)
    async with db.session() as session:
        current = await session.get(User, referred.id)
        current.referred_by = referrer.id
        result = await award_referral(session, current, "stars:big", "stars", settings)
        assert result is not None
        _referrer, days = result
        assert days == 15.0  # 30 дней * 50%


def test_menu_has_referral_button_and_share_kb():
    kb = main_menu_kb("https://example.tunnel.dev/")
    texts = [btn.text for row in kb.inline_keyboard for btn in row]
    assert any("Реферальная система" in t for t in texts)
    # Проценты не светятся на кнопке — только внутри экрана /ref.
    assert not any("+" in t and "%" in t for t in texts)
    assert any("Тех-поддержка" in t for t in texts)

    rk = referral_kb("https://t.me/GiftTracker_App?start=ref_123")
    flat = [btn for row in rk.inline_keyboard for btn in row]
    share = [b for b in flat if b.url and b.url.startswith("https://t.me/share/url")]
    assert share, "кнопка «Поделиться» должна быть t.me/share-ссылкой"
    assert "start%3Dref_123" in share[0].url


def test_support_kb_hides_broken_url():
    from bot.keyboards import support_kb

    ok = support_kb("https://t.me/support_bot")
    assert any(
        b.url == "https://t.me/support_bot" for row in ok.inline_keyboard for b in row
    )
    empty = support_kb("")
    assert all(b.url is None for row in empty.inline_keyboard for b in row)
