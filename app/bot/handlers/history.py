from __future__ import annotations

import logging
import re
from pathlib import Path

from aiogram import F, Router
from aiogram.filters import Command, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, FSInputFile, Message
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.bot.handlers.orders import load_saved_order_for_edit, order_is_local_editable
from app.bot.keyboards.common import history_order_kb, history_page_kb, main_menu
from app.config import Settings
from app.db.models import Order
from app.services.cdek import CdekClient
from app.services.profile import ProfileService, load_runtime_for_user

logger = logging.getLogger(__name__)
router = Router(name="history")

PAGE_SIZE = 3
OUR_NUMBER_RE = re.compile(r"^\d{4}-\d{6}$")


def _local_status_label(order: Order) -> str:
    mapping = {
        "pending": "черновик / ожидает отправки",
        "completed": "создан в СДЭК",
        "created_no_pdf": "создан в СДЭК (PDF не готовы)",
        "failed": "ошибка создания",
        "cancelled": "отменён",
        "created": "создан",
    }
    return mapping.get(order.status, order.status)


def _order_total_cost(order: Order) -> float:
    """Полная стоимость: товар + доставка."""
    item = float(order.item_cost or 0)
    delivery = float(order.delivery_sum or 0)
    return item + delivery


def _format_order_line(idx: int, order: Order, status_label: str) -> str:
    cdek_number = order.cdek_number or "—"
    total = _order_total_cost(order)
    return (
        f"<b>{idx}. {order.our_number}</b>\n"
        f"Статус: {status_label}\n"
        f"Адрес: {order.to_address}\n"
        f"Получатель: {order.recipient_name}, {order.recipient_phone}\n"
        f"Стоимость: {total:.0f} ₽\n"
        f"Номер СДЭК: <code>{cdek_number}</code>"
    )


def _format_order_detail(order: Order, status_label: str) -> str:
    cdek_number = order.cdek_number or "—"
    total = _order_total_cost(order)
    delivery = float(order.delivery_sum or 0)
    item = float(order.item_cost or 0)
    return (
        f"<b>Заказ {order.our_number}</b>\n"
        f"Статус: {status_label}\n"
        f"Адрес: {order.to_address}\n"
        f"Получатель: {order.recipient_name}, {order.recipient_phone}\n"
        f"Тариф: {order.tariff_name or order.tariff_code}\n"
        f"Товар: {item:.0f} ₽ · Доставка: {delivery:.0f} ₽\n"
        f"Полная стоимость: {total:.0f} ₽\n"
        f"Номер СДЭК: <code>{cdek_number}</code>"
    )


def _has_pdfs(order: Order) -> bool:
    return bool(
        (order.waybill_path and Path(order.waybill_path).exists())
        or (order.barcode_path and Path(order.barcode_path).exists())
    )


def _can_fetch_pdf(order: Order) -> bool:
    return bool(order.cdek_uuid) and order.status != "cancelled"


async def _get_cdek_client(
    session_factory: async_sessionmaker[AsyncSession],
    profiles: ProfileService,
    telegram_user_id: int,
) -> CdekClient | None:
    cfg = await load_runtime_for_user(session_factory, profiles, telegram_user_id)
    if cfg is None:
        return None
    return CdekClient(cfg)


async def _resolve_status(
    order: Order,
    cdek: CdekClient | None,
    *,
    session_factory: async_sessionmaker[AsyncSession] | None = None,
) -> str:
    if order.status == "cancelled":
        return _local_status_label(order)
    if not order.cdek_uuid or cdek is None:
        return _local_status_label(order)
    try:
        entity = await cdek.get_order(order.cdek_uuid)
        label = CdekClient.latest_status_label(entity)
        cdek_number = str(entity.get("cdek_number") or "") or None
        if session_factory is not None and cdek_number and cdek_number != order.cdek_number:
            async with session_factory() as session:
                db_order = await session.get(Order, order.id)
                if db_order:
                    db_order.cdek_number = cdek_number
                    await session.commit()
            order.cdek_number = cdek_number
        return label or _local_status_label(order)
    except Exception:
        logger.exception("failed to fetch CDEK status for %s", order.our_number)
        return f"{_local_status_label(order)} (не удалось обновить из СДЭК)"


async def _ensure_pdf_files(
    order: Order,
    *,
    cdek: CdekClient | None,
    session_factory: async_sessionmaker[AsyncSession],
    pdf_storage_path: str,
) -> tuple[Path | None, Path | None, str | None]:
    """
    Возвращает локальные PDF; при отсутствии докачивает из СДЭК по cdek_uuid.
    (waybill, barcode, error)
    """
    waybill = Path(order.waybill_path) if order.waybill_path else None
    barcode = Path(order.barcode_path) if order.barcode_path else None
    local_waybill = waybill if waybill and waybill.exists() else None
    local_barcode = barcode if barcode and barcode.exists() else None
    if local_waybill or local_barcode:
        return local_waybill, local_barcode, None

    if not order.cdek_uuid:
        return None, None, "PDF нет: заказ ещё не создан в СДЭК."
    if cdek is None:
        return None, None, "Чтобы выгрузить PDF из СДЭК, пройдите /setup."

    pdf_dir = Path(pdf_storage_path)
    waybill_path = pdf_dir / f"nakladnaya_{order.our_number}.pdf"
    barcode_path = pdf_dir / f"shtrihkody_{order.our_number}.pdf"
    try:
        entity = await cdek.wait_order_ready(order.cdek_uuid, attempts=20, delay=1.2)
        cdek_number = str(entity.get("cdek_number") or "") or None
        await cdek.download_waybill_pdf(order.cdek_uuid, waybill_path)
        await cdek.download_barcode_pdf(order.cdek_uuid, barcode_path)
    except Exception as exc:
        logger.exception("PDF fetch failed for %s", order.our_number)
        return None, None, f"Не удалось выгрузить PDF: {exc}"

    async with session_factory() as session:
        db_order = await session.get(Order, order.id)
        if db_order:
            db_order.waybill_path = str(waybill_path)
            db_order.barcode_path = str(barcode_path)
            if cdek_number:
                db_order.cdek_number = cdek_number
            if db_order.status in {"created_no_pdf", "pending", "created"}:
                db_order.status = "completed"
            await session.commit()

    order.waybill_path = str(waybill_path)
    order.barcode_path = str(barcode_path)
    if cdek_number:
        order.cdek_number = cdek_number
    if order.status in {"created_no_pdf", "pending", "created"}:
        order.status = "completed"
    return waybill_path, barcode_path, None


async def _send_order_pdfs(
    message: Message,
    order: Order,
    *,
    cdek: CdekClient | None,
    session_factory: async_sessionmaker[AsyncSession],
    pdf_storage_path: str,
    with_menu: bool = True,
) -> bool:
    """Отправляет PDF (локальные или из СДЭК). True если хоть один файл ушёл."""
    wait: Message | None = None
    if not _has_pdfs(order) and order.cdek_uuid:
        wait = await message.answer("⏳ Выгружаю PDF из СДЭК…")

    waybill, barcode, error = await _ensure_pdf_files(
        order,
        cdek=cdek,
        session_factory=session_factory,
        pdf_storage_path=pdf_storage_path,
    )
    if error:
        if wait:
            await wait.edit_text(error)
            if with_menu:
                await message.answer("Главное меню:", reply_markup=main_menu())
        else:
            await message.answer(error, reply_markup=main_menu() if with_menu else None)
        return False

    sent = False
    if waybill:
        await message.answer_document(
            FSInputFile(waybill, filename=waybill.name),
            caption=f"Накладная {order.our_number}",
        )
        sent = True
    if barcode:
        await message.answer_document(
            FSInputFile(barcode, filename=barcode.name),
            caption=f"Штрихкоды {order.our_number}",
            reply_markup=main_menu() if with_menu else None,
        )
        sent = True
    elif sent and with_menu:
        await message.answer("Готово.", reply_markup=main_menu())

    if wait:
        if sent:
            await wait.edit_text(f"✅ PDF для <b>{order.our_number}</b> готовы.")
        else:
            await wait.edit_text("PDF для этого заказа ещё нет.")
    return sent


async def _load_page(
    session_factory: async_sessionmaker[AsyncSession],
    telegram_user_id: int,
    page: int,
) -> tuple[list[Order], int]:
    async with session_factory() as session:
        total = int(
            await session.scalar(
                select(func.count())
                .select_from(Order)
                .where(Order.telegram_user_id == telegram_user_id)
            )
            or 0
        )
        if total == 0:
            return [], 0
        max_page = max(0, (total - 1) // PAGE_SIZE)
        page = max(0, min(page, max_page))
        result = await session.execute(
            select(Order)
            .where(Order.telegram_user_id == telegram_user_id)
            .order_by(Order.created_at.desc())
            .offset(page * PAGE_SIZE)
            .limit(PAGE_SIZE)
        )
        return list(result.scalars().all()), total


async def _build_page_text(
    orders: list[Order],
    total: int,
    page: int,
    *,
    cdek: CdekClient | None,
    session_factory: async_sessionmaker[AsyncSession],
) -> str:
    if not orders:
        return "Заказов пока нет."
    start = page * PAGE_SIZE + 1
    end = page * PAGE_SIZE + len(orders)
    lines = [
        f"<b>Мои заказы</b> · {start}–{end} из {total}",
        "",
    ]
    for idx, order in enumerate(orders, start=1):
        status = await _resolve_status(order, cdek, session_factory=session_factory)
        lines.append(_format_order_line(idx, order, status))
        lines.append("")
    return "\n".join(lines).rstrip()


async def _render_orders_page(
    target: Message | CallbackQuery,
    *,
    page: int,
    session_factory: async_sessionmaker[AsyncSession],
    profiles: ProfileService,
    answer_callback: bool = True,
) -> None:
    user = target.from_user
    if user is None:
        return
    orders, total = await _load_page(session_factory, user.id, page)
    if total == 0:
        text = "Заказов пока нет."
        if isinstance(target, CallbackQuery) and target.message:
            await target.message.edit_text(text)
            await target.message.answer("Главное меню:", reply_markup=main_menu())
            if answer_callback:
                await target.answer()
        else:
            assert isinstance(target, Message)
            await target.answer(text, reply_markup=main_menu())
        return

    max_page = max(0, (total - 1) // PAGE_SIZE)
    page = max(0, min(page, max_page))
    orders, total = await _load_page(session_factory, user.id, page)
    cdek = await _get_cdek_client(session_factory, profiles, user.id)
    text = await _build_page_text(
        orders, total, page, cdek=cdek, session_factory=session_factory
    )
    markup = history_page_kb(page, total, [o.id for o in orders], page_size=PAGE_SIZE)

    if isinstance(target, CallbackQuery) and target.message:
        await target.message.edit_text(text, reply_markup=markup)
        if answer_callback:
            await target.answer()
    else:
        assert isinstance(target, Message)
        await target.answer(text, reply_markup=markup)
        await target.answer("Выберите заказ или листайте список.", reply_markup=main_menu())


@router.message(Command("orders"))
@router.message(F.text == "📋 Мои заказы")
async def my_orders(
    message: Message,
    session_factory: async_sessionmaker[AsyncSession],
    profiles: ProfileService,
) -> None:
    await _render_orders_page(
        message,
        page=0,
        session_factory=session_factory,
        profiles=profiles,
    )


@router.callback_query(F.data.startswith("hist:page:"))
async def history_page(
    callback: CallbackQuery,
    session_factory: async_sessionmaker[AsyncSession],
    profiles: ProfileService,
) -> None:
    raw = (callback.data or "").split(":")[-1]
    page = int(raw) if raw.isdigit() else 0
    await _render_orders_page(
        callback,
        page=page,
        session_factory=session_factory,
        profiles=profiles,
    )


async def _get_user_order(
    session_factory: async_sessionmaker[AsyncSession],
    order_id: int,
    telegram_user_id: int,
) -> Order | None:
    async with session_factory() as session:
        result = await session.execute(
            select(Order).where(
                Order.id == order_id,
                Order.telegram_user_id == telegram_user_id,
            )
        )
        return result.scalar_one_or_none()


async def _get_user_order_by_number(
    session_factory: async_sessionmaker[AsyncSession],
    number: str,
    telegram_user_id: int,
) -> Order | None:
    async with session_factory() as session:
        result = await session.execute(
            select(Order).where(
                Order.our_number == number,
                Order.telegram_user_id == telegram_user_id,
            )
        )
        return result.scalar_one_or_none()


def _parse_view_callback(data: str | None) -> tuple[int | None, int]:
    parts = (data or "").split(":")
    # hist:view:{order_id}[:page]
    if len(parts) < 3 or not parts[2].isdigit():
        return None, 0
    order_id = int(parts[2])
    page = int(parts[3]) if len(parts) > 3 and parts[3].isdigit() else 0
    return order_id, page


@router.callback_query(F.data.startswith("hist:view:"))
async def history_view_order(
    callback: CallbackQuery,
    session_factory: async_sessionmaker[AsyncSession],
    profiles: ProfileService,
) -> None:
    order_id, page = _parse_view_callback(callback.data)
    if order_id is None:
        await callback.answer("Некорректный заказ", show_alert=True)
        return
    order = await _get_user_order(session_factory, order_id, callback.from_user.id)
    if not order:
        await callback.answer("Заказ не найден", show_alert=True)
        return
    cdek = await _get_cdek_client(session_factory, profiles, callback.from_user.id)
    status = await _resolve_status(order, cdek, session_factory=session_factory)
    await callback.message.edit_text(
        _format_order_detail(order, status),
        reply_markup=history_order_kb(
            order.id,
            editable=order_is_local_editable(order),
            has_pdfs=_has_pdfs(order),
            can_fetch_pdf=_can_fetch_pdf(order),
            page=page,
        ),
    )
    await callback.answer()


@router.callback_query(F.data.startswith("hist:pdf:"))
async def history_send_pdf(
    callback: CallbackQuery,
    session_factory: async_sessionmaker[AsyncSession],
    profiles: ProfileService,
    settings: Settings,
) -> None:
    order_id = int((callback.data or "").split(":")[-1])
    order = await _get_user_order(session_factory, order_id, callback.from_user.id)
    if not order:
        await callback.answer("Заказ не найден", show_alert=True)
        return
    await callback.answer()
    cdek = await _get_cdek_client(session_factory, profiles, callback.from_user.id)
    assert callback.message is not None
    sent = await _send_order_pdfs(
        callback.message,
        order,
        cdek=cdek,
        session_factory=session_factory,
        pdf_storage_path=settings.pdf_storage_path,
        with_menu=True,
    )
    if not sent and not order.cdek_uuid:
        await callback.message.answer("PDF для этого заказа ещё нет.", reply_markup=main_menu())


@router.callback_query(F.data.startswith("hist:edit:"))
async def history_edit_order(
    callback: CallbackQuery,
    state: FSMContext,
    session_factory: async_sessionmaker[AsyncSession],
    profiles: ProfileService,
) -> None:
    order_id = int((callback.data or "").split(":")[-1])
    order = await _get_user_order(session_factory, order_id, callback.from_user.id)
    if not order:
        await callback.answer("Заказ не найден", show_alert=True)
        return
    if not order_is_local_editable(order):
        await callback.answer(
            "Этот заказ уже в работе и недоступен для редактирования",
            show_alert=True,
        )
        return
    await load_saved_order_for_edit(
        callback,
        state,
        order,
        profiles=profiles,
        session_factory=session_factory,
    )
    await callback.answer()


@router.callback_query(F.data.startswith("hist:cancel:"))
async def history_cancel_order(
    callback: CallbackQuery,
    session_factory: async_sessionmaker[AsyncSession],
    profiles: ProfileService,
) -> None:
    order_id = int((callback.data or "").split(":")[-1])
    async with session_factory() as session:
        result = await session.execute(
            select(Order).where(
                Order.id == order_id,
                Order.telegram_user_id == callback.from_user.id,
            )
        )
        order = result.scalar_one_or_none()
        if not order:
            await callback.answer("Заказ не найден", show_alert=True)
            return
        if not order_is_local_editable(order):
            await callback.answer(
                "Этот заказ уже в работе и не может быть отменён",
                show_alert=True,
            )
            return
        order.status = "cancelled"
        await session.commit()
    order = await _get_user_order(session_factory, order_id, callback.from_user.id)
    assert order is not None
    await callback.message.edit_text(
        _format_order_detail(order, _local_status_label(order)),
        reply_markup=history_order_kb(
            order.id,
            editable=order_is_local_editable(order),
            has_pdfs=_has_pdfs(order),
            can_fetch_pdf=_can_fetch_pdf(order),
            page=0,
        ),
    )
    await callback.answer("Заказ отменён")


@router.callback_query(F.data.startswith("hist:delete:"))
async def history_delete_order(
    callback: CallbackQuery,
    session_factory: async_sessionmaker[AsyncSession],
    profiles: ProfileService,
) -> None:
    order_id = int((callback.data or "").split(":")[-1])
    async with session_factory() as session:
        result = await session.execute(
            select(Order).where(
                Order.id == order_id,
                Order.telegram_user_id == callback.from_user.id,
            )
        )
        order = result.scalar_one_or_none()
        if not order:
            await callback.answer("Заказ не найден", show_alert=True)
            return
        if not order_is_local_editable(order):
            await callback.answer(
                "Этот заказ уже в работе и не может быть удалён",
                show_alert=True,
            )
            return
        waybill_path = order.waybill_path
        barcode_path = order.barcode_path
        await session.execute(delete(Order).where(Order.id == order.id))
        await session.commit()
    for path in (waybill_path, barcode_path):
        if path and Path(path).exists():
            try:
                Path(path).unlink()
            except OSError:
                pass
    await callback.answer("Заказ удалён")
    await _render_orders_page(
        callback,
        page=0,
        session_factory=session_factory,
        profiles=profiles,
        answer_callback=False,
    )


@router.message(Command("pdf"))
async def resend_pdf(
    message: Message,
    session_factory: async_sessionmaker[AsyncSession],
    profiles: ProfileService,
    settings: Settings,
) -> None:
    parts = (message.text or "").split(maxsplit=1)
    if len(parts) < 2:
        await message.answer(
            "Использование: <code>/pdf 2026-000001</code>\n"
            "Или просто отправьте номер заказа."
        )
        return
    await _deliver_pdf_by_number(
        message,
        parts[1].strip(),
        session_factory=session_factory,
        profiles=profiles,
        settings=settings,
    )


@router.message(StateFilter(None), F.text.regexp(OUR_NUMBER_RE))
async def resend_pdf_by_number(
    message: Message,
    session_factory: async_sessionmaker[AsyncSession],
    profiles: ProfileService,
    settings: Settings,
) -> None:
    number = (message.text or "").strip()
    await _deliver_pdf_by_number(
        message,
        number,
        session_factory=session_factory,
        profiles=profiles,
        settings=settings,
    )


async def _deliver_pdf_by_number(
    message: Message,
    number: str,
    *,
    session_factory: async_sessionmaker[AsyncSession],
    profiles: ProfileService,
    settings: Settings,
) -> None:
    order = await _get_user_order_by_number(session_factory, number, message.from_user.id)
    if not order:
        await message.answer("Заказ не найден.", reply_markup=main_menu())
        return
    cdek = await _get_cdek_client(session_factory, profiles, message.from_user.id)
    await _send_order_pdfs(
        message,
        order,
        cdek=cdek,
        session_factory=session_factory,
        pdf_storage_path=settings.pdf_storage_path,
        with_menu=True,
    )
