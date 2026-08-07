from __future__ import annotations

import logging
import re

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.bot.keyboards.common import main_menu, setup_confirm_kb, yes_no_kb
from app.bot.menu import MENU_TEXTS
from app.bot.states import SetupStates
from app.services.profile import ProfileDraft, ProfileService

logger = logging.getLogger(__name__)
router = Router(name="setup")

PHONE_RE = re.compile(r"^\+?\d{10,15}$")


def _normalize_phone(raw: str) -> str:
    digits = re.sub(r"[^\d+]", "", raw.strip())
    if digits.startswith("8") and len(digits) == 11:
        digits = "+7" + digits[1:]
    elif digits.startswith("7") and len(digits) == 11:
        digits = "+" + digits
    elif digits.isdigit() and len(digits) == 10:
        digits = "+7" + digits
    return digits


async def start_setup(message: Message, state: FSMContext, *, restart: bool = False) -> None:
    await state.clear()
    await state.set_state(SetupStates.cdek_client_id)
    await state.update_data(setup_draft={})
    text = (
        "🛠 <b>Мастер начальной настройки</b>\n\n"
        "Нужно один раз указать секреты СДЭК/DaData и параметры отправки.\n"
        "Секреты хранятся <b>зашифрованно</b> и привязаны к вашему Telegram.\n\n"
        "1/11. Для работы бота необходимо зарегистрировать личный кабинет на сайте "
        '<a href="https://www.cdek.ru">CDEK.ru</a>. '
        "API-ключи доступны в меню профиля «Интеграции».\n\n"
        "Отправьте <b>CDEK_CLIENT_ID</b>:"
    )
    if restart:
        text = "Начинаем настройку заново.\n\n" + text
    await message.answer(text, reply_markup=main_menu())


@router.message(Command("settings"))
@router.message(F.text == "⚙️ Настройки")
async def cmd_settings(
    message: Message,
    state: FSMContext,
    profiles: ProfileService,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    await state.clear()
    profile = await profiles.get_or_none(session_factory, message.from_user.id)
    if profiles.is_ready(profile):
        assert profile is not None
        await message.answer(
            profiles.summary_html(profile)
            + "\n\nЧтобы изменить — /setup",
            reply_markup=main_menu(),
        )
        return
    await start_setup(message, state)


@router.message(Command("setup"))
async def cmd_setup(message: Message, state: FSMContext) -> None:
    await start_setup(message, state, restart=True)


@router.message(SetupStates.cdek_client_id, F.text, ~F.text.in_(MENU_TEXTS))
async def setup_client_id(message: Message, state: FSMContext) -> None:
    value = (message.text or "").strip()
    if len(value) < 5:
        await message.answer("Слишком короткий CLIENT_ID. Отправьте ещё раз:")
        return
    data = await state.get_data()
    draft = data.get("setup_draft") or {}
    draft["cdek_client_id"] = value
    await state.update_data(setup_draft=draft)
    await state.set_state(SetupStates.cdek_client_secret)
    await message.answer("2/11. Отправьте <b>CDEK_CLIENT_SECRET</b>:")


@router.message(SetupStates.cdek_client_secret, F.text, ~F.text.in_(MENU_TEXTS))
async def setup_client_secret(message: Message, state: FSMContext) -> None:
    value = (message.text or "").strip()
    if len(value) < 5:
        await message.answer("Слишком короткий SECRET. Отправьте ещё раз:")
        return
    data = await state.get_data()
    draft = data.get("setup_draft") or {}
    draft["cdek_client_secret"] = value
    await state.update_data(setup_draft=draft)
    await state.set_state(SetupStates.cdek_test_mode)
    await message.answer(
        "3/11. Режим СДЭК:",
        reply_markup=yes_no_kb("setup_test"),
    )


@router.callback_query(SetupStates.cdek_test_mode, F.data.startswith("setup_test:"))
async def setup_test_mode(callback: CallbackQuery, state: FSMContext) -> None:
    mode = (callback.data or "").split(":")[-1]
    # yes = test, no = prod (кнопки: Prod=no, Тест=yes)
    test_mode = mode == "yes"
    data = await state.get_data()
    draft = data.get("setup_draft") or {}
    draft["cdek_test_mode"] = test_mode
    await state.update_data(setup_draft=draft)
    await state.set_state(SetupStates.shipment_point)
    await callback.message.edit_text(
        f"Режим: {'тест' if test_mode else 'prod'}\n\n"
        "4/11. Код <b>ПВЗ отгрузки</b> СДЭК (например <code>PRM17</code>):"
    )
    await callback.answer()


@router.message(SetupStates.shipment_point, F.text, ~F.text.in_(MENU_TEXTS))
async def setup_shipment(message: Message, state: FSMContext) -> None:
    value = (message.text or "").strip().upper()
    if len(value) < 3:
        await message.answer("Код ПВЗ слишком короткий. Пример: PRM17")
        return
    data = await state.get_data()
    draft = data.get("setup_draft") or {}
    draft["cdek_shipment_point"] = value
    await state.update_data(setup_draft=draft)
    await state.set_state(SetupStates.sender_name)
    await message.answer("5/11. ФИО отправителя (и seller):")


@router.message(SetupStates.sender_name, F.text, ~F.text.in_(MENU_TEXTS))
async def setup_sender_name(message: Message, state: FSMContext) -> None:
    value = (message.text or "").strip()
    if len(value) < 3:
        await message.answer("Укажите полное ФИО.")
        return
    data = await state.get_data()
    draft = data.get("setup_draft") or {}
    draft["sender_name"] = value
    await state.update_data(setup_draft=draft)
    await state.set_state(SetupStates.sender_phone)
    await message.answer("6/11. Телефон отправителя (+79001234567):")


@router.message(SetupStates.sender_phone, F.text, ~F.text.in_(MENU_TEXTS))
async def setup_sender_phone(message: Message, state: FSMContext) -> None:
    phone = _normalize_phone(message.text or "")
    if not PHONE_RE.match(phone):
        await message.answer("Некорректный телефон. Пример: +79001234567")
        return
    data = await state.get_data()
    draft = data.get("setup_draft") or {}
    draft["sender_phone"] = phone
    await state.update_data(setup_draft=draft)
    await state.set_state(SetupStates.dadata_api_key)
    await message.answer(
        "7/11. Для работы бота необходима бесплатная регистрация в сервисе "
        '<a href="https://dadata.ru">DaData.ru</a>.\n\n'
        "Отправьте <b>DADATA_API_KEY</b>:"
    )


@router.message(SetupStates.dadata_api_key, F.text, ~F.text.in_(MENU_TEXTS))
async def setup_dadata_key(message: Message, state: FSMContext) -> None:
    value = (message.text or "").strip()
    if len(value) < 8:
        await message.answer("Ключ слишком короткий. Отправьте ещё раз:")
        return
    data = await state.get_data()
    draft = data.get("setup_draft") or {}
    draft["dadata_api_key"] = value
    await state.update_data(setup_draft=draft)
    await state.set_state(SetupStates.dadata_secret_key)
    await message.answer("8/11. Отправьте <b>DADATA_SECRET_KEY</b>:")


@router.message(SetupStates.dadata_secret_key, F.text, ~F.text.in_(MENU_TEXTS))
async def setup_dadata_secret(message: Message, state: FSMContext) -> None:
    value = (message.text or "").strip()
    if len(value) < 8:
        await message.answer("Секрет слишком короткий. Отправьте ещё раз:")
        return
    data = await state.get_data()
    draft = data.get("setup_draft") or {}
    draft["dadata_secret_key"] = value
    await state.update_data(setup_draft=draft)
    await state.set_state(SetupStates.weight)
    await message.answer("9/11. Вес посылки по умолчанию в <b>граммах</b> (например 100):")


@router.message(SetupStates.weight, F.text, ~F.text.in_(MENU_TEXTS))
async def setup_weight(message: Message, state: FSMContext) -> None:
    raw = (message.text or "").strip().replace(",", ".")
    try:
        weight = int(float(raw))
        if weight <= 0:
            raise ValueError
    except ValueError:
        await message.answer("Введите целое число граммов, например 100")
        return
    data = await state.get_data()
    draft = data.get("setup_draft") or {}
    draft["weight_g"] = weight
    await state.update_data(setup_draft=draft)
    await state.set_state(SetupStates.dimensions)
    await message.answer(
        "10/11. Габариты в см: <b>длина ширина высота</b>\n"
        "Пример: <code>10 10 5</code>"
    )


@router.message(SetupStates.dimensions, F.text, ~F.text.in_(MENU_TEXTS))
async def setup_dimensions(message: Message, state: FSMContext) -> None:
    parts = (message.text or "").replace("×", " ").replace("x", " ").replace("х", " ").split()
    if len(parts) != 3:
        await message.answer("Нужно три числа: длина ширина высота. Пример: 10 10 5")
        return
    try:
        length, width, height = (int(float(p)) for p in parts)
        if min(length, width, height) <= 0:
            raise ValueError
    except ValueError:
        await message.answer("Некорректные размеры. Пример: 10 10 5")
        return
    data = await state.get_data()
    draft = data.get("setup_draft") or {}
    draft["length_cm"] = length
    draft["width_cm"] = width
    draft["height_cm"] = height
    await state.update_data(setup_draft=draft)
    await state.set_state(SetupStates.item_name)
    await message.answer(
        "11/11. Название товара в накладной (например <code>Бижутерия</code>):"
    )


@router.message(SetupStates.item_name, F.text, ~F.text.in_(MENU_TEXTS))
async def setup_item_name(message: Message, state: FSMContext) -> None:
    value = (message.text or "").strip()
    if len(value) < 2:
        await message.answer("Укажите название товара.")
        return
    data = await state.get_data()
    draft = data.get("setup_draft") or {}
    draft["item_name"] = value
    draft.setdefault("item_ware_key", "ITEM-1")
    draft.setdefault("cdek_order_type", 1)
    await state.update_data(setup_draft=draft)
    await state.set_state(SetupStates.confirm)

    mode = "тест" if draft.get("cdek_test_mode") else "prod"
    text = (
        "<b>Проверьте настройки</b>\n\n"
        f"СДЭК CLIENT_ID: <code>{draft.get('cdek_client_id')}</code> ({mode})\n"
        f"ПВЗ отгрузки: <code>{draft.get('cdek_shipment_point')}</code>\n"
        f"Отправитель: {draft.get('sender_name')}, {draft.get('sender_phone')}\n"
        f"DaData API: <code>{draft.get('dadata_api_key', '')[:6]}…</code>\n"
        f"Товар: {draft.get('item_name')}\n"
        f"Габариты: {draft.get('weight_g')} г, "
        f"{draft.get('length_cm')}×{draft.get('width_cm')}×{draft.get('height_cm')} см\n\n"
        "Секреты будут сохранены в зашифрованном виде."
    )
    await message.answer(text, reply_markup=setup_confirm_kb())


@router.callback_query(SetupStates.confirm, F.data == "setup:restart")
async def setup_restart(callback: CallbackQuery, state: FSMContext) -> None:
    await callback.answer()
    await start_setup(callback.message, state, restart=True)


@router.callback_query(SetupStates.confirm, F.data == "setup:save")
async def setup_save(
    callback: CallbackQuery,
    state: FSMContext,
    profiles: ProfileService,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    data = await state.get_data()
    raw = data.get("setup_draft") or {}
    draft = ProfileDraft(
        cdek_client_id=raw.get("cdek_client_id", ""),
        cdek_client_secret=raw.get("cdek_client_secret", ""),
        dadata_api_key=raw.get("dadata_api_key", ""),
        dadata_secret_key=raw.get("dadata_secret_key", ""),
        cdek_test_mode=bool(raw.get("cdek_test_mode")),
        cdek_shipment_point=raw.get("cdek_shipment_point", ""),
        cdek_order_type=int(raw.get("cdek_order_type") or 1),
        sender_name=raw.get("sender_name", ""),
        sender_phone=raw.get("sender_phone", ""),
        weight_g=int(raw.get("weight_g") or 100),
        length_cm=int(raw.get("length_cm") or 10),
        width_cm=int(raw.get("width_cm") or 10),
        height_cm=int(raw.get("height_cm") or 5),
        item_name=raw.get("item_name") or "Бижутерия",
        item_ware_key=raw.get("item_ware_key") or "ITEM-1",
    )
    try:
        profile = await profiles.save_draft(session_factory, callback.from_user.id, draft)
        await state.clear()
        await callback.message.edit_text(
            "✅ Настройки сохранены.\n\n" + profiles.summary_html(profile)
        )
        await callback.message.answer(
            "Можно пользоваться ботом: расчёт и создание заказа.",
            reply_markup=main_menu(),
        )
    except Exception as exc:
        logger.exception("setup save failed")
        await callback.message.answer(f"Ошибка сохранения: {exc}")
    await callback.answer()
