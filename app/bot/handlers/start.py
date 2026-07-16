from aiogram import F, Router
from aiogram.filters import Command, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.types import Message
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.bot.handlers.setup import start_setup
from app.bot.keyboards.common import main_menu
from app.services.profile import ProfileService

router = Router(name="start")

HELP_TEXT = (
    "🤖 <b>Бот расчёта и заказов СДЭК</b>\n\n"
    "• <b>Настройки</b> — мастер секретов и параметров отправки\n"
    "• <b>Рассчитать</b> — стоимость доставки по адресу\n"
    "• <b>Создать заказ</b> — оформление + PDF накладной и штрихкодов\n"
    "• <b>Мои заказы</b> — последние заказы и повторная выгрузка PDF\n\n"
    "Перед работой пройдите настройку (/setup).\n"
    "Секреты хранятся зашифрованно для каждого пользователя.\n"
    "Перед созданием заказа можно изменить габариты, отправителя и ПВЗ отправки."
)


@router.message(CommandStart())
async def cmd_start(
    message: Message,
    state: FSMContext,
    profiles: ProfileService,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    await state.clear()
    profile = await profiles.get_or_none(session_factory, message.from_user.id)
    if not profiles.is_ready(profile):
        await message.answer(
            "Привет! Для работы нужно один раз настроить бота под ваш аккаунт СДЭК.",
            reply_markup=main_menu(),
        )
        await start_setup(message, state)
        return
    assert profile is not None
    await message.answer(
        "Снова здравствуйте!\n\n" + profiles.summary_html(profile),
        reply_markup=main_menu(),
    )


@router.message(Command("help"))
@router.message(F.text == "ℹ️ Помощь")
async def cmd_help(message: Message, state: FSMContext) -> None:
    await state.clear()
    await message.answer(HELP_TEXT, reply_markup=main_menu())


@router.message(Command("id"))
async def cmd_id(message: Message) -> None:
    user = message.from_user
    await message.answer(
        f"Ваш Telegram ID: <code>{user.id if user else '—'}</code>\n"
        f"Username: @{user.username if user and user.username else '—'}"
    )


@router.message(Command("cancel"))
async def cmd_cancel(message: Message, state: FSMContext) -> None:
    await state.clear()
    await message.answer("Отменено.", reply_markup=main_menu())
