from __future__ import annotations

import logging

from aiogram import Bot, F, Router
from aiogram.filters import Command
from aiogram.types import CallbackQuery, Message
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.bot.keyboards.common import admin_access_kb, main_menu, request_access_kb
from app.config import Settings
from app.services.access import (
    STATUS_PENDING,
    STATUS_REJECTED,
    AccessDecision,
    AccessService,
    format_cooldown,
)

logger = logging.getLogger(__name__)
router = Router(name="access")


def _user_label(user) -> str:
    uname = f"@{user.username}" if user.username else "—"
    name = user.full_name or "—"
    return f"{name} ({uname}, id <code>{user.id}</code>)"


async def notify_admins(
    bot: Bot,
    access: AccessService,
    settings: Settings,
    *,
    text: str,
    reply_markup=None,
) -> int:
    sent = 0
    targets = access.admin_notify_ids() | set(settings.admin_user_ids)
    for admin_id in targets:
        try:
            await bot.send_message(admin_id, text, reply_markup=reply_markup)
            sent += 1
        except Exception:
            logger.exception("failed to notify admin %s", admin_id)
    return sent


@router.message(Command("request_access"))
@router.message(F.text == "🔑 Запросить доступ")
async def request_access(
    message: Message,
    bot: Bot,
    settings: Settings,
    access: AccessService,
    session_factory: async_sessionmaker[AsyncSession],
    access_decision: AccessDecision | None = None,
) -> None:
    user = message.from_user
    if user is None:
        return

    decision = access_decision or await access.evaluate(
        session_factory, user.id, user.username
    )
    if decision.allowed:
        await message.answer(
            "У вас уже есть доступ к боту.",
            reply_markup=main_menu(),
        )
        return

    if decision.status == STATUS_PENDING:
        await message.answer(
            "⏳ Заявка уже отправлена администратору. Ожидайте решения.",
            reply_markup=request_access_kb(),
        )
        return

    if decision.status == STATUS_REJECTED and not decision.can_request:
        await message.answer(
            "⛔ Вам запрещено пользоваться ботом.\n"
            f"Повторный запрос можно отправить {format_cooldown(decision.cooldown_until)}.",
            reply_markup=request_access_kb(),
        )
        return

    await access.create_or_renew_request(
        session_factory,
        user_id=user.id,
        username=user.username,
        full_name=user.full_name,
    )

    admin_text = (
        "🔔 <b>Новая заявка на доступ к боту</b>\n\n"
        f"Пользователь: {_user_label(user)}\n"
        "Подтвердить доступ?"
    )
    sent = await notify_admins(
        bot,
        access,
        settings,
        text=admin_text,
        reply_markup=admin_access_kb(user.id),
    )

    if sent == 0:
        await message.answer(
            "Не удалось отправить заявку администратору (нет доступных ADMIN_TELEGRAM_IDS).\n"
            "Передайте администратору ваш ID: "
            f"<code>{user.id}</code>",
            reply_markup=request_access_kb(),
        )
        return

    await message.answer(
        "✅ Заявка отправлена администратору.\nОжидайте подтверждения в этом чате.",
        reply_markup=request_access_kb(),
    )


@router.callback_query(F.data.startswith("access:approve:"))
@router.callback_query(F.data.startswith("access:reject:"))
async def admin_decide(
    callback: CallbackQuery,
    bot: Bot,
    access: AccessService,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    admin = callback.from_user
    if admin is None or not access.is_admin(admin.id, admin.username):
        await callback.answer("Только администратор может решать заявки", show_alert=True)
        return

    parts = (callback.data or "").split(":")
    if len(parts) != 3 or not parts[2].isdigit():
        await callback.answer("Некорректная заявка", show_alert=True)
        return

    action = parts[1]
    user_id = int(parts[2])
    approve = action == "approve"

    decision = await access.evaluate(session_factory, user_id, None)
    if decision.status != STATUS_PENDING:
        status = decision.status or "нет заявки"
        await callback.answer(f"Заявка уже обработана ({status})", show_alert=True)
        if callback.message:
            await callback.message.edit_reply_markup(reply_markup=None)
        return

    record = await access.decide(
        session_factory,
        user_id=user_id,
        approve=approve,
        admin_id=admin.id,
    )
    if record is None:
        await callback.answer("Заявка не найдена", show_alert=True)
        return

    if approve:
        result_text = (
            f"✅ Доступ подтверждён для id <code>{user_id}</code>\n"
            f"Администратор: {_user_label(admin)}"
        )
        user_text = (
            "✅ Администратор подтвердил доступ к боту.\n"
            "Нажмите /start, чтобы начать настройку."
        )
    else:
        result_text = (
            f"❌ Доступ отклонён для id <code>{user_id}</code>\n"
            f"Администратор: {_user_label(admin)}"
        )
        user_text = (
            "⛔ Администратор запретил пользоваться ботом.\n"
            "Повторный запрос можно отправить через сутки."
        )

    if callback.message:
        await callback.message.edit_text(result_text)
    await callback.answer("Готово")

    try:
        await bot.send_message(
            user_id,
            user_text,
            reply_markup=main_menu() if approve else request_access_kb(),
        )
    except Exception:
        logger.exception("failed to notify user %s about access decision", user_id)
