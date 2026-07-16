from collections.abc import Awaitable, Callable
from typing import Any

from aiogram import BaseMiddleware
from aiogram.types import CallbackQuery, Message, TelegramObject, Update

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.config import Settings
from app.services.cdek import CdekClient
from app.services.dadata import DaDataClient


class AccessControlMiddleware(BaseMiddleware):
    """Пропускает только пользователей из ALLOWED_TELEGRAM_IDS (если список задан)."""

    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        user = data.get("event_from_user")
        user_id = user.id if user else None
        username = user.username if user else None

        if self.settings.is_user_allowed(user_id, username):
            return await handler(event, data)

        # Сообщаем ID — чтобы его можно было добавить в whitelist
        uname = f"@{username}" if username else "—"
        text = (
            "⛔ Бот доступен только авторизованным пользователям.\n"
            f"Ваш Telegram ID: <code>{user_id}</code>\n"
            f"Username: {uname}\n"
            "Передайте это администратору."
        )
        if isinstance(event, Update):
            if event.message:
                await event.message.answer(text)
            elif event.callback_query:
                await event.callback_query.answer("Нет доступа", show_alert=True)
                if event.callback_query.message:
                    await event.callback_query.message.answer(text)
        elif isinstance(event, Message):
            await event.answer(text)
        elif isinstance(event, CallbackQuery):
            await event.answer("Нет доступа", show_alert=True)
            if event.message:
                await event.message.answer(text)
        return None


class ServicesMiddleware(BaseMiddleware):
    def __init__(
        self,
        settings: Settings,
        cdek: CdekClient,
        dadata: DaDataClient,
        session_factory: async_sessionmaker[AsyncSession],
    ) -> None:
        self.settings = settings
        self.cdek = cdek
        self.dadata = dadata
        self.session_factory = session_factory

    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        data["settings"] = self.settings
        data["cdek"] = self.cdek
        data["dadata"] = self.dadata
        data["session_factory"] = self.session_factory
        return await handler(event, data)
