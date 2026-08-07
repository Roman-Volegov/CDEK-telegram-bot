from __future__ import annotations

import logging
from dataclasses import dataclass

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.bot.admin_users import list_users_for_admin
from app.bot.keyboards.common import admin_user_pick_kb, main_menu, payment_confirm_kb
from app.bot.states import PaymentStates
from app.db.models import CdekPayment, Order
from app.services.access import AccessDecision, AccessService
from app.services.cdek import CdekClient
from app.services.profile import ProfileService, load_runtime_for_user

logger = logging.getLogger(__name__)
router = Router(name="payment")

_CHUNK_LIMIT = 3500
PAYMENT_OWNER_KEY = "payment_owner_id"


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


async def _run_payment_calc(
    message: Message,
    state: FSMContext,
    *,
    owner_id: int,
    owner_label: str,
    profiles: ProfileService,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    cfg = await load_runtime_for_user(session_factory, profiles, owner_id)
    if cfg is None:
        await message.answer(
            f"У пользователя <b>{owner_label}</b> не настроены ключи СДЭК (/setup).\n"
            "Без них нельзя получить стоимости заказов.",
            reply_markup=main_menu(),
        )
        await state.clear()
        return

    cdek = CdekClient(cfg)
    wait = await message.answer(
        f"⏳ Собираю неоплаченные заказы для <b>{owner_label}</b>…"
    )
    orders = await _load_unpaid_orders(session_factory, owner_id)
    if not orders:
        await wait.edit_text(
            f"У <b>{owner_label}</b> нет неоплаченных заказов.\n"
            "Все созданные в СДЭК заказы уже учтены в оплатах.",
        )
        await message.answer("Главное меню:", reply_markup=main_menu())
        await state.clear()
        return

    payable, warnings = await _collect_payable(orders, cdek, session_factory)
    if not payable:
        text = "Не удалось получить стоимость ни по одному неоплаченному заказу."
        if warnings:
            text += "\n\n" + "\n".join(warnings[:20])
        await wait.edit_text(text)
        await message.answer("Главное меню:", reply_markup=main_menu())
        await state.clear()
        return

    list_lines = [
        f"<b>Заказы к оплате за СДЭК</b>",
        f"Пользователь: <b>{owner_label}</b>",
        "",
    ]
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
        **{PAYMENT_OWNER_KEY: owner_id},
        payment_owner_label=owner_label,
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
        f"Пользователь: {owner_label}\n"
        f"Заказов: {len(payable)}",
        reply_markup=payment_confirm_kb(),
    )


@router.message(Command("pay"))
@router.message(F.text == "💳 Оплатить за СДЭК")
async def payment_start(
    message: Message,
    state: FSMContext,
    profiles: ProfileService,
    session_factory: async_sessionmaker[AsyncSession],
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
        await state.set_state(PaymentStates.pick_user)
        await message.answer(
            "По чьим заказам посчитать оплату за СДЭК?",
            reply_markup=admin_user_pick_kb(options, prefix="payuser"),
        )
        return

    await _run_payment_calc(
        message,
        state,
        owner_id=user.id,
        owner_label="я",
        profiles=profiles,
        session_factory=session_factory,
    )


@router.callback_query(PaymentStates.pick_user, F.data == "payuser:cancel")
async def payment_pick_user_cancel(callback: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    if callback.message:
        await callback.message.edit_text("Оплата отменена.")
        await callback.message.answer("Главное меню:", reply_markup=main_menu())
    await callback.answer()


@router.callback_query(PaymentStates.pick_user, F.data.startswith("payuser:"))
async def payment_pick_user(
    callback: CallbackQuery,
    state: FSMContext,
    profiles: ProfileService,
    session_factory: async_sessionmaker[AsyncSession],
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
        owner_label = "я"
    elif raw.isdigit():
        owner_id = int(raw)
        users = await list_users_for_admin(session_factory)
        owner_label = next(
            (u.label for u in users if u.telegram_user_id == owner_id),
            str(owner_id),
        )
    else:
        await callback.answer("Некорректный пользователь", show_alert=True)
        return

    await callback.answer()
    if callback.message:
        await callback.message.edit_text(f"Считаю оплату для: <b>{owner_label}</b>")
        await _run_payment_calc(
            callback.message,
            state,
            owner_id=owner_id,
            owner_label=owner_label,
            profiles=profiles,
            session_factory=session_factory,
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
    access: AccessService,
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

    owner_id = int(data.get(PAYMENT_OWNER_KEY) or callback.from_user.id)
    if not access.is_admin(callback.from_user.id, callback.from_user.username):
        owner_id = callback.from_user.id
    owner_label = str(data.get("payment_owner_label") or owner_id)

    async with session_factory() as session:
        result = await session.execute(
            select(Order.id).where(
                Order.id.in_(order_ids),
                Order.telegram_user_id == owner_id,
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
            telegram_user_id=owner_id,
            total_sum=total,
            orders_count=len(still_open),
        )
        session.add(payment)
        await session.flush()
        await session.execute(
            update(Order)
            .where(Order.id.in_(still_open), Order.telegram_user_id == owner_id)
            .values(payment_id=payment.id)
        )
        await session.commit()
        payment_id = payment.id

    await state.clear()
    if callback.message:
        await callback.message.edit_text(
            f"✅ Оплата подтверждена\n"
            f"Пользователь: <b>{owner_label}</b>\n"
            f"Запись №{payment_id}\n"
            f"Сумма: <b>{total:.0f} ₽</b>\n"
            f"Заказов: {len(still_open)}"
        )
        await callback.message.answer(
            "В следующий раз в расчёт попадут только новые неоплаченные заказы.",
            reply_markup=main_menu(),
        )
    await callback.answer("Оплата сохранена")
