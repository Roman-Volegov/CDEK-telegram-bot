"""Проверка Telegram WebApp initData."""

from __future__ import annotations

import hashlib
import hmac
import json
import time
from urllib.parse import parse_qsl


class InitDataError(ValueError):
    pass


def validate_init_data(
    init_data: str,
    bot_token: str,
    *,
    max_age_seconds: int = 86_400,
    now: float | None = None,
) -> dict:
    """
    Возвращает словарь с user_id и полями user.
    https://core.telegram.org/bots/webapps#validating-data-received-via-the-mini-app
    """
    raw = (init_data or "").strip()
    if not raw or not bot_token:
        raise InitDataError("Нет данных Telegram")

    pairs = parse_qsl(raw, keep_blank_values=True, strict_parsing=False)
    parsed = {key: value for key, value in pairs}
    received_hash = parsed.pop("hash", "")
    if not received_hash:
        raise InitDataError("Нет подписи Telegram")

    data_check = "\n".join(f"{key}={parsed[key]}" for key in sorted(parsed))
    secret = hmac.new(b"WebAppData", bot_token.encode("utf-8"), hashlib.sha256).digest()
    calculated = hmac.new(secret, data_check.encode("utf-8"), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(calculated, received_hash):
        raise InitDataError("Подпись Telegram не сошлась")

    try:
        auth_date = int(parsed.get("auth_date") or "0")
    except ValueError as exc:
        raise InitDataError("Некорректная дата входа") from exc
    current = time.time() if now is None else now
    if auth_date <= 0 or current - auth_date > max_age_seconds:
        raise InitDataError("Сессия Mini App устарела. Откройте настройки заново.")

    try:
        user = json.loads(parsed.get("user") or "")
        user_id = int(user["id"])
    except (TypeError, ValueError, KeyError, json.JSONDecodeError) as exc:
        raise InitDataError("В initData нет пользователя") from exc

    return {
        "user_id": user_id,
        "username": user.get("username"),
        "full_name": " ".join(
            part for part in (user.get("first_name"), user.get("last_name")) if part
        ).strip(),
        "auth_date": auth_date,
        "user": user,
    }
