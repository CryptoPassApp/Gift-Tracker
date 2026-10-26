"""Тесты валидации Telegram WebApp initData (HMAC-SHA256)."""
from __future__ import annotations

import hashlib
import hmac
import json
import urllib.parse

import pytest
from fastapi import HTTPException

from api.dependencies import validate_init_data
from tests.conftest import TEST_BOT_TOKEN, sign_init_data


def build_init_data(bot_token: str, user: dict, tamper_hash: bool = False) -> str:
    params = {
        "user": json.dumps(user, separators=(",", ":")),
        "auth_date": "1770000000",
        "query_id": "AAF_pytest",  # должен совпадать с query_id в tests/conftest.py
    }
    dcs = "\n".join(f"{k}={params[k]}" for k in sorted(params))
    secret = hmac.new(b"WebAppData", bot_token.encode(), hashlib.sha256).digest()
    params["hash"] = hmac.new(secret, dcs.encode(), hashlib.sha256).hexdigest()
    if tamper_hash:
        params["hash"] = ("0" if params["hash"][0] != "0" else "1") + params["hash"][1:]
    return urllib.parse.urlencode(params)


USER = {"id": 4242, "first_name": "Тест", "username": "tester"}


class TestValidateInitData:
    def test_valid_signature_returns_user(self):
        payload = validate_init_data(build_init_data(TEST_BOT_TOKEN, USER), TEST_BOT_TOKEN)
        assert payload["id"] == 4242
        assert payload["username"] == "tester"

    def test_tampered_hash_rejected(self):
        raw = build_init_data(TEST_BOT_TOKEN, USER, tamper_hash=True)
        with pytest.raises(HTTPException) as exc:
            validate_init_data(raw, TEST_BOT_TOKEN)
        assert exc.value.status_code == 401

    def test_wrong_bot_token_rejected(self):
        raw = build_init_data("ANOTHER:TOKEN", USER)
        with pytest.raises(HTTPException):
            validate_init_data(raw, TEST_BOT_TOKEN)

    def test_missing_hash_rejected(self):
        params = {"user": json.dumps(USER), "auth_date": "1"}
        raw = urllib.parse.urlencode(params)
        with pytest.raises(HTTPException):
            validate_init_data(raw, TEST_BOT_TOKEN)

    def test_garbage_input_rejected(self):
        for raw in ("", "not-a-query-string", "foo=bar", "user=notjson&hash=ab" * 3):
            with pytest.raises(HTTPException):
                validate_init_data(raw, TEST_BOT_TOKEN)

    def test_sign_helper_matches_telegram_format(self):
        """Хелпер conftest и ручная сборка дают идентичную подпись."""
        assert sign_init_data(TEST_BOT_TOKEN, USER) == build_init_data(TEST_BOT_TOKEN, USER)
