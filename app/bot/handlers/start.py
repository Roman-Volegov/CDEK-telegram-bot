from aiogram import F, Router
from aiogram.filters import Command, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.types import Message

from app.bot.keyboards.common import main_menu

router = Router(name="start")

HELP_TEXT = (
    "🤖 <b>Бот расчёта и заказов СДЭК</b>\n\n"
    "• <b>Рассчитать</b> — стоимость доставки по адресу\n"
    "• <b>Создать заказ</b> — оформление + PDF накладной и штрихкодов\n"
    "• <b>Мои заказы</b> — последние заказы и повторная выгрузка PDF\n\n"
    "<b>Формат номера:</b> <code>2026-000001</code>\n"
    "Отгрузка всегда с вашего ПВЗ СДЭК.\n"
    "Наложенный платёж всегда 0 ₽.\n"
    "Стоимость товара в декларации спрашивается при создании заказа.\n\n"
    "Адрес можно писать в свободной форме:\n"
    "<i>спб невский 10</i>\n"
    "<i>г. Казань, ул. Баумана, 1</i>"
)


@router.message(CommandStart())
async def cmd_start(message: Message, state: FSMContext) -> None:
    await state.clear()
    await message.answer(
        "Привет! Я помогу рассчитать доставку СДЭК и оформить заказ.\n\n"
        "Выберите действие на клавиатуре или /help.",
        reply_markup=main_menu(),
    )


@router.message(Command("help"))
@router.message(F.text == "ℹ️ Помощь")
async def cmd_help(message: Message, state: FSMContext) -> None:
    await state.clear()
    await message.answer(HELP_TEXT, reply_markup=main_menu())


@router.message(Command("cancel"))
async def cmd_cancel(message: Message, state: FSMContext) -> None:
    await state.clear()
    await message.answer("Отменено.", reply_markup=main_menu())
