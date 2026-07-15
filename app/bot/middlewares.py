from collections.abc import Awaitable, Callable
from typing import Any

from aiogram import BaseMiddleware
from aiogram.types import TelegramObject

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.config import Settings
from app.services.cdek import CdekClient
from app.services.dadata import DaDataClient


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
