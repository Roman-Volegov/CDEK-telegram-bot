from __future__ import annotations

import logging
from pathlib import Path

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.types import FSInputFile, Message
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.bot.keyboards.common import main_menu
from app.db.models import Order

logger = logging.getLogger(__name__)
router = Router(name="history")


@router.message(Command("orders"))
@router.message(F.text == "📋 Мои заказы")
async def list_orders(
    message: Message,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    async with session_factory() as session:
        result = await session.execute(
            select(Order)
            .where(Order.telegram_user_id == message.from_user.id)
            .order_by(Order.id.desc())
            .limit(10)
        )
        orders = list(result.scalars().all())

    if not orders:
        await message.answer("Заказов пока нет.", reply_markup=main_menu())
        return

    lines = ["<b>Последние заказы:</b>"]
    for o in orders:
        track = o.cdek_number or "—"
        lines.append(
            f"• <code>{o.our_number}</code> · {o.status} · трек {track}\n"
            f"  {o.to_address[:80]}"
        )
    lines.append("\nЧтобы повторно получить PDF, отправьте номер заказа: <code>2026-000001</code>")
    await message.answer("\n".join(lines), reply_markup=main_menu())


@router.message(F.text.regexp(r"^\d{4}-\d{6}$"))
async def resend_pdfs(
    message: Message,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    number = (message.text or "").strip()
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
    if order.status != "completed" or not order.waybill_path or not order.barcode_path:
        await message.answer(f"PDF для {number} недоступны (статус: {order.status}).")
        return

    waybill = Path(order.waybill_path)
    barcode = Path(order.barcode_path)
    if not waybill.exists() or not barcode.exists():
        await message.answer("Файлы PDF на диске не найдены.")
        return

    await message.answer_document(FSInputFile(waybill, filename=waybill.name))
    await message.answer_document(
        FSInputFile(barcode, filename=barcode.name),
        reply_markup=main_menu(),
    )
