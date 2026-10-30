with open("bot/services/notifier.py", "r", encoding="utf-8") as f:
    text = f.read()

# 1. Update imports
target_imp = "from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup"
replacement_imp = "from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup, WebAppInfo"
assert target_imp in text, "target_imp not found"
text = text.replace(target_imp, replacement_imp, 1)

# 2. Update _signal_text, _alert_text, _snipe_keyboard
target_block = """def _signal_text(signal: Signal) -> str:
    \"\"\"Строка атрибутов конкретного NFT (Model · Backdrop · Symbol).\"\"\"
    header = "🔥 HOT LIQUIDATION" if signal.is_hot else "⚡ SNIPE SIGNAL"
    basis = (
        f"модели {signal.floor_basis.split(':', 1)[1]}"
        if signal.floor_basis and signal.floor_basis.startswith("model:")
        else "коллекции"
    )
    return (
        f"{header} — <b>−{signal.discount_pct}% BELOW FLOOR</b>\\n"
        f"\\n"
        f"🎁 <b>{signal.gift_name}</b> · Lot {signal.lot_number}{_traits_line(signal)}\\n"
        f"💵 Buy Now: <b>{signal.deal_price} TON</b>\\n"
        f"📈 Floor ({basis}): <s>{signal.floor_price} TON</s>\\n"
        f"💰 Profit: <b>+{signal.net_profit_ton} TON</b> <i>(net after fees)</i>"
    )


def _alert_text(signal: Signal, alert: Alert) -> str:
    \"\"\"Текст пуша о сработавшем персональном алерте.\"\"\"
    return (
        f"🔔 <b>Ваш алерт сработал!</b>\\n"
        f"\\n"
        f"🎁 <b>{signal.gift_name}</b> · Lot {signal.lot_number}{_traits_line(signal)}\\n"
        f"💵 Цена: <b>{signal.deal_price} TON</b> "
        f"(триггер: ≤ {alert.max_price} TON)\\n"
        f"📉 Дисконт: <b>−{signal.discount_pct}%</b> "
        f"(порог: −{alert.min_discount}%)\\n"
        f"💰 Profit: <b>+{signal.net_profit_ton} TON</b> net"
    )


def _snipe_keyboard(url: str, is_pro: bool) -> InlineKeyboardMarkup:
    \"\"\"Кнопка покупки: PRO получает прямой диплинк Tonkeeper/Getgems.\"\"\"
    button = InlineKeyboardButton(
        text=("⚡ Snipe via Tonkeeper" if is_pro else "⚡ Открыть лот"),
        url=url,
    )
    return InlineKeyboardMarkup(inline_keyboard=[[button]])"""

replacement_block = """def _signal_text(signal: Signal) -> str:
    \"\"\"Текст пуша о горячем лоте с прямой ссылкой на NFT.\"\"\"
    header = "🔥 HOT LIQUIDATION" if signal.is_hot else "⚡ SNIPE SIGNAL"
    basis = (
        f"модели {signal.floor_basis.split(':', 1)[1]}"
        if signal.floor_basis and signal.floor_basis.startswith("model:")
        else "коллекции"
    )
    market_name = signal.source.title() if signal.source else "Getgems"
    lot_link = f'<a href="{signal.marketplace_url}"><b>{signal.gift_name}</b> · Lot {signal.lot_number}</a>'
    return (
        f"{header} — <b>−{signal.discount_pct}% BELOW FLOOR</b>\\n"
        f"\\n"
        f"🎁 {lot_link}{_traits_line(signal)}\\n"
        f"💵 Buy Now: <b>{signal.deal_price} TON</b>\\n"
        f"📈 Floor ({basis}): <s>{signal.floor_price} TON</s>\\n"
        f"💰 Profit: <b>+{signal.net_profit_ton} TON</b> <i>(net after fees)</i>\\n"
        f"🛒 Маркет: <b>{market_name}</b>\\n"
        f"🔗 Ссылка на лот: <a href=\"{signal.marketplace_url}\">{signal.marketplace_url}</a>"
    )


def _alert_text(signal: Signal, alert: Alert) -> str:
    \"\"\"Текст пуша о сработавшем персональном алерте с прямой ссылкой на NFT.\"\"\"
    market_name = signal.source.title() if signal.source else "Getgems"
    lot_link = f'<a href="{signal.marketplace_url}"><b>{signal.gift_name}</b> · Lot {signal.lot_number}</a>'
    return (
        f"🔔 <b>Ваш алерт сработал!</b>\\n"
        f"\\n"
        f"🎁 {lot_link}{_traits_line(signal)}\\n"
        f"💵 Цена: <b>{signal.deal_price} TON</b> "
        f"(триггер: ≤ {alert.max_price} TON)\\n"
        f"📉 Дисконт: <b>−{signal.discount_pct}%</b> "
        f"(порог: −{alert.min_discount}%)\\n"
        f"💰 Profit: <b>+{signal.net_profit_ton} TON</b> net\\n"
        f"🛒 Маркет: <b>{market_name}</b>\\n"
        f"🔗 Ссылка на лот: <a href=\"{signal.marketplace_url}\">{signal.marketplace_url}</a>"
    )


def _snipe_keyboard(url: str, is_pro: bool, mini_app_url: str | None = None) -> InlineKeyboardMarkup:
    \"\"\"Кнопки покупки:
    1. Прямой переход к лоту на маркетплейсе / Tonkeeper.
    2. Кнопка WebApp 'Открыть в терминале' (как у BotFather — открывает Mini App прямо в Telegram).
    \"\"\"
    rows = []
    buy_text = "⚡ Snipe via Tonkeeper" if is_pro else "⚡ Открыть лот"
    rows.append([InlineKeyboardButton(text=buy_text, url=url)])

    if mini_app_url and str(mini_app_url).startswith("https://"):
        rows.append([
            InlineKeyboardButton(
                text="📱 Открыть в терминале",
                web_app=WebAppInfo(url=mini_app_url),
            )
        ])
    return InlineKeyboardMarkup(inline_keyboard=rows)"""

# In case docstring in _signal_text is different
if target_block not in text:
    # Find start and end of that section
    idx_st = text.find("def _signal_text")
    idx_en = text.find("class Notifier:")
    text = text[:idx_st] + replacement_block + "\n\n\n" + text[idx_en:]
else:
    text = text.replace(target_block, replacement_block, 1)

# 3. Update call in _deliver
target_deliver = "keyboard = _snipe_keyboard(url, user.is_pro_active())"
replacement_deliver = "keyboard = _snipe_keyboard(url, user.is_pro_active(), mini_app_url=self.settings.MINI_APP_URL)"
assert target_deliver in text, "target_deliver not found"
text = text.replace(target_deliver, replacement_deliver, 1)

with open("bot/services/notifier.py", "w", encoding="utf-8") as f:
    f.write(text)

print("Updated bot/services/notifier.py successfully")
