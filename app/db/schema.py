"""Создание и лёгкие миграции схемы (без Alembic)."""

from __future__ import annotations

import logging

from sqlalchemy import inspect, text
from sqlalchemy.ext.asyncio import AsyncEngine

from app.db.models import Base

logger = logging.getLogger(__name__)


def _ensure_orders_comment(sync_conn) -> None:
    insp = inspect(sync_conn)
    if "orders" not in insp.get_table_names():
        return
    cols = {c["name"] for c in insp.get_columns("orders")}
    if "comment" in cols:
        return
    logger.info("Adding orders.comment column")
    sync_conn.execute(text("ALTER TABLE orders ADD COLUMN comment VARCHAR(255)"))


def _ensure_orders_payment_id(sync_conn) -> None:
    insp = inspect(sync_conn)
    if "orders" not in insp.get_table_names():
        return
    cols = {c["name"] for c in insp.get_columns("orders")}
    if "payment_id" in cols:
        return
    logger.info("Adding orders.payment_id column")
    sync_conn.execute(text("ALTER TABLE orders ADD COLUMN payment_id INTEGER"))
    sync_conn.execute(
        text("CREATE INDEX IF NOT EXISTS ix_orders_payment_id ON orders (payment_id)")
    )


async def init_schema(engine: AsyncEngine) -> None:
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        await conn.run_sync(_ensure_orders_payment_id)
        await conn.run_sync(_ensure_orders_comment)
