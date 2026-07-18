from __future__ import annotations

import asyncio
import logging

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.fsm.storage.redis import RedisStorage
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.bot.handlers import router
from app.bot.middlewares import AccessControlMiddleware, ServicesMiddleware
from app.config import get_settings
from app.db.schema import init_schema
from app.services.access import AccessService
from app.services.crypto import SecretBox
from app.services.profile import ProfileService


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
)
logger = logging.getLogger(__name__)


async def main() -> None:
    settings = get_settings()
    engine = create_async_engine(settings.database_url, pool_pre_ping=True)
    await init_schema(engine)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)

    secret_box = SecretBox(settings.encryption_key)
    profiles = ProfileService(secret_box)
    access = AccessService(settings)
    bootstrapped = await access.ensure_bootstrap_admins(session_factory)
    if bootstrapped:
        logger.info("Bootstrapped %s approved/admin access records", bootstrapped)

    if settings.redis_url:
        redis = Redis.from_url(settings.redis_url)
        storage = RedisStorage(redis=redis)
    else:
        storage = MemoryStorage()

    bot = Bot(
        token=settings.bot_token,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML),
    )
    dp = Dispatcher(storage=storage)
    dp.update.middleware(AccessControlMiddleware(settings, session_factory, access))
    dp.update.middleware(ServicesMiddleware(settings, session_factory, profiles, access))
    dp.include_router(router)

    if not settings.admin_user_ids and not settings.admin_usernames:
        logger.warning(
            "ADMIN_TELEGRAM_IDS / ALLOWED_TELEGRAM_IDS пусты — "
            "заявки на доступ некому отправлять"
        )

    logger.info("Bot starting (polling)")
    try:
        await dp.start_polling(bot)
    finally:
        await bot.session.close()
        await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
