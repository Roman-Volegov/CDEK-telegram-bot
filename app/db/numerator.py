from datetime import datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import OrderSequence


async def next_order_number(session: AsyncSession) -> str:
    """Выдаёт номер вида 2026-000001. Атомарно через SELECT FOR UPDATE."""
    year = datetime.now().year
    result = await session.execute(
        select(OrderSequence).where(OrderSequence.year == year).with_for_update()
    )
    seq = result.scalar_one_or_none()
    if seq is None:
        seq = OrderSequence(year=year, next_value=1)
        session.add(seq)
        await session.flush()

    number = f"{year}-{seq.next_value:06d}"
    seq.next_value += 1
    await session.flush()
    return number
