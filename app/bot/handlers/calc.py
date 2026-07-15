from __future__ import annotations

import logging

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import Message

from app.bot.keyboards.common import main_menu
from app.bot.states import CalcStates
from app.config import Settings
from app.services.cdek import CdekClient
from app.services.dadata import DaDataClient

logger = logging.getLogger(__name__)
router = Router(name="calc")


@router.message(Command("calc"))
@router.message(F.text == "📦 Рассчитать")
async def calc_start(message: Message, state: FSMContext) -> None:
    await state.clear()
    await state.set_state(CalcStates.waiting_address)
    await message.answer(
        "Отправьте адрес доставки в любом формате.\n"
        "Пример: <code>Москва, Тверская 1</code>",
        reply_markup=main_menu(),
    )


@router.message(CalcStates.waiting_address, F.text)
async def calc_address(
    message: Message,
    state: FSMContext,
    dadata: DaDataClient,
    cdek: CdekClient,
    settings: Settings,
) -> None:
    raw = (message.text or "").strip()
    if not raw:
        await message.answer("Пришлите текстовый адрес.")
        return

    wait = await message.answer("🔍 Распознаю адрес и считаю тарифы…")
    try:
        clean = await dadata.clean_address(raw)
        city_name = clean.city_name
        if not city_name:
            await wait.edit_text(
                "Не удалось определить город. Уточните адрес.\n"
                f"Распознано: <code>{clean.display}</code>"
            )
            return

        cities = await cdek.find_city(city_name.replace("г ", "").replace("г. ", ""), clean.region)
        if not cities:
            # повтор без региона
            cities = await cdek.find_city(city_name)
        if not cities:
            await wait.edit_text(
                f"Город «{city_name}» не найден в справочнике СДЭК.\n"
                f"Адрес: <code>{clean.display}</code>"
            )
            return

        city = cities[0]
        tariffs = await cdek.calculate_tariffs(
            to_city_code=city.code,
            weight=settings.default_weight_g,
            length=settings.default_length_cm,
            width=settings.default_width_cm,
            height=settings.default_height_cm,
        )
        if not tariffs:
            await wait.edit_text("Тарифы не найдены для этого направления.")
            await state.clear()
            return

        lines = [
            f"📍 <b>{clean.display}</b>",
            f"Город СДЭК: {city.city} ({city.region}), код {city.code}",
            f"📦 {settings.default_weight_g} г, "
            f"{settings.default_length_cm}×{settings.default_width_cm}×{settings.default_height_cm} см",
            f"ПВЗ отгрузки: <code>{settings.cdek_shipment_point}</code>",
            "",
            "<b>Тарифы:</b>",
        ]
        for t in tariffs[:8]:
            lines.append(f"• {t.label()}")

        await wait.edit_text("\n".join(lines))
        await state.clear()
    except Exception as exc:
        logger.exception("calc failed")
        await wait.edit_text(f"Ошибка расчёта: {exc}")
        await state.clear()
