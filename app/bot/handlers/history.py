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

from app.bot.admin_users import list_users_for_admin
from app.bot.handlers.orders import load_saved_order_for_edit, order_is_local_editable
from app.bot.keyboards.common import (
    admin_user_pick_kb,
    history_delete_confirm_kb,
    history_order_kb,
    history_page_kb,
    history_purge_confirm_kb,
    main_menu,
)
from app.bot.states import HistoryStates
from app.config import Settings
from app.db.models import CdekPayment, Order
from app.services.access import AccessDecision, AccessService
from app.services.cdek import CdekClient
from app.services.profile import ProfileService, load_runtime_for_user

logger = logging.getLogger(__name__)
router = Router(name="history")

PAGE_SIZE = 3
OUR_NUMBER_RE = re.compile(r"^\d{4}-\d{6}$")
ORDERS_OWNER_KEY = "orders_owner_id"


async def _resolve_owner_id(
    state: FSMContext,
    viewer_id: int,
    *,
    access: AccessService,
    username: str | None,
) -> int:
    if not access.is_admin(viewer_id, username):
        return viewer_id
    data = await state.get_data()
    raw = data.get(ORDERS_OWNER_KEY)
    if raw is None:
        return viewer_id
    return int(raw)


async def _set_orders_owner(state: FSMContext, owner_id: int) -> None:
    await state.set_state(HistoryStates.browsing)
    await state.update_data(**{ORDERS_OWNER_KEY: int(owner_id)})


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


def _format_cost(total_sum: float | None, *, in_cdek: bool) -> str:
    if total_sum is None:
        return "ожидается из СДЭК" if in_cdek else "—"
    return f"{total_sum:.0f} ₽"


def _is_paid(order: Order) -> bool:
    return order.payment_id is not None


def _payment_label(order: Order) -> str:
    return "оплачен" if _is_paid(order) else "не оплачен"


def _format_order_line(
    idx: int,
    order: Order,
    status_label: str,
    *,
    cdek_total_sum: float | None = None,
) -> str:
    cdek_number = order.cdek_number or "—"
    in_cdek = bool(order.cdek_uuid)
    track_url = CdekClient.tracking_url(order.cdek_number)
    lines = [
        f"<b>{idx}. {order.our_number}</b>",
        f"Статус: {status_label}",
        f"Оплата: {_payment_label(order)}",
        f"Адрес: {order.to_address}",
        f"Получатель: {order.recipient_name}, {order.recipient_phone}",
        f"Стоимость: {_format_cost(cdek_total_sum, in_cdek=in_cdek)}",
        f"Номер СДЭК: <code>{cdek_number}</code>",
    ]
    if track_url:
        lines.append(f'<a href="{track_url}">Открыть на сайте СДЭК</a>')
    return "\n".join(lines)


def _format_order_detail(
    order: Order,
    status_label: str,
    *,
    cdek_total_sum: float | None = None,
) -> str:
    cdek_number = order.cdek_number or "—"
    item = float(order.item_cost or 0)
    in_cdek = bool(order.cdek_uuid)
    paid_extra = f" (запись №{order.payment_id})" if order.payment_id else ""
    track_url = CdekClient.tracking_url(order.cdek_number)
    lines = [
        f"<b>Заказ {order.our_number}</b>",
        f"Статус: {status_label}",
        f"Оплата: {_payment_label(order)}{paid_extra}",
        f"Адрес: {order.to_address}",
        f"Получатель: {order.recipient_name}, {order.recipient_phone}",
        f"Тариф: {order.tariff_name or order.tariff_code}",
        f"Стоимость товара: {item:.0f} ₽",
        f"Стоимость заказа (СДЭК): {_format_cost(cdek_total_sum, in_cdek=in_cdek)}",
        f"Номер СДЭК: <code>{cdek_number}</code>",
    ]
    if order.comment:
        lines.insert(-1, f"Комментарий: {order.comment}")
    if track_url:
        lines.append(f'<a href="{track_url}">Открыть на сайте СДЭК</a>')
    return "\n".join(lines)


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


async def _resolve_live_info(
    order: Order,
    cdek: CdekClient | None,
    *,
    session_factory: async_sessionmaker[AsyncSession] | None = None,
) -> tuple[str, float | None]:
    """Возвращает (статус, стоимость из СДЭК)."""
    if order.status == "cancelled":
        return _local_status_label(order), None
    if not order.cdek_uuid or cdek is None:
        return _local_status_label(order), None
    try:
        entity = await cdek.get_order(order.cdek_uuid)
        label = CdekClient.latest_status_label(entity)
        cdek_number = str(entity.get("cdek_number") or "") or None
        total_sum = CdekClient.order_total_sum(entity)
        if session_factory is not None and (
            (cdek_number and cdek_number != order.cdek_number)
            or (total_sum is not None and total_sum != order.delivery_sum)
        ):
            async with session_factory() as session:
                db_order = await session.get(Order, order.id)
                if db_order:
                    if cdek_number:
                        db_order.cdek_number = cdek_number
                    if total_sum is not None:
                        db_order.delivery_sum = total_sum
                    await session.commit()
            if cdek_number:
                order.cdek_number = cdek_number
            if total_sum is not None:
                order.delivery_sum = total_sum
        return label or _local_status_label(order), total_sum
    except Exception:
        logger.exception("failed to fetch CDEK status for %s", order.our_number)
        return (
            f"{_local_status_label(order)} (не удалось обновить из СДЭК)",
            None,
        )


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
    title: str = "Мои заказы",
) -> str:
    if not orders:
        return "Заказов пока нет."
    start = page * PAGE_SIZE + 1
    end = page * PAGE_SIZE + len(orders)
    lines = [
        f"<b>{title}</b> · {start}–{end} из {total}",
        "",
    ]
    for idx, order in enumerate(orders, start=1):
        status, total_sum = await _resolve_live_info(
            order, cdek, session_factory=session_factory
        )
        lines.append(
            _format_order_line(idx, order, status, cdek_total_sum=total_sum)
        )
        lines.append("")
    return "\n".join(lines).rstrip()


async def _render_orders_page(
    target: Message | CallbackQuery,
    *,
    page: int,
    session_factory: async_sessionmaker[AsyncSession],
    profiles: ProfileService,
    owner_id: int,
    title: str = "Мои заказы",
    answer_callback: bool = True,
) -> None:
    user = target.from_user
    if user is None:
        return
    orders, total = await _load_page(session_factory, owner_id, page)
    if total == 0:
        text = f"{title}\n\nЗаказов пока нет."
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
    orders, total = await _load_page(session_factory, owner_id, page)
    cdek = await _get_cdek_client(session_factory, profiles, owner_id)
    text = await _build_page_text(
        orders,
        total,
        page,
        cdek=cdek,
        session_factory=session_factory,
        title=title,
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


async def _orders_title(owner_id: int, viewer_id: int, users_label: str | None = None) -> str:
    if owner_id == viewer_id:
        return "Мои заказы"
    if users_label:
        return f"Заказы: {users_label}"
    return f"Заказы пользователя {owner_id}"


@router.message(Command("orders"))
@router.message(F.text == "📋 Мои заказы")
async def my_orders(
    message: Message,
    state: FSMContext,
    session_factory: async_sessionmaker[AsyncSession],
    profiles: ProfileService,
    access: AccessService,
    access_decision: AccessDecision | None = None,
) -> None:
    await state.clear()
    user = message.from_user
    if user is None:
        return
    is_admin = bool(
        access_decision.is_admin
        if access_decision is not None
        else access.is_admin(user.id, user.username)
    )
    if is_admin:
        users = await list_users_for_admin(session_factory)
        options = [
            (u.telegram_user_id, f"{u.label} · заказов: {u.orders_count}")
            for u in users
            if u.telegram_user_id != user.id
        ]
        await state.set_state(HistoryStates.pick_user)
        await message.answer(
            "Чьи заказы показать?",
            reply_markup=admin_user_pick_kb(options, prefix="histuser"),
        )
        return

    await _set_orders_owner(state, user.id)
    await _render_orders_page(
        message,
        page=0,
        session_factory=session_factory,
        profiles=profiles,
        owner_id=user.id,
        title="Мои заказы",
    )


@router.callback_query(HistoryStates.pick_user, F.data == "histuser:cancel")
async def history_pick_user_cancel(callback: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    if callback.message:
        await callback.message.edit_text("Отменено.")
        await callback.message.answer("Главное меню:", reply_markup=main_menu())
    await callback.answer()


@router.callback_query(HistoryStates.pick_user, F.data.startswith("histuser:"))
async def history_pick_user(
    callback: CallbackQuery,
    state: FSMContext,
    session_factory: async_sessionmaker[AsyncSession],
    profiles: ProfileService,
    access: AccessService,
) -> None:
    if callback.from_user is None or not access.is_admin(
        callback.from_user.id, callback.from_user.username
    ):
        await callback.answer("Только для администратора", show_alert=True)
        return
    raw = (callback.data or "").split(":")[-1]
    if raw == "self":
        owner_id = callback.from_user.id
        title = "Мои заказы"
    elif raw.isdigit():
        owner_id = int(raw)
        users = await list_users_for_admin(session_factory)
        label = next(
            (u.label for u in users if u.telegram_user_id == owner_id),
            str(owner_id),
        )
        title = f"Заказы: {label}"
    else:
        await callback.answer("Некорректный пользователь", show_alert=True)
        return

    await _set_orders_owner(state, owner_id)
    await callback.answer()
    await _render_orders_page(
        callback,
        page=0,
        session_factory=session_factory,
        profiles=profiles,
        owner_id=owner_id,
        title=title,
        answer_callback=False,
    )


@router.callback_query(F.data.startswith("hist:page:"))
async def history_page(
    callback: CallbackQuery,
    state: FSMContext,
    session_factory: async_sessionmaker[AsyncSession],
    profiles: ProfileService,
    access: AccessService,
) -> None:
    if callback.from_user is None:
        return
    owner_id = await _resolve_owner_id(
        state,
        callback.from_user.id,
        access=access,
        username=callback.from_user.username,
    )
    await _set_orders_owner(state, owner_id)
    raw = (callback.data or "").split(":")[-1]
    page = int(raw) if raw.isdigit() else 0
    title = await _orders_title(owner_id, callback.from_user.id)
    if owner_id != callback.from_user.id:
        users = await list_users_for_admin(session_factory)
        label = next(
            (u.label for u in users if u.telegram_user_id == owner_id),
            str(owner_id),
        )
        title = f"Заказы: {label}"
    await _render_orders_page(
        callback,
        page=page,
        session_factory=session_factory,
        profiles=profiles,
        owner_id=owner_id,
        title=title,
    )


async def _load_orders_with_cdek_uuid(
    session_factory: async_sessionmaker[AsyncSession],
    telegram_user_id: int,
) -> list[Order]:
    async with session_factory() as session:
        result = await session.execute(
            select(Order)
            .where(
                Order.telegram_user_id == telegram_user_id,
                Order.cdek_uuid.is_not(None),
            )
            .order_by(Order.created_at.desc())
        )
        return list(result.scalars().all())


async def _find_orders_without_cdek_status(
    orders: list[Order],
    cdek: CdekClient,
) -> list[Order]:
    failed: list[Order] = []
    for order in orders:
        if not order.cdek_uuid:
            continue
        try:
            await cdek.get_order(order.cdek_uuid)
        except Exception:
            logger.info(
                "no CDEK status for order %s uuid=%s",
                order.our_number,
                order.cdek_uuid,
            )
            failed.append(order)
    return failed


@router.callback_query(F.data == "hist:purge_nostatus")
async def history_purge_nostatus_ask(
    callback: CallbackQuery,
    state: FSMContext,
    session_factory: async_sessionmaker[AsyncSession],
    profiles: ProfileService,
    access: AccessService,
) -> None:
    await callback.answer()
    if callback.message is None or callback.from_user is None:
        return
    owner_id = await _resolve_owner_id(
        state,
        callback.from_user.id,
        access=access,
        username=callback.from_user.username,
    )
    cdek = await _get_cdek_client(session_factory, profiles, owner_id)
    if cdek is None:
        await callback.message.answer(
            "Чтобы проверить статусы в СДЭК, у выбранного пользователя "
            "должны быть настроены ключи (/setup).",
            reply_markup=main_menu(),
        )
        return

    await callback.message.edit_text("⏳ Проверяю статусы заказов в СДЭК…")
    orders = await _load_orders_with_cdek_uuid(session_factory, owner_id)
    title = await _orders_title(owner_id, callback.from_user.id)
    if owner_id != callback.from_user.id:
        users = await list_users_for_admin(session_factory)
        label = next(
            (u.label for u in users if u.telegram_user_id == owner_id),
            str(owner_id),
        )
        title = f"Заказы: {label}"

    if not orders:
        await callback.message.edit_text("Нет заказов с UUID СДЭК для проверки.")
        await _set_orders_owner(state, owner_id)
        await _render_orders_page(
            callback,
            page=0,
            session_factory=session_factory,
            profiles=profiles,
            owner_id=owner_id,
            title=title,
            answer_callback=False,
        )
        return

    failed = await _find_orders_without_cdek_status(orders, cdek)
    if not failed:
        await callback.message.edit_text(
            "У всех заказов со связью СДЭК статус успешно получен.",
        )
        await _set_orders_owner(state, owner_id)
        await _render_orders_page(
            callback,
            page=0,
            session_factory=session_factory,
            profiles=profiles,
            owner_id=owner_id,
            title=title,
            answer_callback=False,
        )
        return

    await state.set_state(HistoryStates.purge_confirm)
    await state.update_data(
        purge_order_ids=[o.id for o in failed],
        **{ORDERS_OWNER_KEY: owner_id},
    )
    lines = [
        f"<b>Удалить из базы {len(failed)} заказ(ов)</b>, "
        "по которым не удалось получить статус из СДЭК?",
        "",
    ]
    for order in failed[:30]:
        cdek_no = order.cdek_number or "—"
        lines.append(f"• {order.our_number} · СДЭК <code>{cdek_no}</code>")
    if len(failed) > 30:
        lines.append(f"… и ещё {len(failed) - 30}")
    lines.append("")
    lines.append("Заказы в личном кабинете СДЭК не изменяются.")
    await callback.message.edit_text(
        "\n".join(lines),
        reply_markup=history_purge_confirm_kb(),
    )


@router.callback_query(HistoryStates.purge_confirm, F.data == "hist:purge_nostatus_yes")
async def history_purge_nostatus_confirm(
    callback: CallbackQuery,
    state: FSMContext,
    session_factory: async_sessionmaker[AsyncSession],
    profiles: ProfileService,
    access: AccessService,
) -> None:
    data = await state.get_data()
    order_ids = [int(x) for x in (data.get("purge_order_ids") or [])]
    if callback.from_user is None:
        await state.clear()
        await callback.answer("Нечего удалять", show_alert=True)
        return
    owner_id = int(data.get(ORDERS_OWNER_KEY) or callback.from_user.id)
    if not access.is_admin(callback.from_user.id, callback.from_user.username):
        owner_id = callback.from_user.id
    await state.clear()
    if not order_ids:
        await callback.answer("Нечего удалять", show_alert=True)
        return

    pdf_paths: list[str] = []
    async with session_factory() as session:
        result = await session.execute(
            select(Order).where(
                Order.id.in_(order_ids),
                Order.telegram_user_id == owner_id,
            )
        )
        to_delete = list(result.scalars().all())
        for order in to_delete:
            if order.waybill_path:
                pdf_paths.append(order.waybill_path)
            if order.barcode_path:
                pdf_paths.append(order.barcode_path)
        if to_delete:
            await session.execute(
                delete(Order).where(
                    Order.id.in_([o.id for o in to_delete]),
                    Order.telegram_user_id == owner_id,
                )
            )
            await session.commit()
        deleted = len(to_delete)

    for path in pdf_paths:
        if path and Path(path).exists():
            try:
                Path(path).unlink()
            except OSError:
                pass

    await callback.answer(f"Удалено: {deleted}")
    if callback.message:
        await callback.message.edit_text(
            f"✅ Из базы удалено заказов без статуса СДЭК: <b>{deleted}</b>"
        )
    await _set_orders_owner(state, owner_id)
    title = await _orders_title(owner_id, callback.from_user.id)
    if owner_id != callback.from_user.id:
        users = await list_users_for_admin(session_factory)
        label = next(
            (u.label for u in users if u.telegram_user_id == owner_id),
            str(owner_id),
        )
        title = f"Заказы: {label}"
    await _render_orders_page(
        callback,
        page=0,
        session_factory=session_factory,
        profiles=profiles,
        owner_id=owner_id,
        title=title,
        answer_callback=False,
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


async def _render_order_card(
    callback: CallbackQuery,
    order: Order,
    *,
    page: int,
    session_factory: async_sessionmaker[AsyncSession],
    profiles: ProfileService,
    answer_text: str | None = None,
) -> None:
    # Статус/стоимость через ключи владельца заказа
    cdek = await _get_cdek_client(session_factory, profiles, order.telegram_user_id)
    status, total_sum = await _resolve_live_info(
        order, cdek, session_factory=session_factory
    )
    assert callback.message is not None
    await callback.message.edit_text(
        _format_order_detail(order, status, cdek_total_sum=total_sum),
        reply_markup=history_order_kb(
            order.id,
            editable=order_is_local_editable(order),
            has_pdfs=_has_pdfs(order),
            can_fetch_pdf=_can_fetch_pdf(order),
            is_paid=_is_paid(order),
            page=page,
        ),
    )
    if answer_text:
        await callback.answer(answer_text)
    else:
        await callback.answer()


@router.callback_query(F.data.startswith("hist:view:"))
async def history_view_order(
    callback: CallbackQuery,
    state: FSMContext,
    session_factory: async_sessionmaker[AsyncSession],
    profiles: ProfileService,
    access: AccessService,
) -> None:
    order_id, page = _parse_view_callback(callback.data)
    if order_id is None or callback.from_user is None:
        await callback.answer("Некорректный заказ", show_alert=True)
        return
    owner_id = await _resolve_owner_id(
        state,
        callback.from_user.id,
        access=access,
        username=callback.from_user.username,
    )
    order = await _get_user_order(session_factory, order_id, owner_id)
    if not order:
        await callback.answer("Заказ не найден", show_alert=True)
        return
    await _render_order_card(
        callback,
        order,
        page=page,
        session_factory=session_factory,
        profiles=profiles,
    )


@router.callback_query(F.data.startswith("hist:pdf:"))
async def history_send_pdf(
    callback: CallbackQuery,
    state: FSMContext,
    session_factory: async_sessionmaker[AsyncSession],
    profiles: ProfileService,
    settings: Settings,
    access: AccessService,
) -> None:
    if callback.from_user is None:
        return
    order_id = int((callback.data or "").split(":")[-1])
    owner_id = await _resolve_owner_id(
        state,
        callback.from_user.id,
        access=access,
        username=callback.from_user.username,
    )
    order = await _get_user_order(session_factory, order_id, owner_id)
    if not order:
        await callback.answer("Заказ не найден", show_alert=True)
        return
    await callback.answer()
    cdek = await _get_cdek_client(session_factory, profiles, order.telegram_user_id)
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
    access: AccessService,
) -> None:
    if callback.from_user is None:
        return
    order_id = int((callback.data or "").split(":")[-1])
    owner_id = await _resolve_owner_id(
        state,
        callback.from_user.id,
        access=access,
        username=callback.from_user.username,
    )
    order = await _get_user_order(session_factory, order_id, owner_id)
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
    state: FSMContext,
    session_factory: async_sessionmaker[AsyncSession],
    profiles: ProfileService,
    access: AccessService,
) -> None:
    if callback.from_user is None:
        return
    order_id = int((callback.data or "").split(":")[-1])
    owner_id = await _resolve_owner_id(
        state,
        callback.from_user.id,
        access=access,
        username=callback.from_user.username,
    )
    async with session_factory() as session:
        result = await session.execute(
            select(Order).where(
                Order.id == order_id,
                Order.telegram_user_id == owner_id,
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
    order = await _get_user_order(session_factory, order_id, owner_id)
    assert order is not None
    await callback.message.edit_text(
        _format_order_detail(order, _local_status_label(order), cdek_total_sum=None),
        reply_markup=history_order_kb(
            order.id,
            editable=order_is_local_editable(order),
            has_pdfs=_has_pdfs(order),
            can_fetch_pdf=_can_fetch_pdf(order),
            is_paid=_is_paid(order),
            page=0,
        ),
    )
    await callback.answer("Заказ отменён")


@router.callback_query(F.data.startswith("hist:pay:"))
async def history_mark_paid(
    callback: CallbackQuery,
    state: FSMContext,
    session_factory: async_sessionmaker[AsyncSession],
    profiles: ProfileService,
    access: AccessService,
) -> None:
    order_id, page = _parse_order_page_callback(callback.data, prefix_parts=2)
    if order_id is None or callback.from_user is None:
        await callback.answer("Некорректный заказ", show_alert=True)
        return
    owner_id = await _resolve_owner_id(
        state,
        callback.from_user.id,
        access=access,
        username=callback.from_user.username,
    )
    async with session_factory() as session:
        result = await session.execute(
            select(Order).where(
                Order.id == order_id,
                Order.telegram_user_id == owner_id,
            )
        )
        order = result.scalar_one_or_none()
        if not order:
            await callback.answer("Заказ не найден", show_alert=True)
            return
        if order.payment_id is not None:
            await callback.answer("Уже оплачен", show_alert=True)
            return
        total = float(order.delivery_sum or 0)
        payment = CdekPayment(
            telegram_user_id=order.telegram_user_id,
            total_sum=total,
            orders_count=1,
        )
        session.add(payment)
        await session.flush()
        order.payment_id = payment.id
        await session.commit()
        order_id_saved = order.id

    order = await _get_user_order(session_factory, order_id_saved, owner_id)
    assert order is not None
    await _render_order_card(
        callback,
        order,
        page=page,
        session_factory=session_factory,
        profiles=profiles,
        answer_text="Помечен оплаченным",
    )


@router.callback_query(F.data.startswith("hist:unpay:"))
async def history_mark_unpaid(
    callback: CallbackQuery,
    state: FSMContext,
    session_factory: async_sessionmaker[AsyncSession],
    profiles: ProfileService,
    access: AccessService,
) -> None:
    order_id, page = _parse_order_page_callback(callback.data, prefix_parts=2)
    if order_id is None or callback.from_user is None:
        await callback.answer("Некорректный заказ", show_alert=True)
        return
    owner_id = await _resolve_owner_id(
        state,
        callback.from_user.id,
        access=access,
        username=callback.from_user.username,
    )
    async with session_factory() as session:
        result = await session.execute(
            select(Order).where(
                Order.id == order_id,
                Order.telegram_user_id == owner_id,
            )
        )
        order = result.scalar_one_or_none()
        if not order:
            await callback.answer("Заказ не найден", show_alert=True)
            return
        if order.payment_id is None:
            await callback.answer("Уже не оплачен", show_alert=True)
            return
        order.payment_id = None
        await session.commit()
        order_id_saved = order.id

    order = await _get_user_order(session_factory, order_id_saved, owner_id)
    assert order is not None
    await _render_order_card(
        callback,
        order,
        page=page,
        session_factory=session_factory,
        profiles=profiles,
        answer_text="Помечен не оплаченным",
    )


def _parse_order_page_callback(data: str | None, *, prefix_parts: int) -> tuple[int | None, int]:
    """hist:delete:{id}:{page} / hist:delete_yes:{id}:{page} / hist:pay:{id}:{page}"""
    parts = (data or "").split(":")
    if len(parts) < prefix_parts + 1 or not parts[prefix_parts].isdigit():
        return None, 0
    order_id = int(parts[prefix_parts])
    page = (
        int(parts[prefix_parts + 1])
        if len(parts) > prefix_parts + 1 and parts[prefix_parts + 1].isdigit()
        else 0
    )
    return order_id, page


@router.callback_query(F.data.startswith("hist:delete:"))
async def history_delete_ask(
    callback: CallbackQuery,
    state: FSMContext,
    session_factory: async_sessionmaker[AsyncSession],
    access: AccessService,
) -> None:
    order_id, page = _parse_order_page_callback(callback.data, prefix_parts=2)
    if order_id is None or callback.from_user is None:
        await callback.answer("Некорректный заказ", show_alert=True)
        return
    owner_id = await _resolve_owner_id(
        state,
        callback.from_user.id,
        access=access,
        username=callback.from_user.username,
    )
    order = await _get_user_order(session_factory, order_id, owner_id)
    if not order:
        await callback.answer("Заказ не найден", show_alert=True)
        return
    cdek = order.cdek_number or "—"
    note = ""
    if order.cdek_uuid:
        note = (
            "\n\nЗаказ уже есть в СДЭК — удалится только запись в боте, "
            "не сам заказ в личном кабинете СДЭК."
        )
    await callback.message.edit_text(
        f"Удалить заказ <b>{order.our_number}</b> из базы бота?\n"
        f"Номер СДЭК: <code>{cdek}</code>{note}",
        reply_markup=history_delete_confirm_kb(order.id, page=page),
    )
    await callback.answer()


@router.callback_query(F.data.startswith("hist:delete_yes:"))
async def history_delete_confirm(
    callback: CallbackQuery,
    state: FSMContext,
    session_factory: async_sessionmaker[AsyncSession],
    profiles: ProfileService,
    access: AccessService,
) -> None:
    order_id, page = _parse_order_page_callback(callback.data, prefix_parts=2)
    if order_id is None or callback.from_user is None:
        await callback.answer("Некорректный заказ", show_alert=True)
        return
    owner_id = await _resolve_owner_id(
        state,
        callback.from_user.id,
        access=access,
        username=callback.from_user.username,
    )
    async with session_factory() as session:
        result = await session.execute(
            select(Order).where(
                Order.id == order_id,
                Order.telegram_user_id == owner_id,
            )
        )
        order = result.scalar_one_or_none()
        if not order:
            await callback.answer("Заказ не найден", show_alert=True)
            return
        waybill_path = order.waybill_path
        barcode_path = order.barcode_path
        our_number = order.our_number
        await session.execute(delete(Order).where(Order.id == order.id))
        await session.commit()
    for path in (waybill_path, barcode_path):
        if path and Path(path).exists():
            try:
                Path(path).unlink()
            except OSError:
                pass
    await callback.answer(f"Удалён {our_number}")
    await _set_orders_owner(state, owner_id)
    title = await _orders_title(owner_id, callback.from_user.id)
    if owner_id != callback.from_user.id:
        users = await list_users_for_admin(session_factory)
        label = next(
            (u.label for u in users if u.telegram_user_id == owner_id),
            str(owner_id),
        )
        title = f"Заказы: {label}"
    await _render_orders_page(
        callback,
        page=page,
        session_factory=session_factory,
        profiles=profiles,
        owner_id=owner_id,
        title=title,
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
