"""Общие фикстуры тестов GiftTracker.

Каждый тест получает изолированную базу SQLite во временном каталоге
и свой экземпляр Settings (без чтения .env разработчика).
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from bot.config import Settings  # noqa: E402
from bot.database import Database  # noqa: E402

TEST_BOT_TOKEN = "TEST:TOKEN:FOR:PYTEST"


def make_settings(tmp_path: Path, **overrides) -> Settings:
    """Settings с временной базой; kwargs имеют наивысший приоритет."""
    defaults = {
        "DATABASE_URL": f"sqlite+aiosqlite:///{(tmp_path / 'test.db').as_posix()}",
        "BOT_TOKEN": TEST_BOT_TOKEN,
        "MARKET_SOURCE": "mock",
        "FREE_PUSH_DELAY_SEC": 0,
        "SCAN_INTERVAL_SEC": 0.01,
        # Heartbeat не должен трогать реальный ./data во время тестов.
        "BOT_HEARTBEAT_FILE": str(tmp_path / "bot_heartbeat.json"),
        # Кэш адресов коллекций тоже изолируем: иначе тесты подхватят
        # реальный data/gift_collections.json и «найдут» адреса у всех.
        "GIFT_COLLECTIONS_FILE": str(tmp_path / "gift_collections.json"),
        "INIT_DATA_MAX_AGE_SEC": 0,  # в тестах датасет auth_date статичен (1770000000)
        "_env_file": None,  # игнорируем .env окружения разработчика
    }
    defaults.update(overrides)
    return Settings(**defaults)


@pytest.fixture()
def settings(tmp_path: Path) -> Settings:
    return make_settings(tmp_path)


@pytest.fixture()
def client(tmp_path: Path):
    from starlette.testclient import TestClient
    from api.main import create_app
    settings = make_settings(tmp_path)
    app = create_app(settings)
    with TestClient(app) as test_client:
        yield test_client


@pytest.fixture()
async def db(settings: Settings):
    """Инициализированная база с таблицами; закрывается после теста."""
    database = Database(settings)
    await database.init()
    try:
        yield database
    finally:
        await database.dispose()


def sign_init_data(bot_token: str, user_payload: dict) -> str:
    """Подписывает initData так же, как это делает Telegram (HMAC-SHA256)."""
    import hashlib
    import hmac
    import json
    import urllib.parse

    params = {
        "user": json.dumps(user_payload, separators=(",", ":")),
        "auth_date": "1770000000",
        "query_id": "AAF_pytest",
    }
    data_check_string = "\n".join(f"{k}={params[k]}" for k in sorted(params))
    secret_key = hmac.new(b"WebAppData", bot_token.encode(), hashlib.sha256).digest()
    params["hash"] = hmac.new(secret_key, data_check_string.encode(), hashlib.sha256).hexdigest()
    return urllib.parse.urlencode(params)


__all__ = ["TEST_BOT_TOKEN", "make_settings", "sign_init_data"]
