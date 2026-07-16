from collections.abc import Awaitable, Callable
from typing import Any

from aiogram import BaseMiddleware
from aiogram.types import CallbackQuery, Message, TelegramObject, Update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.config import Settings
from app.services.access import AccessService
from app.services.profile import ProfileService


def _extract_user_event(event: TelegramObject) -> tuple[Message | CallbackQuery | None, Any]:
    if isinstance(event, Update):
        if event.message:
            return event.message, event.message.from_user
        if event.callback_query:
            return event.callback_query, event.callback_query.from_user
        return None, None
    if isinstance(event, Message):
        return event, event.from_user
    if isinstance(event, CallbackQuery):
        return event, event.from_user
    return None, None


def _is_access_flow(event: Message | CallbackQuery | None) -> bool:
    """События, которые можно обрабатывать без approved-статуса."""
    if event is None:
        return False
    if isinstance(event, CallbackQuery):
        data = event.data or ""
        return data.startswith("access:")
    text = (event.text or "").strip()
    if not text:
        return False
    if text.startswith("/start") or text.startswith("/id") or text.startswith("/request_access"):
        return True
    if text in {"🔑 Запросить доступ", "ℹ️ Помощь", "/help"}:
        return True
    return False


class AccessControlMiddleware(BaseMiddleware):
    def __init__(
        self,
        settings: Settings,
        session_factory: async_sessionmaker[AsyncSession],
        access: AccessService,
    ) -> None:
        self.settings = settings
        self.session_factory = session_factory
        self.access = access

    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        tg_event, user = _extract_user_event(event)
        if user is None:
            return await handler(event, data)

        decision = await self.access.evaluate(
            self.session_factory,
            user.id,
            user.username,
            getattr(user, "full_name", None),
        )
        data["access_decision"] = decision
        data["access"] = self.access

        if decision.allowed:
            return await handler(event, data)

        # Админские callback'и и поток запроса доступа
        if _is_access_flow(tg_event):
            return await handler(event, data)

        # Остальное блокируем короткой подсказкой
        text = (
            "⛔ Доступ к боту ещё не разрешён.\n"
            "Нажмите /start и запросите разрешение у администратора."
        )
        if isinstance(tg_event, CallbackQuery):
            await tg_event.answer("Нет доступа", show_alert=True)
            if tg_event.message:
                await tg_event.message.answer(text)
        elif isinstance(tg_event, Message):
            await tg_event.answer(text)
        return None


class ServicesMiddleware(BaseMiddleware):
    def __init__(
        self,
        settings: Settings,
        session_factory: async_sessionmaker[AsyncSession],
        profiles: ProfileService,
        access: AccessService,
    ) -> None:
        self.settings = settings
        self.session_factory = session_factory
        self.profiles = profiles
        self.access = access

    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        data["settings"] = self.settings
        data["session_factory"] = self.session_factory
        data["profiles"] = self.profiles
        data["access"] = self.access
        return await handler(event, data)
