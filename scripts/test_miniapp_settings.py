#!/usr/bin/env python3
"""Проверки initData, секций настроек и отказа API без подписи."""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import sys
import time
from pathlib import Path
from urllib.parse import urlencode

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from aiohttp.test_utils import TestClient, TestServer

from app.config import Settings
from app.db.models import UserProfile
from app.services.access import AccessService
from app.services.crypto import SecretBox
from app.services.profile import ProfileService
from app.services.settings_form import merge_section, normalize_phone
from app.web.init_data import InitDataError, validate_init_data
from app.web.server import build_app


def _signed_init_data(bot_token: str, user_id: int, *, auth_date: int | None = None) -> str:
    payload = {
        "auth_date": str(auth_date if auth_date is not None else int(time.time())),
        "query_id": "AAE",
        "user": json.dumps({"id": user_id, "first_name": "Ann"}, separators=(",", ":")),
    }
    data_check = "\n".join(f"{key}={payload[key]}" for key in sorted(payload))
    secret = hmac.new(b"WebAppData", bot_token.encode(), hashlib.sha256).digest()
    payload["hash"] = hmac.new(secret, data_check.encode(), hashlib.sha256).hexdigest()
    return urlencode(payload)


def test_init_data() -> None:
    token = "123456:TEST"
    raw = _signed_init_data(token, 42)
    identity = validate_init_data(raw, token)
    assert identity["user_id"] == 42
    tampered = raw.replace("Ann", "Bob")
    try:
        validate_init_data(tampered, token)
    except InitDataError:
        pass
    else:
        raise AssertionError("tampered initData accepted")
    old = _signed_init_data(token, 42, auth_date=int(time.time()) - 90_000)
    try:
        validate_init_data(old, token)
    except InitDataError:
        pass
    else:
        raise AssertionError("expired initData accepted")


def test_merge() -> None:
    assert normalize_phone("89001234567") == "+79001234567"
    current = {
        "cdek_client_id": "client-id-1",
        "cdek_client_secret": "secret-value",
        "cdek_test_mode": False,
        "cdek_shipment_point": "PRM17",
        "cdek_order_type": 1,
    }
    updates, errors = merge_section(
        "cdek",
        current,
        {
            "cdek_client_id": "",
            "cdek_client_secret": "",
            "cdek_test_mode": True,
            "cdek_shipment_point": "msk45",
            "cdek_order_type": 1,
        },
    )
    assert errors == []
    assert "cdek_client_id" not in updates
    assert updates["cdek_shipment_point"] == "MSK45"
    assert updates["cdek_test_mode"] is True

    _, missing = merge_section(
        "cdek",
        {**current, "cdek_client_id": "", "cdek_client_secret": ""},
        {"cdek_shipment_point": "PRM17", "cdek_test_mode": False, "cdek_order_type": 1},
    )
    assert any("CLIENT_ID" in item for item in missing)

    updates, errors = merge_section(
        "sender",
        {},
        {"sender_name": "Иван Иванов", "sender_phone": "8 (900) 123-45-67"},
    )
    assert errors == []
    assert updates["sender_phone"] == "+79001234567"


def test_public_view_hides_secrets() -> None:
    box = SecretBox("test-key")
    profiles = ProfileService(box)
    profile = UserProfile(
        telegram_user_id=1,
        setup_completed=True,
        cdek_client_id_enc=box.encrypt("client-id-1"),
        cdek_client_secret_enc=box.encrypt("secret-value"),
        dadata_api_key_enc=box.encrypt("dadata-api-key"),
        dadata_secret_key_enc=box.encrypt("dadata-secret"),
        cdek_test_mode=False,
        cdek_shipment_point="PRM17",
        cdek_order_type=1,
        sender_name="Иван",
        sender_phone="+79001234567",
        weight_g=100,
        length_cm=10,
        width_cm=10,
        height_cm=5,
        item_name="Бижутерия",
        item_ware_key="JEWELRY-1",
    )
    view = profiles.public_view(profile)
    blob = json.dumps(view)
    assert "secret-value" not in blob
    assert "dadata-secret" not in blob
    assert view["cdek_client_secret_set"] is True
    assert view["ready"] is True
    assert view["cdek_shipment_point"] == "PRM17"


async def test_api_requires_init_data() -> None:
    settings = Settings(
        BOT_TOKEN="123456:TEST",
        ENCRYPTION_KEY="test-key",
        DATABASE_URL="postgresql+asyncpg://cdek:cdek@localhost/cdek_bot",
        WEBAPP_URL="",
    )
    app = build_app(
        settings,
        session_factory=None,
        profiles=ProfileService(SecretBox("test-key")),
        access=AccessService(settings),
    )
    async with TestClient(TestServer(app)) as client:
        health = await client.get("/health")
        assert health.status == 200
        page = await client.get("/settings")
        assert page.status == 200
        html = await page.text()
        assert "Сохранить СДЭК" in html
        denied = await client.get("/miniapp/settings")
        assert denied.status == 401


def main() -> int:
    test_init_data()
    test_merge()
    test_public_view_hides_secrets()
    asyncio.run(test_api_requires_init_data())
    print("OK miniapp settings")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
