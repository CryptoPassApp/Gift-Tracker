"""Лицензирование GiftTracker: проверка license.key при старте.

Лицензия — подписанная Ed25519 строка вида
``base64url(payload_json).base64url(signature)``:

    {"product":"gifttracker","buyer":"...","tg_id":123,
     "token_hash":"<sha256(BOT_TOKEN)>","issued":"YYYY-MM-DD",
     "expires":null|"YYYY-MM-DD","note":"...","version":1}

Приватный ключ у продавца; в коде только публичный — лицензию можно
проверить, подделать нельзя. Привязка двойная:

  * ``token_hash`` == sha256(BOT_TOKEN из .env) — лицензия живет на
    конкретном боте покупателя;
  * ``tg_id`` входит в ADMIN_IDS — лицензия живет на конкретном владельце.

Смена токена бота или покупка «своего» бота под чужую лицензию требует
нового license.key от продавца.

Проверка — чистый stdlib (RFC 8032, только verify), никаких новых
зависимостей проекту это не добавляет.
"""
from __future__ import annotations

import base64
import binascii
import hashlib
import json
import time
from pathlib import Path
from typing import Any

# Публичный ключ продавца (Ed25519, raw 32 байта). Пару ключей не терять:
# без приватной части выпуск лицензий невозможен.
LICENSE_PUBKEY_HEX = (
    "b7bf7e566d7fab9dbc5d2074a3f48264f9befa0c9748543c49044b795b625488"
)

PRODUCT = "gifttracker"
LICENSE_FILENAME = "license.key"

# Корень проекта (gift_tracker/): licensing.py лежит в bot/.
BASE_DIR = Path(__file__).resolve().parent.parent


class LicenseError(Exception):
    """Лицензия отсутствует, не прошла подпись или не совпала привязка."""


# ---------------------------------------------------------------------------
# Ed25519 verify (RFC 8032), только проверка подписи, ~60 строк stdlib.
# ---------------------------------------------------------------------------

_P = 2**255 - 19
_L = 2**252 + 27742317777372353535851937790883648493
_D = (-121665 * pow(121666, _P - 2, _P)) % _P
_I = pow(2, (_P - 1) // 4, _P)


def _curve_xx(y: int) -> int:
    """x^2 из уравнения кривой: x^2 = (y^2 - 1) / (1 + d*y^2)."""
    return ((y * y - 1) * pow((1 + _D * y * y) % _P, _P - 2, _P)) % _P


def _xrecover(y: int) -> int:
    """Восстанавливает координату x по y (меньший корень, знак сверху)."""
    xx = _curve_xx(y)
    x = pow(xx, (_P + 3) // 8, _P)
    if (x * x - xx) % _P != 0:
        x = (x * _I) % _P
    if (x * x - xx) % _P != 0:
        raise ValueError("точка не на кривой")
    if x % 2 != 0:
        x = _P - x
    return x


# Базовая точка B в расширенных координатах (x, y, z, x*y); y = 4/5 mod p.
_BY = (4 * pow(5, _P - 2, _P)) % _P
_BX = _xrecover(_BY)
_B = (_BX % _P, _BY % _P, 1, (_BX * _BY) % _P)
_IDENTITY = (0, 1, 1, 0)


def _edwards_add(p: tuple[int, int, int, int], q: tuple[int, int, int, int]) -> tuple[int, int, int, int]:
    x1, y1, z1, t1 = p
    x2, y2, z2, t2 = q
    a = ((y1 - x1) * (y2 - x2)) % _P
    b = ((y1 + x1) * (y2 + x2)) % _P
    c = (t1 * 2 * _D * t2) % _P
    d = (z1 * 2 * z2) % _P
    e, f, g, h = b - a, d - c, d + c, b + a
    return (e * f) % _P, (g * h) % _P, (f * g) % _P, (e * h) % _P


def _scalarmult(point: tuple[int, int, int, int], scalar: int) -> tuple[int, int, int, int]:
    q = _IDENTITY
    while scalar > 0:
        if scalar & 1:
            q = _edwards_add(q, point)
        point = _edwards_add(point, point)
        scalar >>= 1
    return q


def _point_equal(p: tuple[int, int, int, int], q: tuple[int, int, int, int]) -> bool:
    x1, y1, z1, _ = p
    x2, y2, z2, _ = q
    return (x1 * z2 - x2 * z1) % _P == 0 and (y1 * z2 - y2 * z1) % _P == 0


def _decompress(s32: bytes) -> tuple[int, int, int, int] | None:
    """32 байта -> точка (x, y, 1, x*y) или None, если точка невалидна."""
    if len(s32) != 32:
        return None
    val = int.from_bytes(s32, "little")
    sign = val >> 255
    y = val & ((1 << 255) - 1)
    if y >= _P:
        return None
    xx = _curve_xx(y)
    x = pow(xx, (_P + 3) // 8, _P)
    if (x * x - xx) % _P != 0:
        x = (x * _I) % _P
    if (x * x - xx) % _P != 0:
        return None
    if x == 0 and sign:
        return None
    if x & 1 != sign:
        x = _P - x
    return (x, y, 1, (x * y) % _P)


def ed25519_verify(public: bytes, signature: bytes, message: bytes) -> bool:
    """Проверка подписи Ed25519: [S]B == R + h*A (канонический S < L)."""
    if len(public) != 32 or len(signature) != 64:
        return False
    a_point = _decompress(public)
    if a_point is None:
        return False
    r_point = _decompress(signature[:32])
    if r_point is None:
        return False
    s = int.from_bytes(signature[32:], "little")
    if s >= _L:  # неканоническая подпись — отбраковываем
        return False
    h = int.from_bytes(
        hashlib.sha512(signature[:32] + public + message).digest(), "little"
    ) % _L
    left = _scalarmult(_B, s)
    right = _edwards_add(r_point, _scalarmult(a_point, h))
    return _point_equal(left, right)


# ---------------------------------------------------------------------------
# Разбор и проверка license.key
# ---------------------------------------------------------------------------

def _b64u_decode(part: str) -> bytes:
    padding = "=" * (-len(part) % 4)
    return base64.urlsafe_b64decode(part + padding)


def load_license(path: Path | None = None) -> dict[str, Any]:
    """Читает и проверяет подпись license.key, возвращает payload."""
    path = path or (BASE_DIR / LICENSE_FILENAME)
    if not path.exists():
        raise LicenseError(
            f"Не найден {path.name}. Положите файл лицензии от продавца в корень "
            f"проекта ({BASE_DIR.name}{path.sep}{path.name}) и перезапустите."
        )
    text = path.read_text(encoding="utf-8").strip()
    if text.count(".") != 1:
        raise LicenseError("license.key поврежден (нет подписи).")
    payload_b64, sig_b64 = text.split(".")
    try:
        raw = _b64u_decode(payload_b64)
        sig = _b64u_decode(sig_b64)
        payload = json.loads(raw.decode("utf-8"))
    except (binascii.Error, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise LicenseError(f"license.key не читается: {exc}") from exc

    pubkey = bytes.fromhex(LICENSE_PUBKEY_HEX)
    if not ed25519_verify(pubkey, sig, raw):
        raise LicenseError("Подпись license.key неверна (файл не от продавца или изменен).")
    if not isinstance(payload, dict) or payload.get("product") != PRODUCT:
        raise LicenseError("license.key от другого продукта.")
    return payload


def verify_binding(payload: dict[str, Any], bot_token: str, admin_ids: list[int]) -> str:
    """Проверяет привязку лицензии к боту и владельцу. Возвращает имя покупателя."""
    token_hash = hashlib.sha256((bot_token or "").encode("utf-8")).hexdigest()
    if payload.get("token_hash") != token_hash:
        raise LicenseError(
            "Лицензия выпущена для другого токена бота. Укажите в .env токен, "
            "на который выписана лицензия, или запросите перевыпуск у продавца."
        )
    if int(payload.get("tg_id") or 0) not in {int(i) for i in admin_ids}:
        raise LicenseError(
            "Telegram ID лицензии не входит в ADMIN_IDS из .env. Добавьте "
            f"ID {payload.get('tg_id')} в ADMIN_IDS или запросите перевыпуск."
        )
    expires = payload.get("expires")
    if expires:
        try:
            exp_ts = time.mktime(time.strptime(str(expires), "%Y-%m-%d")) + 86400
        except ValueError as exc:
            raise LicenseError("license.key: некорректная дата expires.") from exc
        if time.time() > exp_ts:
            raise LicenseError(f"Лицензия истекла {expires}. Запросите продление у продавца.")
    return str(payload.get("buyer") or "unknown")


def ensure_licensed(bot_token: str, admin_ids: list[int], path: Path | None = None) -> dict[str, Any]:
    """Полная проверка для старта процесса. Бросает LicenseError / возвращает payload."""
    payload = load_license(path)
    buyer = verify_binding(payload, bot_token, admin_ids)
    return {"buyer": buyer, "expires": payload.get("expires")}


__all__ = [
    "LICENSE_FILENAME",
    "LicenseError",
    "ed25519_verify",
    "ensure_licensed",
    "load_license",
    "verify_binding",
]
