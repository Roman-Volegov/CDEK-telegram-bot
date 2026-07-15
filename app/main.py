import asyncio
import logging
from pathlib import Path

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.fsm.storage.redis import RedisStorage

from app.bot.handlers import setup_routers
from app.bot.middlewares import ServicesMiddleware
from app.config import get_settings
from app.db.session import get_session_factory, init_db
from app.services.cdek import CdekClient
from app.services.dadata import DaDataClient

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)


async def main() -> None:
    settings = get_settings()
    Path(settings.pdf_storage_path).mkdir(parents=True, exist_ok=True)

    await init_db()
    logger.info("Database ready")

    try:
        storage = RedisStorage.from_url(settings.redis_url)
        logger.info("FSM storage: Redis")
    except Exception:
        logger.warning("Redis недоступен, используем MemoryStorage")
        storage = MemoryStorage()

    bot = Bot(
        token=settings.bot_token,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML),
    )
    dp = Dispatcher(storage=storage)

    cdek = CdekClient(settings)
    dadata = DaDataClient(settings)
    session_factory = get_session_factory()
    dp.update.middleware(ServicesMiddleware(settings, cdek, dadata, session_factory))
    dp.include_router(setup_routers())

    logger.info(
        "Bot starting (CDEK %s, shipment_point=%s)",
        "TEST" if settings.cdek_test_mode else "PROD",
        settings.cdek_shipment_point,
    )
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
