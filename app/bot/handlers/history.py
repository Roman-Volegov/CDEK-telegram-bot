from __future__ import annotations

from pathlib import Path

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, FSInputFile, Message
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.bot.handlers.orders import load_saved_order_for_edit, order_is_local_editable
from app.bot.keyboards.common import history_order_kb, main_menu
from app.db.models import Order
from app.services.profile import ProfileService


router = Router(name="history")


def _format_order(order: Order) -> str:
    cdek = order.cdek_number or order.cdek_uuid or "—"
    return (
        f"<b>Заказ {order.our_number}</b>\n"
        f"Статус: {order.status}\n"
        f"Адрес: {order.to_address}\n"
        f"Получатель: {order.recipient_name}, {order.recipient_phone}\n"
        f"Тариф: {order.tariff_name or order.tariff_code}\n"
        f"Стоимость товара: {float(order.item_cost):.0f} ₽\n"
        f"CDEK: <code>{cdek}</code>"
    )


def _has_pdfs(order: Order) -> bool:
    return bool(
        (order.waybill_path and Path(order.waybill_path).exists())
        or (order.barcode_path and Path(order.barcode_path).exists())
    )


@router.message(Command("orders"))
@router.message(F.text == "📋 Мои заказы")
async def my_orders(
    message: Message,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    async with session_factory() as session:
        result = await session.execute(
            select(Order)
            .where(Order.telegram_user_id == message.from_user.id)
            .order_by(Order.created_at.desc())
            .limit(10)
        )
        orders = list(result.scalars().all())

    if not orders:
        await message.answer("Заказов пока нет.", reply_markup=main_menu())
        return

    for order in orders:
        await message.answer(
            _format_order(order),
            reply_markup=history_order_kb(
                order.id,
                editable=order_is_local_editable(order),
                has_pdfs=_has_pdfs(order),
            ),
        )
    await message.answer("Выберите действие для заказа.", reply_markup=main_menu())


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


@router.callback_query(F.data.startswith("hist:view:"))
async def history_view_order(
    callback: CallbackQuery,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    order_id = int((callback.data or "").split(":")[-1])
    order = await _get_user_order(session_factory, order_id, callback.from_user.id)
    if not order:
        await callback.answer("Заказ не найден", show_alert=True)
        return
    await callback.message.edit_text(
        _format_order(order),
        reply_markup=history_order_kb(
            order.id,
            editable=order_is_local_editable(order),
            has_pdfs=_has_pdfs(order),
        ),
    )
    await callback.answer()


@router.callback_query(F.data.startswith("hist:pdf:"))
async def history_send_pdf(
    callback: CallbackQuery,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    order_id = int((callback.data or "").split(":")[-1])
    order = await _get_user_order(session_factory, order_id, callback.from_user.id)
    if not order:
        await callback.answer("Заказ не найден", show_alert=True)
        return
    sent = False
    if order.waybill_path and Path(order.waybill_path).exists():
        await callback.message.answer_document(FSInputFile(order.waybill_path))
        sent = True
    if order.barcode_path and Path(order.barcode_path).exists():
        await callback.message.answer_document(FSInputFile(order.barcode_path))
        sent = True
    if not sent:
        await callback.answer("PDF для этого заказа ещё нет", show_alert=True)
        return
    await callback.answer("PDF отправлены")


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
        await callback.answer("Этот заказ уже в работе и недоступен для редактирования", show_alert=True)
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
            await callback.answer("Этот заказ уже в работе и не может быть отменён", show_alert=True)
            return
        order.status = "cancelled"
        await session.commit()
    order = await _get_user_order(session_factory, order_id, callback.from_user.id)
    assert order is not None
    await callback.message.edit_text(
        _format_order(order),
        reply_markup=history_order_kb(
            order.id,
            editable=order_is_local_editable(order),
            has_pdfs=_has_pdfs(order),
        ),
    )
    await callback.answer("Заказ отменён")


@router.callback_query(F.data.startswith("hist:delete:"))
async def history_delete_order(
    callback: CallbackQuery,
    session_factory: async_sessionmaker[AsyncSession],
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
            await callback.answer("Этот заказ уже в работе и не может быть удалён", show_alert=True)
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
    await callback.message.edit_text("🗑 Заказ удалён.")
    await callback.answer("Заказ удалён")


@router.message(Command("pdf"))
async def resend_pdf(message: Message, session_factory: async_sessionmaker[AsyncSession]) -> None:
    parts = (message.text or "").split(maxsplit=1)
    if len(parts) < 2:
        await message.answer("Использование: <code>/pdf 2026-000001</code>")
        return
    number = parts[1].strip()
    async with session_factory() as session:
        result = await session.execute(
            select(Order).where(
                Order.our_number == number,
                Order.telegram_user_id == message.from_user.id,
            )
        )
        order = result.scalar_one_or_none()
    if not order:
        await message.answer("Заказ не найден.")
        return
    sent = False
    if order.waybill_path and Path(order.waybill_path).exists():
        await message.answer_document(FSInputFile(order.waybill_path))
        sent = True
    if order.barcode_path and Path(order.barcode_path).exists():
        await message.answer_document(FSInputFile(order.barcode_path), reply_markup=main_menu())
        sent = True
    if not sent:
        await message.answer("PDF для этого заказа ещё нет.", reply_markup=main_menu())
