from __future__ import annotations

import logging
from dataclasses import dataclass

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.bot.deps import ensure_runtime
from app.bot.keyboards.common import main_menu, payment_confirm_kb
from app.bot.states import PaymentStates
from app.db.models import CdekPayment, Order
from app.services.cdek import CdekClient
from app.services.profile import ProfileService

logger = logging.getLogger(__name__)
router = Router(name="payment")

# Лимит Telegram на сообщение ~4096; оставляем запас
_CHUNK_LIMIT = 3500


@dataclass
class PayableItem:
    order_id: int
    cdek_number: str
    total_sum: float


def _chunk_texts(lines: list[str], limit: int = _CHUNK_LIMIT) -> list[str]:
    chunks: list[str] = []
    current: list[str] = []
    size = 0
    for line in lines:
        add = len(line) + (1 if current else 0)
        if current and size + add > limit:
            chunks.append("\n".join(current))
            current = [line]
            size = len(line)
        else:
            current.append(line)
            size += add
    if current:
        chunks.append("\n".join(current))
    return chunks


async def _load_unpaid_orders(
    session_factory: async_sessionmaker[AsyncSession],
    telegram_user_id: int,
) -> list[Order]:
    async with session_factory() as session:
        result = await session.execute(
            select(Order)
            .where(
                Order.telegram_user_id == telegram_user_id,
                Order.payment_id.is_(None),
                Order.cdek_uuid.is_not(None),
                Order.status != "cancelled",
            )
            .order_by(Order.created_at.asc())
        )
        return list(result.scalars().all())


async def _collect_payable(
    orders: list[Order],
    cdek: CdekClient,
    session_factory: async_sessionmaker[AsyncSession],
) -> tuple[list[PayableItem], list[str]]:
    """
    Собирает заказы с известной стоимостью из СДЭК.
    Возвращает (к оплате, предупреждения о заказах без суммы).
    """
    payable: list[PayableItem] = []
    warnings: list[str] = []
    for order in orders:
        assert order.cdek_uuid
        try:
            entity = await cdek.get_order(order.cdek_uuid)
            cdek_number = str(entity.get("cdek_number") or "") or order.cdek_number
            total_sum = CdekClient.order_total_sum(entity)
            if cdek_number and cdek_number != order.cdek_number:
                async with session_factory() as session:
                    db_order = await session.get(Order, order.id)
                    if db_order:
                        db_order.cdek_number = cdek_number
                        if total_sum is not None:
                            db_order.delivery_sum = total_sum
                        await session.commit()
                order.cdek_number = cdek_number
            elif total_sum is not None:
                async with session_factory() as session:
                    db_order = await session.get(Order, order.id)
                    if db_order and db_order.delivery_sum != total_sum:
                        db_order.delivery_sum = total_sum
                        await session.commit()
                order.delivery_sum = total_sum
        except Exception:
            logger.exception("payment: failed to fetch order %s", order.our_number)
            warnings.append(
                f"• {order.cdek_number or order.our_number} — не удалось получить данные из СДЭК"
            )
            continue

        if total_sum is None:
            warnings.append(
                f"• {cdek_number or order.our_number} — стоимость ещё не готова в СДЭК"
            )
            continue
        if not cdek_number:
            warnings.append(f"• {order.our_number} — нет номера СДЭК")
            continue
        payable.append(
            PayableItem(
                order_id=order.id,
                cdek_number=cdek_number,
                total_sum=float(total_sum),
            )
        )
    return payable, warnings


@router.message(Command("pay"))
@router.message(F.text == "💳 Оплатить за СДЭК")
async def payment_start(
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
    _, cdek, _ = ready

    wait = await message.answer("⏳ Собираю неоплаченные заказы и стоимости из СДЭК…")
    orders = await _load_unpaid_orders(session_factory, message.from_user.id)
    if not orders:
        await wait.edit_text(
            "Неоплаченных заказов нет.\nВсе созданные в СДЭК заказы уже учтены в оплатах.",
        )
        await message.answer("Главное меню:", reply_markup=main_menu())
        return

    payable, warnings = await _collect_payable(orders, cdek, session_factory)
    if not payable:
        text = "Не удалось получить стоимость ни по одному неоплаченному заказу."
        if warnings:
            text += "\n\n" + "\n".join(warnings[:20])
        await wait.edit_text(text)
        await message.answer("Главное меню:", reply_markup=main_menu())
        return

    list_lines = ["<b>Заказы к оплате за СДЭК</b>", ""]
    for item in payable:
        list_lines.append(
            f"• <code>{item.cdek_number}</code> — <b>{item.total_sum:.0f} ₽</b>"
        )
    if warnings:
        list_lines.extend(["", "<i>Не вошли в расчёт:</i>", *warnings[:15]])

    chunks = _chunk_texts(list_lines)
    await wait.edit_text(chunks[0])
    for chunk in chunks[1:]:
        await message.answer(chunk)

    total = sum(item.total_sum for item in payable)
    await state.set_state(PaymentStates.confirm)
    await state.update_data(
        payment_items=[
            {
                "order_id": item.order_id,
                "cdek_number": item.cdek_number,
                "total_sum": item.total_sum,
            }
            for item in payable
        ],
    )
    await message.answer(
        f"<b>Итого к оплате: {total:.0f} ₽</b>\n"
        f"Заказов: {len(payable)}",
        reply_markup=payment_confirm_kb(),
    )


@router.callback_query(PaymentStates.confirm, F.data == "payment:cancel")
async def payment_cancel(callback: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    if callback.message:
        await callback.message.edit_text("Оплата отменена.")
        await callback.message.answer("Главное меню:", reply_markup=main_menu())
    await callback.answer()


@router.callback_query(PaymentStates.confirm, F.data == "payment:confirm")
async def payment_confirm(
    callback: CallbackQuery,
    state: FSMContext,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    data = await state.get_data()
    items = data.get("payment_items") or []
    amounts = {
        int(item["order_id"]): float(item["total_sum"])
        for item in items
        if item.get("order_id") is not None
    }
    order_ids = list(amounts.keys())
    if not order_ids or callback.from_user is None:
        await state.clear()
        await callback.answer("Нет данных для оплаты", show_alert=True)
        return

    async with session_factory() as session:
        # Повторно проверяем, что заказы ещё не оплачены
        result = await session.execute(
            select(Order.id).where(
                Order.id.in_(order_ids),
                Order.telegram_user_id == callback.from_user.id,
                Order.payment_id.is_(None),
            )
        )
        still_open = [row[0] for row in result.all()]
        if not still_open:
            await state.clear()
            await callback.answer("Эти заказы уже оплачены", show_alert=True)
            if callback.message:
                await callback.message.edit_text("Эти заказы уже были оплачены ранее.")
                await callback.message.answer("Главное меню:", reply_markup=main_menu())
            return

        total = sum(amounts[oid] for oid in still_open if oid in amounts)
        payment = CdekPayment(
            telegram_user_id=callback.from_user.id,
            total_sum=total,
            orders_count=len(still_open),
        )
        session.add(payment)
        await session.flush()
        await session.execute(
            update(Order)
            .where(Order.id.in_(still_open))
            .values(payment_id=payment.id)
        )
        await session.commit()
        payment_id = payment.id

    await state.clear()
    if callback.message:
        await callback.message.edit_text(
            f"✅ Оплата подтверждена\n"
            f"Запись №{payment_id}\n"
            f"Сумма: <b>{total:.0f} ₽</b>\n"
            f"Заказов: {len(still_open)}"
        )
        await callback.message.answer(
            "В следующий раз в расчёт попадут только новые неоплаченные заказы.",
            reply_markup=main_menu(),
        )
    await callback.answer("Оплата сохранена")
