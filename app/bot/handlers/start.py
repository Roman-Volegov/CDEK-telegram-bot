from aiogram import F, Router
from aiogram.filters import Command, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.types import Message
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.bot.handlers.setup import start_setup
from app.bot.keyboards.common import main_menu, request_access_kb
from app.services.access import (
    STATUS_PENDING,
    STATUS_REJECTED,
    AccessDecision,
    AccessService,
    format_cooldown,
)
from app.services.profile import ProfileService

router = Router(name="start")

HELP_TEXT = (
    "🤖 <b>Бот расчёта и заказов СДЭК</b>\n\n"
    "• Сначала новый пользователь запрашивает доступ у администратора\n"
    "• <b>Настройки</b> — мастер секретов и параметров отправки\n"
    "• <b>Рассчитать</b> — стоимость доставки по адресу\n"
    "• <b>Создать заказ</b> — оформление + PDF накладной и штрихкодов\n"
    "• <b>Мои заказы</b> — последние заказы и повторная выгрузка PDF\n\n"
    "Перед работой пройдите настройку (/setup).\n"
    "Секреты хранятся зашифрованно для каждого пользователя."
)


@router.message(CommandStart())
async def cmd_start(
    message: Message,
    state: FSMContext,
    profiles: ProfileService,
    session_factory: async_sessionmaker[AsyncSession],
    access: AccessService,
    access_decision: AccessDecision | None = None,
) -> None:
    await state.clear()
    user = message.from_user
    if user is None:
        return

    decision = access_decision or await access.evaluate(
        session_factory, user.id, user.username
    )

    if not decision.allowed:
        if decision.status == STATUS_PENDING:
            await message.answer(
                "⏳ Заявка на доступ уже отправлена.\nОжидайте решения администратора.",
                reply_markup=request_access_kb(),
            )
            return
        if decision.status == STATUS_REJECTED and not decision.can_request:
            await message.answer(
                "⛔ Вам запрещено пользоваться ботом.\n"
                f"Повторный запрос можно отправить {format_cooldown(decision.cooldown_until)}.",
                reply_markup=request_access_kb(),
            )
            return
        await message.answer(
            "Привет! Для работы с ботом нужно разрешение администратора.\n"
            "Нажмите кнопку ниже — администратору придёт уведомление.",
            reply_markup=request_access_kb(),
        )
        return

    profile = await profiles.get_or_none(session_factory, user.id)
    if not profiles.is_ready(profile):
        await message.answer(
            "Доступ подтверждён. Для работы нужно один раз настроить бота под ваш аккаунт СДЭК.",
            reply_markup=main_menu(),
        )
        await start_setup(message, state)
        return
    assert profile is not None
    prefix = "Снова здравствуйте!"
    if decision.is_admin:
        prefix = "Снова здравствуйте, администратор!"
    await message.answer(
        f"{prefix}\n\n" + profiles.summary_html(profile),
        reply_markup=main_menu(),
    )


@router.message(Command("help"))
@router.message(F.text == "ℹ️ Помощь")
async def cmd_help(
    message: Message,
    state: FSMContext,
    access_decision: AccessDecision | None = None,
) -> None:
    await state.clear()
    kb = main_menu() if (access_decision and access_decision.allowed) else request_access_kb()
    await message.answer(HELP_TEXT, reply_markup=kb)


@router.message(Command("id"))
async def cmd_id(message: Message) -> None:
    user = message.from_user
    await message.answer(
        f"Ваш Telegram ID: <code>{user.id if user else '—'}</code>\n"
        f"Username: @{user.username if user and user.username else '—'}"
    )


@router.message(Command("cancel"))
async def cmd_cancel(
    message: Message,
    state: FSMContext,
    access_decision: AccessDecision | None = None,
) -> None:
    await state.clear()
    kb = main_menu() if (access_decision and access_decision.allowed) else request_access_kb()
    await message.answer("Отменено.", reply_markup=kb)

