"""aiohttp: страница /settings и API профиля."""

from __future__ import annotations

import json
import logging
from pathlib import Path

from aiohttp import web

from app.config import Settings
from app.services.access import AccessService
from app.services.profile import ProfileService
from app.web.init_data import InitDataError, validate_init_data

logger = logging.getLogger(__name__)

_STATIC = Path(__file__).resolve().parent / "static" / "settings.html"
_JSON_DUMPS = lambda payload: json.dumps(payload, ensure_ascii=False)


def _json(payload: dict, *, status: int = 200) -> web.Response:
    return web.json_response(payload, status=status, dumps=_JSON_DUMPS)


def _init_data_from_request(request: web.Request) -> str:
    header = request.headers.get("Authorization", "")
    if header.lower().startswith("tma "):
        return header[4:].strip()
    return (request.headers.get("X-Telegram-Init-Data") or "").strip()


async def _authorized_user(request: web.Request) -> dict:
    settings: Settings = request.app["settings"]
    access: AccessService = request.app["access"]
    session_factory = request.app["session_factory"]
    try:
        identity = validate_init_data(_init_data_from_request(request), settings.bot_token)
    except InitDataError as exc:
        raise web.HTTPUnauthorized(
            text=_JSON_DUMPS({"error": str(exc)}),
            content_type="application/json",
        ) from exc

    decision = await access.evaluate(
        session_factory,
        identity["user_id"],
        identity.get("username"),
        identity.get("full_name"),
    )
    if not decision.allowed:
        raise web.HTTPForbidden(
            text=_JSON_DUMPS({"error": "Нет доступа к боту"}),
            content_type="application/json",
        )
    return identity


async def health(_request: web.Request) -> web.Response:
    return _json({"ok": True})


async def settings_page(_request: web.Request) -> web.Response:
    html = _STATIC.read_text(encoding="utf-8")
    return web.Response(
        text=html,
        content_type="text/html",
        charset="utf-8",
        headers={"Cache-Control": "no-store"},
    )


async def get_settings(request: web.Request) -> web.Response:
    identity = await _authorized_user(request)
    profiles: ProfileService = request.app["profiles"]
    profile = await profiles.get_or_none(
        request.app["session_factory"], identity["user_id"]
    )
    return _json({"settings": profiles.public_view(profile), "errors": {}})


async def patch_settings(request: web.Request) -> web.Response:
    identity = await _authorized_user(request)
    try:
        body = await request.json()
    except json.JSONDecodeError:
        return _json({"error": "Ожидался JSON"}, status=400)
    if not isinstance(body, dict):
        return _json({"error": "Ожидался объект JSON"}, status=400)

    section = str(body.get("section") or "").strip()
    profiles: ProfileService = request.app["profiles"]
    view, errors = await profiles.update_section(
        request.app["session_factory"],
        identity["user_id"],
        section,
        body,
    )
    status = 400 if errors else 200
    return _json({"settings": view, "errors": errors}, status=status)


def build_app(
    settings: Settings,
    session_factory,
    profiles: ProfileService,
    access: AccessService,
) -> web.Application:
    app = web.Application(client_max_size=32 * 1024)
    app["settings"] = settings
    app["session_factory"] = session_factory
    app["profiles"] = profiles
    app["access"] = access
    app.router.add_get("/health", health)
    app.router.add_get("/settings", settings_page)
    app.router.add_get("/", settings_page)
    app.router.add_get("/miniapp/settings", get_settings)
    app.router.add_patch("/miniapp/settings", patch_settings)
    return app


async def start_web(
    settings: Settings,
    session_factory,
    profiles: ProfileService,
    access: AccessService,
) -> web.AppRunner:
    app = build_app(settings, session_factory, profiles, access)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, settings.webapp_host, settings.webapp_port)
    await site.start()
    public = settings.miniapp_settings_url or "(WEBAPP_URL не задан, кнопка бота остаётся в чате)"
    logger.info(
        "Mini App listening on %s:%s, public settings %s",
        settings.webapp_host,
        settings.webapp_port,
        public,
    )
    return runner


async def stop_web(runner: web.AppRunner | None) -> None:
    if runner is not None:
        await runner.cleanup()
