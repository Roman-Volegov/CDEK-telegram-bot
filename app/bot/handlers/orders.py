from __future__ import annotations

import logging
import re
from pathlib import Path

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, FSInputFile, Message
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.bot.deps import (
    build_order_summary,
    effective_package,
    ensure_runtime,
    order_overrides_from_state,
)
from app.services.runtime import RuntimeConfig
from app.bot.keyboards.common import (
    confirm_address_kb,
    confirm_order_kb,
    edit_order_kb,
    main_menu,
    pvz_kb,
    tariffs_kb,
)
from app.bot.menu import MENU_TEXTS
from app.bot.states import OrderStates
from app.config import Settings
from app.db.models import Order
from app.db.numerator import next_order_number
from app.services.cdek import TariffOption
from app.services.profile import ProfileService

logger = logging.getLogger(__name__)
router = Router(name="order")

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


def _tariff_to_pvz(data: dict) -> bool:
    chosen = data.get("chosen_tariff") or {}
    mode = int(chosen.get("delivery_mode") or 0)
    return mode in (2, 4)


def _can_return_to_confirm(data: dict) -> bool:
    return bool(
        data.get("from_edit_menu")
        and data.get("recipient_name")
        and data.get("recipient_phone")
        and data.get("item_cost") is not None
        and data.get("chosen_tariff")
        and data.get("to_city_code")
    )


async def _return_to_confirm_message(
    message: Message,
    state: FSMContext,
    cfg: RuntimeConfig,
) -> None:
    data = await state.get_data()
    await state.update_data(from_edit_menu=False)
    await state.set_state(OrderStates.confirm_order)
    await message.answer(
        build_order_summary(cfg, data, float(data.get("item_cost") or 0)),
        reply_markup=confirm_order_kb(),
    )


async def _return_to_confirm_callback(
    callback: CallbackQuery,
    state: FSMContext,
    cfg: RuntimeConfig,
) -> None:
    data = await state.get_data()
    await state.update_data(from_edit_menu=False)
    await state.set_state(OrderStates.confirm_order)
    await callback.message.edit_text(
        build_order_summary(cfg, data, float(data.get("item_cost") or 0)),
        reply_markup=confirm_order_kb(),
    )


async def _show_tariffs_for_edit(
    callback: CallbackQuery,
    state: FSMContext,
    *,
    profiles: ProfileService,
    session_factory: async_sessionmaker[AsyncSession],
    prompt: str,
) -> None:
    data = await state.get_data()
    ready = await ensure_runtime(
        callback,
        state=state,
        profiles=profiles,
        session_factory=session_factory,
        overrides=order_overrides_from_state(data),
    )
    if ready is None:
        return
    cfg, cdek, _ = ready
    city_code = data.get("to_city_code")
    if not city_code:
        await callback.message.edit_text("Сначала укажите адрес доставки.")
        return
    weight, length, width, height = effective_package(cfg, data)
    tariffs = await cdek.calculate_tariffs(
        to_city_code=int(city_code),
        weight=weight,
        length=length,
        width=width,
        height=height,
    )
    if not tariffs:
        await callback.message.edit_text("Тарифы не найдены для текущего адреса/габаритов.")
        return
    serialized = [
        {
            "tariff_code": t.tariff_code,
            "tariff_name": t.tariff_name,
            "delivery_mode": t.delivery_mode,
            "delivery_sum": t.delivery_sum,
            "period_min": t.period_min,
            "period_max": t.period_max,
        }
        for t in tariffs[:10]
    ]
    await state.update_data(tariffs=serialized, from_edit_menu=True)
    await state.set_state(OrderStates.choose_tariff)
    lines = [prompt] + [f"• {t.label()}" for t in tariffs[:10]]
    await callback.message.edit_text(
        "\n".join(lines),
        reply_markup=tariffs_kb(tariffs, prefix="otariff"),
    )


def order_is_local_editable(order: Order) -> bool:
    return not order.cdek_uuid


async def load_saved_order_for_edit(
    callback: CallbackQuery,
    state: FSMContext,
    order: Order,
    *,
    profiles: ProfileService,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    ready = await ensure_runtime(
        callback,
        state=state,
        profiles=profiles,
        session_factory=session_factory,
    )
    if ready is None:
        return
    cfg, _, dadata = ready
    clean = await dadata.clean_address(order.to_address)
    lat = float(clean.geo_lat) if clean.geo_lat else None
    lon = float(clean.geo_lon) if clean.geo_lon else None

    await state.clear()
    await state.update_data(
        editing_order_id=order.id,
        editing_order_number=order.our_number,
        from_edit_menu=False,
        raw_address=order.to_address,
        clean_address=clean.display,
        city=clean.city_name,
        region=clean.region,
        postal_code=clean.postal_code,
        street=clean.street,
        house=clean.house,
        geo_lat=lat,
        geo_lon=lon,
        to_city_code=order.to_city_code,
        delivery_point=order.delivery_point,
        delivery_point_address=order.delivery_point,
        recipient_name=order.recipient_name,
        recipient_phone=order.recipient_phone,
        item_cost=float(order.item_cost),
        chosen_tariff={
            "tariff_code": int(order.tariff_code),
            "tariff_name": order.tariff_name or f"Тариф {order.tariff_code}",
            "delivery_mode": 2 if order.delivery_point else 1,
            "delivery_sum": float(order.delivery_sum or 0),
            "period_min": 0,
            "period_max": 0,
        },
        override_item_name=cfg.default_item_name,
    )
    await state.set_state(OrderStates.confirm_order)
    data = await state.get_data()
    await callback.message.edit_text(
        build_order_summary(cfg, data, float(order.item_cost)),
        reply_markup=confirm_order_kb(),
    )


@router.message(Command("order"))
@router.message(F.text == "🚚 Создать заказ")
async def order_start(
    message: Message,
    state: FSMContext,
    profiles: ProfileService,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    await state.clear()
    ready = await ensure_runtime(
        message, state=state, profiles=profiles, session_factory=session_factory
    )
    if ready is None:
        return
    await state.set_state(OrderStates.waiting_address)
    await message.answer(
        "Создание заказа СДЭК.\nОтправьте адрес доставки в любом формате.",
        reply_markup=main_menu(),
    )


@router.message(OrderStates.waiting_address, F.text, ~F.text.in_(MENU_TEXTS))
@router.message(OrderStates.edit_address, F.text, ~F.text.in_(MENU_TEXTS))
async def order_address(
    message: Message,
    state: FSMContext,
    profiles: ProfileService,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    ready = await ensure_runtime(
        message, state=state, profiles=profiles, session_factory=session_factory
    )
    if ready is None:
        return
    _, _, dadata = ready
    raw = (message.text or "").strip()
    wait = await message.answer("🔍 Распознаю адрес…")
    try:
        clean = await dadata.clean_address(raw)
        lat = float(clean.geo_lat) if clean.geo_lat else None
        lon = float(clean.geo_lon) if clean.geo_lon else None
        await state.update_data(
            raw_address=raw,
            clean_address=clean.display,
            city=clean.city_name,
            region=clean.region,
            postal_code=clean.postal_code,
            street=clean.street,
            house=clean.house,
            geo_lat=lat,
            geo_lon=lon,
            delivery_point=None,
            delivery_point_address=None,
        )
        await state.set_state(OrderStates.confirm_address)
        await wait.edit_text(
            f"Распознал адрес:\n📍 <b>{clean.display}</b>\n\nВсё верно?",
            reply_markup=confirm_address_kb(),
        )
    except Exception as exc:
        logger.exception("address clean failed")
        await wait.edit_text(f"Не удалось распознать адрес: {exc}")


@router.callback_query(OrderStates.confirm_address, F.data == "addr:retry")
async def order_address_retry(callback: CallbackQuery, state: FSMContext) -> None:
    data = await state.get_data()
    if data.get("from_edit_menu"):
        await state.set_state(OrderStates.edit_address)
    else:
        await state.set_state(OrderStates.waiting_address)
    await callback.message.edit_text("Пришлите адрес ещё раз.")
    await callback.answer()


@router.callback_query(OrderStates.confirm_address, F.data == "addr:ok")
async def order_address_ok(
    callback: CallbackQuery,
    state: FSMContext,
    profiles: ProfileService,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    data = await state.get_data()
    ready = await ensure_runtime(
        callback, state=state, profiles=profiles, session_factory=session_factory,
        overrides=order_overrides_from_state(data),
    )
    if ready is None:
        await callback.answer()
        return
    cfg, cdek, _ = ready
    city_name = data.get("city") or ""
    region = data.get("region")
    await callback.message.edit_text("Считаю тарифы…")
    try:
        cities = await cdek.find_city(city_name, region) or await cdek.find_city(city_name)
        if not cities:
            await callback.message.edit_text(
                f"Город «{city_name}» не найден в СДЭК. /order — начать заново."
            )
            await state.clear()
            await callback.answer()
            return

        city = cities[0]
        weight, length, width, height = effective_package(cfg, data)
        tariffs = await cdek.calculate_tariffs(
            to_city_code=city.code,
            weight=weight,
            length=length,
            width=width,
            height=height,
        )
        if not tariffs:
            await callback.message.edit_text("Тарифы не найдены.")
            await state.clear()
            await callback.answer()
            return

        serialized = [
            {
                "tariff_code": t.tariff_code,
                "tariff_name": t.tariff_name,
                "delivery_mode": t.delivery_mode,
                "delivery_sum": t.delivery_sum,
                "period_min": t.period_min,
                "period_max": t.period_max,
            }
            for t in tariffs[:10]
        ]
        await state.update_data(to_city_code=city.code, tariffs=serialized)
        await state.set_state(OrderStates.choose_tariff)
        lines = ["Выберите тариф:"] + [f"• {t.label()}" for t in tariffs[:10]]
        await callback.message.edit_text(
            "\n".join(lines),
            reply_markup=tariffs_kb(tariffs, prefix="otariff"),
        )
    except Exception as exc:
        logger.exception("tariffs failed")
        await callback.message.edit_text(f"Ошибка: {exc}")
        await state.clear()
    await callback.answer()


@router.callback_query(OrderStates.choose_tariff, F.data == "otariff:cancel")
async def order_tariff_cancel(callback: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    await callback.message.edit_text("Создание заказа отменено.")
    await callback.answer()


@router.callback_query(OrderStates.choose_tariff, F.data.startswith("otariff:"))
async def order_tariff_chosen(
    callback: CallbackQuery,
    state: FSMContext,
    profiles: ProfileService,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    idx_raw = (callback.data or "").split(":")[-1]
    if not idx_raw.isdigit():
        await callback.answer()
        return
    idx = int(idx_raw)
    data = await state.get_data()
    tariffs = data.get("tariffs") or []
    if idx < 0 or idx >= len(tariffs):
        await callback.answer("Тариф недоступен", show_alert=True)
        return

    chosen = tariffs[idx]
    await state.update_data(chosen_tariff=chosen)
    tariff = TariffOption(**chosen)
    if not tariff.to_pvz:
        await state.update_data(delivery_point=None, delivery_point_address=None)
        data = await state.get_data()
        if _can_return_to_confirm(data):
            ready = await ensure_runtime(
                callback,
                state=state,
                profiles=profiles,
                session_factory=session_factory,
                overrides=order_overrides_from_state(data),
            )
            if ready is None:
                await callback.answer()
                return
            cfg, _, _ = ready
            await _return_to_confirm_callback(callback, state, cfg)
            await callback.answer()
            return
        await state.set_state(OrderStates.waiting_name)
        await callback.message.edit_text(
            f"Тариф: <b>{tariff.tariff_name}</b>\nВведите ФИО получателя:"
        )
        await callback.answer()
        return

    await callback.message.edit_text(
        f"Тариф: <b>{tariff.tariff_name}</b>\n🔍 Ищу ПВЗ СДЭК по адресу…"
    )
    ready = await ensure_runtime(
        callback, state=state, profiles=profiles, session_factory=session_factory,
        overrides=order_overrides_from_state(data),
    )
    if ready is None:
        await callback.answer()
        return
    _, cdek, _ = ready
    try:
        points, kind = await cdek.find_pvz_for_address(
            city_code=int(data["to_city_code"]),
            address=data.get("clean_address") or "",
            street=data.get("street"),
            house=data.get("house"),
            postal_code=data.get("postal_code"),
            latitude=data.get("geo_lat"),
            longitude=data.get("geo_lon"),
            limit=8,
        )
    except Exception as exc:
        logger.exception("pvz search failed")
        await state.set_state(OrderStates.waiting_pvz)
        await callback.message.edit_text(
            f"Не удалось подобрать ПВЗ автоматически: {exc}\n"
            "Введите код ПВЗ вручную:"
        )
        await callback.answer()
        return

    if not points:
        await state.set_state(OrderStates.waiting_pvz)
        await callback.message.edit_text(
            "ПВЗ не найдены. Введите код ПВЗ вручную:"
        )
        await callback.answer()
        return

    serialized = [
        {
            "code": p.code,
            "name": p.name,
            "address": p.address,
            "address_full": p.address_full,
            "distance_km": p.distance_km,
            "work_time": p.work_time,
            "match_kind": p.match_kind,
        }
        for p in points
    ]
    await state.update_data(pvz_options=serialized)
    await state.set_state(OrderStates.choose_pvz)
    if kind == "exact":
        header = f"📍 Нашёл ПВЗ по адресу:\n<code>{data.get('clean_address')}</code>"
    else:
        header = f"📍 Точного ПВЗ нет. Ближайшие к <code>{data.get('clean_address')}</code>:"
    lines = [f"Тариф: <b>{tariff.tariff_name}</b>", header, ""]
    for p in points:
        dist = f" (~{p.distance_km:.1f} км)" if p.distance_km is not None else ""
        work = f"\n  🕒 {p.work_time}" if p.work_time else ""
        lines.append(f"• <b>{p.code}</b> — {p.address}{dist}{work}")
    await callback.message.edit_text("\n".join(lines), reply_markup=pvz_kb(points, prefix="pvz"))
    await callback.answer()


@router.callback_query(OrderStates.choose_pvz, F.data == "pvz:cancel")
async def order_pvz_cancel(callback: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    await callback.message.edit_text("Создание заказа отменено.")
    await callback.answer()


@router.callback_query(OrderStates.choose_pvz, F.data == "pvz:manual")
async def order_pvz_manual(callback: CallbackQuery, state: FSMContext) -> None:
    await state.set_state(OrderStates.waiting_pvz)
    await callback.message.edit_text("Введите код ПВЗ вручную:")
    await callback.answer()


@router.callback_query(OrderStates.choose_pvz, F.data.startswith("pvz:"))
async def order_pvz_chosen(
    callback: CallbackQuery,
    state: FSMContext,
    profiles: ProfileService,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    idx_raw = (callback.data or "").split(":")[-1]
    if not idx_raw.isdigit():
        await callback.answer()
        return
    idx = int(idx_raw)
    data = await state.get_data()
    options = data.get("pvz_options") or []
    if idx < 0 or idx >= len(options):
        await callback.answer("ПВЗ недоступен", show_alert=True)
        return
    point = options[idx]
    await state.update_data(
        delivery_point=point["code"],
        delivery_point_address=point.get("address") or point.get("address_full"),
    )
    data = await state.get_data()
    if _can_return_to_confirm(data):
        ready = await ensure_runtime(
            callback,
            state=state,
            profiles=profiles,
            session_factory=session_factory,
            overrides=order_overrides_from_state(data),
        )
        if ready is None:
            await callback.answer()
            return
        cfg, _, _ = ready
        await _return_to_confirm_callback(callback, state, cfg)
        await callback.answer()
        return
    await state.set_state(OrderStates.waiting_name)
    await callback.message.edit_text(
        f"ПВЗ: <b>{point['code']}</b>\n{point.get('address') or ''}\n\n"
        "Введите ФИО получателя:"
    )
    await callback.answer()


@router.message(OrderStates.waiting_pvz, F.text, ~F.text.in_(MENU_TEXTS))
@router.message(OrderStates.edit_delivery_pvz, F.text, ~F.text.in_(MENU_TEXTS))
async def order_pvz(
    message: Message,
    state: FSMContext,
    profiles: ProfileService,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    code = (message.text or "").strip().upper()
    if len(code) < 3:
        await message.answer("Код ПВЗ слишком короткий.")
        return
    await state.update_data(delivery_point=code, delivery_point_address=None)
    data = await state.get_data()
    if _can_return_to_confirm(data):
        ready = await ensure_runtime(
            message,
            state=state,
            profiles=profiles,
            session_factory=session_factory,
            overrides=order_overrides_from_state(data),
        )
        if ready is None:
            return
        cfg, _, _ = ready
        await _return_to_confirm_message(message, state, cfg)
        return
    await state.set_state(OrderStates.waiting_name)
    await message.answer("Введите ФИО получателя:")


@router.message(OrderStates.waiting_name, F.text, ~F.text.in_(MENU_TEXTS))
async def order_name(message: Message, state: FSMContext) -> None:
    name = (message.text or "").strip()
    if len(name) < 3:
        await message.answer("Укажите полное ФИО.")
        return
    await state.update_data(recipient_name=name)
    await state.set_state(OrderStates.waiting_phone)
    await message.answer("Введите телефон получателя (+79001234567):")


@router.message(OrderStates.waiting_phone, F.text, ~F.text.in_(MENU_TEXTS))
async def order_phone(message: Message, state: FSMContext) -> None:
    phone = _normalize_phone(message.text or "")
    if not PHONE_RE.match(phone):
        await message.answer("Некорректный телефон. Пример: +79001234567")
        return
    await state.update_data(recipient_phone=phone)
    await state.set_state(OrderStates.waiting_item_cost)
    await message.answer(
        "Укажите стоимость товара для декларации (₽).\nНаложенный платёж будет 0."
    )


@router.message(OrderStates.waiting_item_cost, F.text, ~F.text.in_(MENU_TEXTS))
async def order_item_cost(
    message: Message,
    state: FSMContext,
    profiles: ProfileService,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    raw = (message.text or "").replace(",", ".").strip()
    try:
        cost = float(raw)
        if cost < 0:
            raise ValueError
    except ValueError:
        await message.answer("Введите число, например 1500")
        return

    await state.update_data(item_cost=cost)
    data = await state.get_data()
    ready = await ensure_runtime(
        message, state=state, profiles=profiles, session_factory=session_factory,
        overrides=order_overrides_from_state(data),
    )
    if ready is None:
        return
    cfg, _, _ = ready
    await state.set_state(OrderStates.confirm_order)
    await message.answer(build_order_summary(cfg, data, cost), reply_markup=confirm_order_kb())


async def _show_confirm(callback: CallbackQuery, state: FSMContext, cfg) -> None:
    data = await state.get_data()
    cost = float(data.get("item_cost") or 0)
    await state.set_state(OrderStates.confirm_order)
    await callback.message.edit_text(
        build_order_summary(cfg, data, cost),
        reply_markup=confirm_order_kb(),
    )


@router.callback_query(OrderStates.confirm_order, F.data == "order:cancel")
async def order_cancel(callback: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    await callback.message.edit_text("Отменено.")
    await callback.answer()


@router.callback_query(OrderStates.confirm_order, F.data == "order:edit")
async def order_edit_menu(callback: CallbackQuery, state: FSMContext) -> None:
    data = await state.get_data()
    await state.set_state(OrderStates.edit_menu)
    await callback.message.edit_text(
        "Что изменить для <b>этого</b> заказа?",
        reply_markup=edit_order_kb(show_delivery_pvz=_tariff_to_pvz(data)),
    )
    await callback.answer()


@router.callback_query(OrderStates.edit_menu, F.data == "edit:back")
async def order_edit_back(
    callback: CallbackQuery,
    state: FSMContext,
    profiles: ProfileService,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    data = await state.get_data()
    ready = await ensure_runtime(
        callback, state=state, profiles=profiles, session_factory=session_factory,
        overrides=order_overrides_from_state(data),
    )
    if ready is None:
        await callback.answer()
        return
    cfg, _, _ = ready
    await _show_confirm(callback, state, cfg)
    await callback.answer()


@router.callback_query(OrderStates.edit_menu, F.data == "edit:address")
async def order_edit_address(callback: CallbackQuery, state: FSMContext) -> None:
    await state.update_data(from_edit_menu=True)
    await state.set_state(OrderStates.edit_address)
    await callback.message.edit_text(
        "Введите новый адрес доставки в любом формате.\n"
        "После подтверждения адреса нужно будет заново выбрать тариф."
    )
    await callback.answer()


@router.callback_query(OrderStates.edit_menu, F.data == "edit:tariff")
async def order_edit_tariff(
    callback: CallbackQuery,
    state: FSMContext,
    profiles: ProfileService,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    try:
        await _show_tariffs_for_edit(
            callback,
            state,
            profiles=profiles,
            session_factory=session_factory,
            prompt="Выберите новый тариф:",
        )
    except Exception as exc:
        logger.exception("edit tariff failed")
        await callback.message.edit_text(f"Не удалось пересчитать тарифы: {exc}")
    await callback.answer()


@router.callback_query(OrderStates.edit_menu, F.data == "edit:delivery_pvz")
async def order_edit_delivery_pvz(
    callback: CallbackQuery,
    state: FSMContext,
    profiles: ProfileService,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    data = await state.get_data()
    if not _tariff_to_pvz(data):
        await callback.answer("Для выбранного тарифа ПВЗ получения не нужен", show_alert=True)
        return
    await state.update_data(from_edit_menu=True)
    await callback.message.edit_text("🔍 Ищу ПВЗ СДЭК по адресу…")
    ready = await ensure_runtime(
        callback,
        state=state,
        profiles=profiles,
        session_factory=session_factory,
        overrides=order_overrides_from_state(data),
    )
    if ready is None:
        await callback.answer()
        return
    _, cdek, _ = ready
    try:
        points, kind = await cdek.find_pvz_for_address(
            city_code=int(data["to_city_code"]),
            address=data.get("clean_address") or "",
            street=data.get("street"),
            house=data.get("house"),
            postal_code=data.get("postal_code"),
            latitude=data.get("geo_lat"),
            longitude=data.get("geo_lon"),
            limit=8,
        )
    except Exception as exc:
        logger.exception("edit pvz search failed")
        await state.set_state(OrderStates.edit_delivery_pvz)
        await callback.message.edit_text(
            f"Не удалось подобрать ПВЗ: {exc}\nВведите код ПВЗ вручную:"
        )
        await callback.answer()
        return

    if not points:
        await state.set_state(OrderStates.edit_delivery_pvz)
        await callback.message.edit_text("ПВЗ не найдены. Введите код ПВЗ вручную:")
        await callback.answer()
        return

    serialized = [
        {
            "code": p.code,
            "name": p.name,
            "address": p.address,
            "address_full": p.address_full,
            "distance_km": p.distance_km,
            "work_time": p.work_time,
            "match_kind": p.match_kind,
        }
        for p in points
    ]
    await state.update_data(pvz_options=serialized)
    await state.set_state(OrderStates.choose_pvz)
    header = (
        "📍 Нашёл ПВЗ по адресу:"
        if kind == "exact"
        else "📍 Точного ПВЗ нет. Ближайшие:"
    )
    lines = [header, ""]
    for p in points:
        dist = f" (~{p.distance_km:.1f} км)" if p.distance_km is not None else ""
        lines.append(f"• <b>{p.code}</b> — {p.address}{dist}")
    await callback.message.edit_text("\n".join(lines), reply_markup=pvz_kb(points, prefix="pvz"))
    await callback.answer()


@router.callback_query(OrderStates.edit_menu, F.data == "edit:recipient")
async def order_edit_recipient(callback: CallbackQuery, state: FSMContext) -> None:
    data = await state.get_data()
    current = data.get("recipient_name") or "—"
    await state.set_state(OrderStates.edit_recipient_name)
    await callback.message.edit_text(
        f"Текущий получатель: <b>{current}</b>\n\nВведите новое ФИО получателя:"
    )
    await callback.answer()


@router.message(OrderStates.edit_recipient_name, F.text, ~F.text.in_(MENU_TEXTS))
async def order_edit_recipient_name(message: Message, state: FSMContext) -> None:
    name = (message.text or "").strip()
    if len(name) < 3:
        await message.answer("Укажите полное ФИО.")
        return
    await state.update_data(recipient_name=name)
    await state.set_state(OrderStates.edit_recipient_phone)
    await message.answer("Телефон получателя (+79001234567):")


@router.message(OrderStates.edit_recipient_phone, F.text, ~F.text.in_(MENU_TEXTS))
async def order_edit_recipient_phone(
    message: Message,
    state: FSMContext,
    profiles: ProfileService,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    phone = _normalize_phone(message.text or "")
    if not PHONE_RE.match(phone):
        await message.answer("Некорректный телефон. Пример: +79001234567")
        return
    await state.update_data(recipient_phone=phone)
    data = await state.get_data()
    ready = await ensure_runtime(
        message, state=state, profiles=profiles, session_factory=session_factory,
        overrides=order_overrides_from_state(data),
    )
    if ready is None:
        return
    cfg, _, _ = ready
    await _return_to_confirm_message(message, state, cfg)


@router.callback_query(OrderStates.edit_menu, F.data == "edit:item_cost")
async def order_edit_item_cost(callback: CallbackQuery, state: FSMContext) -> None:
    data = await state.get_data()
    current = data.get("item_cost")
    await state.set_state(OrderStates.edit_item_cost)
    await callback.message.edit_text(
        f"Текущая стоимость: <b>{float(current or 0):.0f} ₽</b>\n\n"
        "Введите новую стоимость товара (₽). НП останется 0."
    )
    await callback.answer()


@router.message(OrderStates.edit_item_cost, F.text, ~F.text.in_(MENU_TEXTS))
async def order_edit_item_cost_value(
    message: Message,
    state: FSMContext,
    profiles: ProfileService,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    raw = (message.text or "").replace(",", ".").strip()
    try:
        cost = float(raw)
        if cost < 0:
            raise ValueError
    except ValueError:
        await message.answer("Введите число, например 1500")
        return
    await state.update_data(item_cost=cost)
    data = await state.get_data()
    ready = await ensure_runtime(
        message, state=state, profiles=profiles, session_factory=session_factory,
        overrides=order_overrides_from_state(data),
    )
    if ready is None:
        return
    cfg, _, _ = ready
    await _return_to_confirm_message(message, state, cfg)


@router.callback_query(OrderStates.edit_menu, F.data == "edit:item_name")
async def order_edit_item_name(
    callback: CallbackQuery,
    state: FSMContext,
    profiles: ProfileService,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    data = await state.get_data()
    ready = await ensure_runtime(
        callback, state=state, profiles=profiles, session_factory=session_factory,
        overrides=order_overrides_from_state(data),
    )
    if ready is None:
        await callback.answer()
        return
    cfg, _, _ = ready
    current = data.get("override_item_name") or cfg.default_item_name
    await state.set_state(OrderStates.edit_item_name)
    await callback.message.edit_text(
        f"Текущее название: <b>{current}</b>\n\nВведите новое название товара в накладной:"
    )
    await callback.answer()


@router.message(OrderStates.edit_item_name, F.text, ~F.text.in_(MENU_TEXTS))
async def order_edit_item_name_value(
    message: Message,
    state: FSMContext,
    profiles: ProfileService,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    value = (message.text or "").strip()
    if len(value) < 2:
        await message.answer("Укажите название товара.")
        return
    await state.update_data(override_item_name=value)
    data = await state.get_data()
    ready = await ensure_runtime(
        message, state=state, profiles=profiles, session_factory=session_factory,
        overrides=order_overrides_from_state(data),
    )
    if ready is None:
        return
    cfg, _, _ = ready
    await _return_to_confirm_message(message, state, cfg)


@router.callback_query(OrderStates.edit_menu, F.data == "edit:dims")
async def order_edit_dims(callback: CallbackQuery, state: FSMContext) -> None:
    await state.set_state(OrderStates.edit_dimensions)
    await callback.message.edit_text(
        "Введите габариты: <b>вес_г длина ширина высота</b>\n"
        "Пример: <code>100 10 10 5</code>\n\n"
        "После смены габаритов можно заново выбрать тариф в меню редактирования."
    )
    await callback.answer()


@router.message(OrderStates.edit_dimensions, F.text, ~F.text.in_(MENU_TEXTS))
async def order_edit_dims_value(
    message: Message,
    state: FSMContext,
    profiles: ProfileService,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    parts = (message.text or "").replace("×", " ").replace("x", " ").replace("х", " ").split()
    if len(parts) != 4:
        await message.answer("Нужно 4 числа: вес длина ширина высота. Пример: 100 10 10 5")
        return
    try:
        weight, length, width, height = (int(float(p)) for p in parts)
        if min(weight, length, width, height) <= 0:
            raise ValueError
    except ValueError:
        await message.answer("Некорректные значения.")
        return
    await state.update_data(
        override_weight_g=weight,
        override_length_cm=length,
        override_width_cm=width,
        override_height_cm=height,
    )
    data = await state.get_data()
    ready = await ensure_runtime(
        message, state=state, profiles=profiles, session_factory=session_factory,
        overrides=order_overrides_from_state(data),
    )
    if ready is None:
        return
    cfg, _, _ = ready
    await _return_to_confirm_message(message, state, cfg)


@router.callback_query(OrderStates.edit_menu, F.data == "edit:sender")
async def order_edit_sender(callback: CallbackQuery, state: FSMContext) -> None:
    await state.set_state(OrderStates.edit_sender_name)
    await callback.message.edit_text("Введите ФИО отправителя для этого заказа:")
    await callback.answer()


@router.message(OrderStates.edit_sender_name, F.text, ~F.text.in_(MENU_TEXTS))
async def order_edit_sender_name(message: Message, state: FSMContext) -> None:
    name = (message.text or "").strip()
    if len(name) < 3:
        await message.answer("Укажите полное ФИО.")
        return
    await state.update_data(override_sender_name=name)
    await state.set_state(OrderStates.edit_sender_phone)
    await message.answer("Телефон отправителя (+79001234567):")


@router.message(OrderStates.edit_sender_phone, F.text, ~F.text.in_(MENU_TEXTS))
async def order_edit_sender_phone(
    message: Message,
    state: FSMContext,
    profiles: ProfileService,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    phone = _normalize_phone(message.text or "")
    if not PHONE_RE.match(phone):
        await message.answer("Некорректный телефон.")
        return
    await state.update_data(override_sender_phone=phone)
    data = await state.get_data()
    ready = await ensure_runtime(
        message, state=state, profiles=profiles, session_factory=session_factory,
        overrides=order_overrides_from_state(data),
    )
    if ready is None:
        return
    cfg, _, _ = ready
    await _return_to_confirm_message(message, state, cfg)


@router.callback_query(OrderStates.edit_menu, F.data == "edit:shipment")
async def order_edit_shipment(callback: CallbackQuery, state: FSMContext) -> None:
    await state.set_state(OrderStates.edit_shipment_point)
    await callback.message.edit_text(
        "Введите код ПВЗ <b>отправки</b> для этого заказа (например PRM17):"
    )
    await callback.answer()


@router.message(OrderStates.edit_shipment_point, F.text, ~F.text.in_(MENU_TEXTS))
async def order_edit_shipment_value(
    message: Message,
    state: FSMContext,
    profiles: ProfileService,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    code = (message.text or "").strip().upper()
    if len(code) < 3:
        await message.answer("Код ПВЗ слишком короткий.")
        return
    await state.update_data(override_shipment_point=code)
    data = await state.get_data()
    ready = await ensure_runtime(
        message, state=state, profiles=profiles, session_factory=session_factory,
        overrides=order_overrides_from_state(data),
    )
    if ready is None:
        return
    cfg, _, _ = ready
    await _return_to_confirm_message(message, state, cfg)


@router.callback_query(OrderStates.confirm_order, F.data == "order:create")
async def order_create(
    callback: CallbackQuery,
    state: FSMContext,
    profiles: ProfileService,
    session_factory: async_sessionmaker[AsyncSession],
    settings: Settings,
) -> None:
    data = await state.get_data()
    ready = await ensure_runtime(
        callback, state=state, profiles=profiles, session_factory=session_factory,
        overrides=order_overrides_from_state(data),
    )
    if ready is None:
        await callback.answer()
        return
    cfg, cdek, _ = ready

    await callback.message.edit_text("⏳ Создаю заказ в СДЭК и готовлю PDF…")
    await callback.answer()

    tariff = data["chosen_tariff"]
    our_number: str | None = None
    created_uuid: str | None = None
    weight, length, width, height = effective_package(cfg, data)
    editing_order_id = data.get("editing_order_id")

    try:
        async with session_factory() as session:
            order: Order | None = None
            if editing_order_id:
                result = await session.execute(
                    select(Order).where(
                        Order.id == int(editing_order_id),
                        Order.telegram_user_id == callback.from_user.id,
                    )
                )
                order = result.scalar_one_or_none()
                if order is None or order.cdek_uuid:
                    raise RuntimeError("Этот заказ уже отправлен в работу и недоступен для редактирования.")
                our_number = order.our_number
            else:
                our_number = await next_order_number(session)
                order = Order(
                    our_number=our_number,
                    telegram_user_id=callback.from_user.id,
                )
                session.add(order)

            order.status = "pending"
            order.tariff_code = int(tariff["tariff_code"])
            order.tariff_name = tariff.get("tariff_name")
            order.to_address = data.get("clean_address") or ""
            order.to_city_code = int(data["to_city_code"])
            order.delivery_point = data.get("delivery_point")
            order.recipient_name = data["recipient_name"]
            order.recipient_phone = data["recipient_phone"]
            order.item_cost = float(data["item_cost"])
            order.delivery_sum = float(tariff.get("delivery_sum") or 0)
            order.error_message = None
            order.cdek_uuid = None
            order.cdek_number = None
            order.waybill_path = None
            order.barcode_path = None
            await session.commit()

        payload = cdek.build_order_payload(
            our_number=our_number,
            tariff_code=int(tariff["tariff_code"]),
            to_city_code=int(data["to_city_code"]),
            to_address=data.get("clean_address") or data.get("raw_address") or "",
            recipient_name=data["recipient_name"],
            recipient_phone=data["recipient_phone"],
            item_cost=float(data["item_cost"]),
            delivery_point=data.get("delivery_point"),
            weight=weight,
            length=length,
            width=width,
            height=height,
        )
        # shipment override already in cfg via RuntimeConfig
        created = await cdek.create_order(payload)
        created_uuid = created.uuid
        entity = await cdek.wait_order_ready(created.uuid)
        cdek_number = str(entity.get("cdek_number") or "") or None

        pdf_dir = Path(settings.pdf_storage_path)
        waybill_path = pdf_dir / f"nakladnaya_{our_number}.pdf"
        barcode_path = pdf_dir / f"shtrihkody_{our_number}.pdf"
        await cdek.download_waybill_pdf(created.uuid, waybill_path)
        await cdek.download_barcode_pdf(created.uuid, barcode_path)

        async with session_factory() as session:
            result = await session.execute(select(Order).where(Order.our_number == our_number))
            db_order = result.scalar_one()
            db_order.cdek_uuid = created.uuid
            db_order.cdek_number = cdek_number
            db_order.status = "completed"
            db_order.waybill_path = str(waybill_path)
            db_order.barcode_path = str(barcode_path)
            await session.commit()

        await callback.message.edit_text(
            f"✅ Заказ <b>{our_number}</b> создан\n"
            f"Трек СДЭК: <code>{cdek_number or 'ожидается'}</code>\n"
            f"UUID: <code>{created.uuid}</code>\n\nОтправляю PDF…"
        )
        await callback.message.answer_document(
            FSInputFile(waybill_path, filename=waybill_path.name),
            caption=f"Накладная {our_number}",
        )
        await callback.message.answer_document(
            FSInputFile(barcode_path, filename=barcode_path.name),
            caption=f"Штрихкоды {our_number}",
            reply_markup=main_menu(),
        )
        await state.clear()
    except Exception as exc:
        logger.exception("order create failed")
        order_exists = bool(created_uuid)
        if our_number:
            try:
                async with session_factory() as session:
                    result = await session.execute(
                        select(Order).where(Order.our_number == our_number)
                    )
                    db_order = result.scalar_one_or_none()
                    if db_order:
                        db_order.cdek_uuid = created_uuid
                        db_order.error_message = str(exc)
                        if order_exists:
                            try:
                                entity = await cdek.get_order(created_uuid)
                                db_order.cdek_number = (
                                    str(entity.get("cdek_number") or "") or None
                                )
                            except Exception:
                                pass
                            db_order.status = "created_no_pdf"
                        else:
                            db_order.status = "failed"
                        await session.commit()
            except Exception:
                logger.exception("failed to persist error order")

        if order_exists and our_number:
            await callback.message.edit_text(
                f"⚠️ Заказ <b>{our_number}</b> создан в СДЭК, но PDF не готовы:\n"
                f"<code>{exc}</code>\n\n"
                f"Позже отправьте номер <code>{our_number}</code> "
                f"или команду <code>/pdf {our_number}</code> — "
                f"бот докачает PDF из СДЭК."
            )
        else:
            await callback.message.edit_text(f"❌ Не удалось создать заказ: {exc}")
        await state.clear()
