"""Тесты рефералки: начисление за оплату, идемпотентность, защита от абьюза."""
from __future__ import annotations

from datetime import datetime, timedelta

from sqlalchemy import select

from bot.database import (
    REFERRAL_CODE_ALPHABET,
    award_referral,
    award_referral_invite,
    ensure_referral_code,
    get_or_create_user,
    referral_stats,
    resolve_referral,
)
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


async def test_invite_awards_one_hour_to_referrer(db, settings):
    referrer, referred = await _two_users(db)
    async with db.session() as session:
        current = await session.get(User, referred.id)
        result = await award_referral_invite(session, current, settings)
        assert result is not None
        referrer_fresh, hours = result
        assert hours == 1.0
        assert referrer_fresh.is_pro is True
        delta = referrer_fresh.pro_expires_at - datetime.utcnow()
        assert timedelta(minutes=55) < delta < timedelta(minutes=65)
        session.expunge(referrer_fresh)

    async with db.session() as session:
        rewards = list((await session.execute(select(ReferralReward))).scalars())
        assert len(rewards) == 1
        assert rewards[0].payment_ref == f"invite:{referred.id}"
        assert rewards[0].source == "invite"
        assert abs(rewards[0].days_awarded - 1 / 24) < 1e-9


async def test_invite_idempotent_on_repeat_start(db, settings):
    referrer, referred = await _two_users(db)
    async with db.session() as session:
        current = await session.get(User, referred.id)
        first = await award_referral_invite(session, current, settings)
        assert first is not None
        exp1 = first[0].pro_expires_at

    # Повторный /start того же приглашенного — второй час не начисляется.
    async with db.session() as session:
        current = await session.get(User, referred.id)
        assert await award_referral_invite(session, current, settings) is None
        ref_now = await session.get(User, referrer.id)
        assert ref_now.pro_expires_at == exp1


async def test_invite_skips_self_referral_and_orphans(db, settings):
    # Юзер без пригласившего.
    async with db.session() as session:
        loner = await get_or_create_user(session, {"id": 6006, "username": "loner2"})
        assert await award_referral_invite(session, loner, settings) is None

    # Самореферал: referred_by = свой id.
    async with db.session() as session:
        sneaky = await get_or_create_user(session, {"id": 7007, "username": "sneaky2"})
        sneaky.referred_by = sneaky.id
        assert await award_referral_invite(session, sneaky, settings) is None


async def test_invite_hours_configurable(tmp_path, db):
    settings = make_settings(tmp_path, REFERRAL_INVITE_HOURS=2)
    referrer, referred = await _two_users(db)
    async with db.session() as session:
        current = await session.get(User, referred.id)
        result = await award_referral_invite(session, current, settings)
        assert result is not None
        referrer_fresh, hours = result
        assert hours == 2.0
        delta = referrer_fresh.pro_expires_at - datetime.utcnow()
        assert timedelta(minutes=115) < delta < timedelta(minutes=125)


async def test_invite_disabled_when_zero_hours(tmp_path, db):
    settings = make_settings(tmp_path, REFERRAL_INVITE_HOURS=0)
    referrer, referred = await _two_users(db)
    async with db.session() as session:
        current = await session.get(User, referred.id)
        assert await award_referral_invite(session, current, settings) is None


async def test_referral_stats_include_invite_hours(db, settings):
    referrer, referred = await _two_users(db)
    async with db.session() as session:
        current = await session.get(User, referred.id)
        await award_referral_invite(session, current, settings)

    async with db.session() as session:
        current = await session.get(User, referred.id)
        await award_referral(session, current, "stars:charge_9", "stars", settings)

    async with db.session() as session:
        ref_now = await session.get(User, referrer.id)
        invited, earned = await referral_stats(session, ref_now)
    assert invited == 1
    assert abs(earned - (5.0 + 1 / 24)) < 1e-9


def test_fmt_earned_shows_days_and_hours():
    from bot.handlers.referral import _fmt_earned

    assert _fmt_earned(0.0) == "0 ч"
    assert _fmt_earned(1 / 24) == "1 ч"
    assert _fmt_earned(3 / 24) == "3 ч"
    assert _fmt_earned(5.0) == "5 дн."
    assert _fmt_earned(5.0 + 1 / 24) == "5 дн. 1 ч"
    assert _fmt_earned(1.0 + 2 / 24) == "1 дн. 2 ч"


async def test_ensure_referral_code_random_stable_not_tg_id(db):
    async with db.session() as session:
        user = await get_or_create_user(session, {"id": 8008, "username": "coded"})
        code = await ensure_referral_code(session, user)
        assert code is not None
        assert len(code) == 8
        assert set(code) <= set(REFERRAL_CODE_ALPHABET)
        assert str(user.telegram_id) not in code
        user_id = user.id

    # Стабильность: повторный вызов в новой сессии возвращает тот же код.
    async with db.session() as session:
        fresh = await session.get(User, user_id)
        assert await ensure_referral_code(session, fresh) == code


async def test_resolve_referral_by_code_case_insensitive(db):
    async with db.session() as session:
        referrer = await get_or_create_user(session, {"id": 9009, "username": "ref9"})
        code = await ensure_referral_code(session, referrer)
        invited = await get_or_create_user(session, {"id": 9010, "username": "inv10"})
        found = await resolve_referral(session, f"ref_{code.lower()}", invited)
        assert found is not None
        assert found.id == referrer.id


async def test_resolve_referral_legacy_tg_id_still_works(db):
    async with db.session() as session:
        referrer = await get_or_create_user(session, {"id": 9109, "username": "ref10"})
        invited = await get_or_create_user(session, {"id": 9110, "username": "inv11"})
        found = await resolve_referral(session, f"ref_{referrer.telegram_id}", invited)
        assert found is not None
        assert found.id == referrer.id


async def test_resolve_referral_rejects_self_unknown_and_garbage(db):
    async with db.session() as session:
        user = await get_or_create_user(session, {"id": 9209, "username": "solo"})
        code = await ensure_referral_code(session, user)
        # Самореферал по своему коду и по своему легаси-tg_id.
        assert await resolve_referral(session, f"ref_{code}", user) is None
        assert await resolve_referral(session, f"ref_{user.telegram_id}", user) is None
        # Мусор: чужой код, неизвестный payload, не-ref payload, пустой код.
        assert await resolve_referral(session, "ref_ZZZZZZZZ", user) is None
        assert await resolve_referral(session, "ref_", user) is None
        assert await resolve_referral(session, "invite_me", user) is None


async def test_bind_via_code_awards_invite_hour(db, settings):
    referrer = None
    invited = None
    async with db.session() as session:
        referrer = await get_or_create_user(session, {"id": 9309, "username": "ref12"})
        code = await ensure_referral_code(session, referrer)
        invited = await get_or_create_user(session, {"id": 9310, "username": "inv12"})
        found = await resolve_referral(session, f"ref_{code}", invited)
        assert found is not None
        invited.referred_by = found.id
        await session.flush()

    async with db.session() as session:
        current = await session.get(User, invited.id)
        result = await award_referral_invite(session, current, settings)
        assert result is not None
        ref_fresh, hours = result
        assert hours == 1.0
        assert ref_fresh.telegram_id == referrer.telegram_id
        delta = ref_fresh.pro_expires_at - datetime.utcnow()
        assert timedelta(minutes=55) < delta < timedelta(minutes=65)


def test_referral_url_uses_code_payload():
    from bot.handlers.referral import _referral_url

    assert _referral_url("GiftTracker_App", "AB2CD3EF") == (
        "https://t.me/GiftTracker_App?start=ref_AB2CD3EF"
    )


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
