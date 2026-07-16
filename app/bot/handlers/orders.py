from __future__ import annotations

import logging
import re
from pathlib import Path

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import FSInputFile, CallbackQuery, Message
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.bot.keyboards.common import (
    confirm_address_kb,
    confirm_order_kb,
    main_menu,
    pvz_kb,
    tariffs_kb,
)
from app.bot.menu import MENU_TEXTS
from app.bot.states import OrderStates
from app.config import Settings
from app.db.models import Order
from app.db.numerator import next_order_number
from app.services.cdek import CdekClient, TariffOption
from app.services.dadata import DaDataClient

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


@router.message(Command("order"))
@router.message(F.text == "🚚 Создать заказ")
async def order_start(message: Message, state: FSMContext) -> None:
    await state.clear()
    await state.set_state(OrderStates.waiting_address)
    await message.answer(
        "Создание заказа СДЭК.\n"
        "Отправьте адрес доставки в любом формате.",
        reply_markup=main_menu(),
    )


@router.message(OrderStates.waiting_address, F.text, ~F.text.in_(MENU_TEXTS))
async def order_address(
    message: Message,
    state: FSMContext,
    dadata: DaDataClient,
) -> None:
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
    await state.set_state(OrderStates.waiting_address)
    await callback.message.edit_text("Пришлите адрес ещё раз.")
    await callback.answer()


@router.callback_query(OrderStates.confirm_address, F.data == "addr:ok")
async def order_address_ok(
    callback: CallbackQuery,
    state: FSMContext,
    cdek: CdekClient,
    settings: Settings,
) -> None:
    data = await state.get_data()
    city_name = data.get("city") or ""
    region = data.get("region")
    await callback.message.edit_text("Считаю тарифы…")
    try:
        cities = await cdek.find_city(city_name, region)
        if not cities:
            cities = await cdek.find_city(city_name)
        if not cities:
            await callback.message.edit_text(
                f"Город «{city_name}» не найден в СДЭК. /order — начать заново."
            )
            await state.clear()
            await callback.answer()
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

        lines = ["Выберите тариф:"]
        for t in tariffs[:10]:
            lines.append(f"• {t.label()}")
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
    cdek: CdekClient,
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
    if tariff.to_pvz:
        await callback.message.edit_text(
            f"Тариф: <b>{tariff.tariff_name}</b>\n🔍 Ищу ПВЗ СДЭК по адресу…"
        )
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
                "Введите код ПВЗ вручную (например <code>SPB17</code>):"
            )
            await callback.answer()
            return

        if not points:
            await state.set_state(OrderStates.waiting_pvz)
            await callback.message.edit_text(
                "ПВЗ по этому адресу и рядом не найдены.\n"
                "Введите код ПВЗ вручную (например <code>SPB17</code>):"
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
            header = (
                f"Тариф: <b>{tariff.tariff_name}</b>\n"
                f"📍 Нашёл ПВЗ по адресу:\n<code>{data.get('clean_address')}</code>\n"
                "Выберите пункт:"
            )
        else:
            header = (
                f"Тариф: <b>{tariff.tariff_name}</b>\n"
                f"📍 Точного ПВЗ по адресу нет.\n"
                f"Ближайшие к <code>{data.get('clean_address')}</code>:"
            )
        lines = [header, ""]
        for p in points:
            dist = f" (~{p.distance_km:.1f} км)" if p.distance_km is not None else ""
            work = f"\n  🕒 {p.work_time}" if p.work_time else ""
            lines.append(f"• <b>{p.code}</b> — {p.address}{dist}{work}")

        await callback.message.edit_text(
            "\n".join(lines),
            reply_markup=pvz_kb(points, prefix="pvz"),
        )
    else:
        await state.set_state(OrderStates.waiting_name)
        await callback.message.edit_text(
            f"Тариф: <b>{tariff.tariff_name}</b>\nВведите ФИО получателя:"
        )
    await callback.answer()


@router.callback_query(OrderStates.choose_pvz, F.data == "pvz:cancel")
async def order_pvz_cancel(callback: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    await callback.message.edit_text("Создание заказа отменено.")
    await callback.answer()


@router.callback_query(OrderStates.choose_pvz, F.data == "pvz:manual")
async def order_pvz_manual(callback: CallbackQuery, state: FSMContext) -> None:
    await state.set_state(OrderStates.waiting_pvz)
    await callback.message.edit_text(
        "Введите код ПВЗ вручную (например <code>SPB17</code>):"
    )
    await callback.answer()


@router.callback_query(OrderStates.choose_pvz, F.data.startswith("pvz:"))
async def order_pvz_chosen(callback: CallbackQuery, state: FSMContext) -> None:
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
    await state.set_state(OrderStates.waiting_name)
    await callback.message.edit_text(
        f"ПВЗ: <b>{point['code']}</b>\n"
        f"{point.get('address') or ''}\n\n"
        "Введите ФИО получателя:"
    )
    await callback.answer()


@router.message(OrderStates.waiting_pvz, F.text, ~F.text.in_(MENU_TEXTS))
async def order_pvz(message: Message, state: FSMContext) -> None:
    code = (message.text or "").strip().upper()
    if len(code) < 3:
        await message.answer("Код ПВЗ слишком короткий. Пример: SPB17")
        return
    await state.update_data(delivery_point=code, delivery_point_address=None)
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
        "Укажите стоимость товара для декларации (₽).\n"
        "Наложенный платёж будет 0."
    )


@router.message(OrderStates.waiting_item_cost, F.text, ~F.text.in_(MENU_TEXTS))
async def order_item_cost(message: Message, state: FSMContext, settings: Settings) -> None:
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
    tariff = data["chosen_tariff"]
    pvz = data.get("delivery_point")
    pvz_addr = data.get("delivery_point_address")
    if pvz:
        dest = f"ПВЗ {pvz}" + (f" — {pvz_addr}" if pvz_addr else "")
    else:
        dest = data.get("clean_address")

    text = (
        "<b>Проверьте заказ</b>\n\n"
        f"Куда: {dest}\n"
        f"Тариф: {tariff['tariff_name']} — {tariff['delivery_sum']:.0f} ₽\n"
        f"Получатель: {data['recipient_name']}, {data['recipient_phone']}\n"
        f"Товар: {settings.default_item_name}, cost={cost:.0f} ₽, НП=0\n"
        f"Место: {settings.default_weight_g} г, "
        f"{settings.default_length_cm}×{settings.default_width_cm}×{settings.default_height_cm}\n"
        f"Отгрузка: ПВЗ {settings.cdek_shipment_point}\n"
        f"Номер будет вида <code>2026-000001</code>"
    )
    await state.set_state(OrderStates.confirm_order)
    await message.answer(text, reply_markup=confirm_order_kb())


@router.callback_query(OrderStates.confirm_order, F.data == "order:cancel")
async def order_cancel(callback: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    await callback.message.edit_text("Отменено.")
    await callback.answer()


@router.callback_query(OrderStates.confirm_order, F.data == "order:create")
async def order_create(
    callback: CallbackQuery,
    state: FSMContext,
    cdek: CdekClient,
    settings: Settings,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    data = await state.get_data()
    await callback.message.edit_text("⏳ Создаю заказ в СДЭК и готовлю PDF…")
    await callback.answer()

    tariff = data["chosen_tariff"]
    our_number: str | None = None
    created_uuid: str | None = None

    try:
        # 1) Сразу фиксируем номер в БД (не откатываем при ошибке СДЭК)
        async with session_factory() as session:
            our_number = await next_order_number(session)
            order = Order(
                our_number=our_number,
                telegram_user_id=callback.from_user.id,
                status="pending",
                tariff_code=int(tariff["tariff_code"]),
                tariff_name=tariff.get("tariff_name"),
                to_address=data.get("clean_address") or "",
                to_city_code=int(data["to_city_code"]),
                delivery_point=data.get("delivery_point"),
                recipient_name=data["recipient_name"],
                recipient_phone=data["recipient_phone"],
                item_cost=float(data["item_cost"]),
                delivery_sum=float(tariff.get("delivery_sum") or 0),
            )
            session.add(order)
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
        )
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
            f"UUID: <code>{created.uuid}</code>\n\n"
            "Отправляю PDF…"
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
        if our_number:
            try:
                async with session_factory() as session:
                    result = await session.execute(
                        select(Order).where(Order.our_number == our_number)
                    )
                    db_order = result.scalar_one_or_none()
                    if db_order:
                        db_order.status = "failed"
                        db_order.cdek_uuid = created_uuid
                        db_order.error_message = str(exc)
                        await session.commit()
            except Exception:
                logger.exception("failed to persist error order")

        await callback.message.edit_text(f"❌ Не удалось создать заказ: {exc}")
        await state.clear()
