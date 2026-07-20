"""Список пользователей для админских сценариев (заказы / оплата)."""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.db.models import BotAccess, Order
from app.services.access import STATUS_APPROVED


@dataclass
class UserOption:
    telegram_user_id: int
    label: str
    orders_count: int = 0


def format_user_label(
    user_id: int,
    *,
    username: str | None = None,
    full_name: str | None = None,
) -> str:
    parts: list[str] = []
    if full_name:
        parts.append(full_name.strip())
    if username:
        parts.append(f"@{username.lstrip('@')}")
    if not parts:
        return f"id {user_id}"
    return f"{' · '.join(parts)} ({user_id})"


async def list_users_for_admin(
    session_factory: async_sessionmaker[AsyncSession],
) -> list[UserOption]:
    """Пользователи с заказами и/или approved-доступом."""
    async with session_factory() as session:
        counts_result = await session.execute(
            select(Order.telegram_user_id, func.count())
            .group_by(Order.telegram_user_id)
            .order_by(func.count().desc())
        )
        counts = {int(uid): int(cnt) for uid, cnt in counts_result.all()}

        access_result = await session.execute(
            select(BotAccess).where(BotAccess.status == STATUS_APPROVED)
        )
        access_rows = list(access_result.scalars().all())

    by_id: dict[int, UserOption] = {}
    for row in access_rows:
        uid = int(row.telegram_user_id)
        by_id[uid] = UserOption(
            telegram_user_id=uid,
            label=format_user_label(
                uid, username=row.username, full_name=row.full_name
            ),
            orders_count=counts.get(uid, 0),
        )

    for uid, cnt in counts.items():
        if uid in by_id:
            by_id[uid].orders_count = cnt
            continue
        by_id[uid] = UserOption(
            telegram_user_id=uid,
            label=format_user_label(uid),
            orders_count=cnt,
        )

    users = list(by_id.values())
    users.sort(key=lambda u: (-u.orders_count, u.label.casefold()))
    return users
