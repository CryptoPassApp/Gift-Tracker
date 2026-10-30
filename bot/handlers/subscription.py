"""Subscription — продажа PRO за Telegram Stars и крипту (Crypto Pay).

Stars-поток:
  1. /pro или кнопка меню -> клавиатура тарифов;
  2. callback pro:month / pro:lifetime -> send_invoice (currency="XTR");
  3. pre_checkout_query -> валидация суммы и payload, answer(ok=True);
  4. successful_payment -> запись Payment, активация PRO.

Крипто-поток (кнопки видны только при заданном CRYPTO_PAY_TOKEN):
  1. callback pro:crypto:<plan> -> CryptoPayService.create_for_user()
     -> сообщение со ссылкой на инвойс в @CryptoBot;
  2. юзер платит; фон-джоба сервиса сверяет оплаты раз в
     CRYPTO_POLL_INTERVAL_SEC секунд;
  3. кнопка «Я уже оплатил» (pro:crypto:check:<invoice_id>) проверяет
     инвойс точечно и активирует PRO без ожидания джобы.
"""
from __future__ import annotations

import logging

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.types import CallbackQuery, LabeledPrice, Message, PreCheckoutQuery, SuccessfulPayment
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from bot.config import Settings, get_settings
from bot.database import activate_pro, award_referral
from bot.handlers.ui import edit_screen
from bot.keyboards import crypto_invoice_kb, main_menu_kb, pro_plans_kb
from bot.models import Payment, User
from bot.services.crypto_payments import CryptoPayError, CryptoPaymentService

logger = logging.getLogger(__name__)

router = Router(name="subscription")

PRO_TEXT = (
    "👑 <b>VIP SNIPER PASS</b>\n"
    "\n"
    "Твое оружие против 45-секундной задержки:\n"
    "\n"
    "⚡ <b>Пуши мгновенно</b> — лот −34% придет в ту же секунду, когда появится\n"
    "🔔 <b>Безлимитные алерты</b> — на любые подарки и пороги\n"
    "🚀 <b>Диплинки Tonkeeper</b> — покупка в 1 клик из пуша\n"
    "🎚 <b>Фильтр дисконта</b> — слать только лоты от −30%\n"
    "\n"
    "⭐ <b>150 Stars</b> — месяц (~$3)\n"
    "👑 <b>999 Stars</b> — навсегда (~$20, экономия 44%)\n"
    "\n"
    "Оплата внутри Telegram — без карт и ввода данных.\n"
    "💰 Можно и криптой (USDT/TON) — разовый платеж через @CryptoBot."
)


def _plan_payloads(settings: Settings) -> dict[str, int]:
    """payload -> стоимость в Stars (для валидации на pre_checkout)."""
    return {
        "pro_month": settings.PRO_MONTH_STARS,
        "pro_lifetime": settings.PRO_LIFETIME_STARS,
    }


def _crypto_prices(settings: Settings) -> dict[str, float] | None:
    """Цены крипто-тарифов для клавиатуры (None = фича выключена)."""
    if not settings.CRYPTO_PAY_TOKEN:
        return None
    return {
        "pro_month": settings.CRYPTO_MONTH_USD,
        "pro_lifetime": settings.CRYPTO_LIFETIME_USD,
    }


@router.message(Command("pro"))
async def cmd_pro(message: Message, db_user: User) -> None:
    """/pro — показать тарифы."""
    prices = _crypto_prices(get_settings())
    if db_user.is_pro_active():
        await message.answer(
            "👑 Ты уже <b>PRO-снайпер</b> — пуши летят мгновенно!\n"
            "Можно продлить или перейти на Lifetime 👇",
            reply_markup=pro_plans_kb(prices),
        )
        return
    await message.answer(PRO_TEXT, reply_markup=pro_plans_kb(prices))


@router.callback_query(F.data == "menu:pro")
async def cb_menu_pro(callback: CallbackQuery, db_user: User) -> None:
    """Кнопка «Купить PRO» — переключает экран тарифов редактированием."""
    await edit_screen(callback, PRO_TEXT, pro_plans_kb(_crypto_prices(get_settings())))
    await callback.answer()


@router.callback_query(F.data.in_({"pro:month", "pro:lifetime"}))
async def cb_buy_pro(callback: CallbackQuery, db_user: User) -> None:
    """Создает инвойс на оплату Stars по выбранному тарифу."""
    settings = get_settings()
    plan = "pro_month" if callback.data == "pro:month" else "pro_lifetime"
    plan_info = settings.plans[plan]

    if callback.message is None:
        await callback.answer()
        return

    await callback.message.answer_invoice(
        title=str(plan_info["title"]),
        description=str(plan_info["description"]),
        payload=plan,
        provider_token="",          # для Telegram Stars (XTR) токен провайдера пустой
        currency="XTR",
        prices=[LabeledPrice(label=str(plan_info["title"]), amount=int(plan_info["stars"]))],
    )
    await callback.answer("Счет создан ⭐")


@router.callback_query(F.data.in_({"pro:crypto:month", "pro:crypto:lifetime"}))
async def cb_buy_crypto(
    callback: CallbackQuery, db_user: User, crypto: CryptoPaymentService | None = None
) -> None:
    """Создает крипто-инвойс Crypto Pay и присылает ссылку на оплату."""
    if callback.data is None:
        await callback.answer()
        return
    plan = f"pro_{callback.data.rsplit(':', maxsplit=1)[1]}"
    service = crypto

    if service is None or not service.enabled:
        await callback.answer("Крипто-оплата временно недоступна", show_alert=True)
        return
    if callback.message is None:
        await callback.answer()
        return

    try:
        invoice_id, url = await service.create_for_user(db_user, plan)
        amount = service.price_for(plan)
        asset = service.settings.CRYPTO_ASSET
    except CryptoPayError as exc:
        logger.warning("Crypto invoice create failed for %s: %s", db_user.telegram_id, exc)
        await callback.answer("Не удалось создать счет. Попробуй позже.", show_alert=True)
        return

    plan_title = "PRO месяц" if plan == "pro_month" else "PRO навсегда"
    await callback.message.answer(
        "💳 <b>Счет на оплату криптой</b>\n"
        f"Тариф: <b>{plan_title}</b> — <b>${amount:g}</b> ({asset})\n"
        "\n"
        "Оплата в @CryptoBot — USDT, TON и другие валюты, разовый платеж.\n"
        "После оплаты нажми «Я уже оплатил» — PRO включится автоматически.",
        reply_markup=crypto_invoice_kb(url, invoice_id),
    )
    await callback.answer()


@router.callback_query(F.data.startswith("pro:crypto:check:"))
async def cb_crypto_check(
    callback: CallbackQuery, crypto: CryptoPaymentService | None = None
) -> None:
    """«Я уже оплатил»: точечная проверка инвойса и активация PRO."""
    raw = (callback.data or "").rsplit(":", maxsplit=1)[1]
    if not raw.isdigit() or crypto is None:
        await callback.answer("Счет не найден", show_alert=True)
        return
    invoice_id = int(raw)

    payment = await crypto.db.get_crypto_invoice(invoice_id)
    already_paid = payment is not None and payment.status == "paid"
    activated = False
    if not already_paid:
        try:
            activated = await crypto.check_invoice_now(invoice_id)
        except CryptoPayError as exc:
            logger.warning("Crypto check failed for #%d: %s", invoice_id, exc)

    if already_paid or activated:
        await callback.answer("👑 Оплата зачислена — PRO активен!", show_alert=True)
    else:
        await callback.answer(
            "Пока не вижу оплаты. Платеж может идти пару минут — нажми кнопку еще раз.",
            show_alert=True,
        )


@router.pre_checkout_query()
async def on_pre_checkout(query: PreCheckoutQuery) -> None:
    """Проверяет инвойс перед списанием Stars."""
    settings = get_settings()
    valid = _plan_payloads(settings)

    payload = query.invoice_payload
    amount = query.total_amount
    ok = payload in valid and amount == valid[payload] and query.currency == "XTR"

    if ok:
        await query.answer(ok=True)
    else:
        logger.warning("Pre-checkout rejected: payload=%s amount=%s", payload, amount)
        await query.answer(ok=False, error_message="Тариф не найден. Открой /pro заново.")


@router.message(F.successful_payment)
async def on_successful_payment(
    message: Message, session: AsyncSession, db_user: User
) -> None:
    """Успешная оплата: фиксируем платеж и активируем PRO."""
    settings = get_settings()
    payment: SuccessfulPayment = message.successful_payment

    plan = payment.invoice_payload
    if plan not in _plan_payloads(settings):
        logger.error("Unknown invoice payload in successful_payment: %s", plan)
        await message.answer("⚠️ Платеж получен, но тариф не распознан. Напиши админу.")
        return

    try:
        session.add(
            Payment(
                user_id=db_user.id,
                telegram_charge_id=payment.telegram_payment_charge_id,
                stars_amount=payment.total_amount,
                plan=plan,
            )
        )
        await session.flush()
    except IntegrityError:
        # Дубликат charge_id (повторная доставка апдейта) — платеж уже учтен.
        await session.rollback()
        await message.answer("✅ Этот платеж уже зачислен. PRO активен!")
        return

    await activate_pro(session, db_user, plan, month_days=settings.PRO_MONTH_DAYS)

    # Рефералка: пригласивший получает дни PRO за оплату приглашенного.
    reward = await award_referral(
        session, db_user, f"stars:{payment.telegram_payment_charge_id}", "stars"
    )

    expires = "навсегда 👑" if plan == "pro_lifetime" else db_user.pro_expires_at.strftime("%d.%m.%Y")
    await message.answer(
        "👑 <b>PRO активирован!</b>\n"
        f"Списано: <b>{payment.total_amount}★</b>\n"
        f"Действует: <b>{expires}</b>\n"
        "\n"
        "⚡ Теперь пуши о жирных лотах приходят <b>мгновенно</b>.\n"
        "Настрой алерты в терминале и забирайте снайпы!",
        reply_markup=main_menu_kb(settings.MINI_APP_URL),
    )
    logger.info(
        "PRO activated: user=%s plan=%s stars=%s charge=%s",
        db_user.telegram_id,
        plan,
        payment.total_amount,
        payment.telegram_payment_charge_id,
    )

    if reward is not None:
        referrer, days = reward
        try:
            await message.bot.send_message(
                referrer.telegram_id,
                f"🤝 Твой приглашенный оплатил PRO — <b>+{days:g} дней</b> начислено автоматически!",
            )
        except Exception:  # noqa: BLE001 — уведомление не ломает активацию
            logger.warning("Referral notify failed for %s", referrer.telegram_id, exc_info=True)


__all__ = ["router", "PRO_TEXT"]
