from __future__ import annotations

from pathlib import Path

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.types import FSInputFile, Message
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.bot.keyboards.common import main_menu
from app.db.models import Order


router = Router(name="history")


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

    lines = ["<b>Последние заказы</b>"]
    for order in orders:
        cdek = order.cdek_number or order.cdek_uuid or "—"
        lines.append(
            f"• <code>{order.our_number}</code> — {order.status}\n"
            f"  {order.to_address}\n"
            f"  Получатель: {order.recipient_name}\n"
            f"  CDEK: <code>{cdek}</code>"
        )
    await message.answer("\n\n".join(lines), reply_markup=main_menu())


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
