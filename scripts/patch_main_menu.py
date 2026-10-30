with open("bot/main.py", "r", encoding="utf-8") as f:
    text = f.read()

target = """    await bot.set_my_commands(
        [
            BotCommand(command="start", description="⚡ Открыть Sniper Terminal"),
            BotCommand(command="alerts", description="🔔 Мои алерты"),
            BotCommand(command="pro", description="👑 Купить PRO за Stars"),
            BotCommand(command="settings", description="🎚 PRO-фильтр дисконта"),
            BotCommand(command="top", description="🏆 Топ-5 снайпов радара"),
        ]
    )"""

replacement = """    await bot.set_my_commands(
        [
            BotCommand(command="start", description="⚡ Открыть терминал"),
            BotCommand(command="alerts", description="🔔 Мои алерты"),
            BotCommand(command="pro", description="👑 Купить PRO за Stars"),
            BotCommand(command="settings", description="🎚 PRO-фильтр дисконта"),
            BotCommand(command="top", description="🏆 Топ-5 снайпов радара"),
        ]
    )
    if settings.MINI_APP_URL and settings.MINI_APP_URL.startswith("https://"):
        try:
            from aiogram.types import MenuButtonWebApp, WebAppInfo
            await bot.set_chat_menu_button(
                menu_button=MenuButtonWebApp(
                    text="Открыть",
                    web_app=WebAppInfo(url=settings.MINI_APP_URL),
                )
            )
        except Exception:
            logger.warning("Failed to update chat menu button", exc_info=True)"""

assert target in text, "target not found"
text = text.replace(target, replacement, 1)

with open("bot/main.py", "w", encoding="utf-8") as f:
    f.write(text)

print("Updated bot/main.py successfully")
