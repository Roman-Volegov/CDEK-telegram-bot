from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.config import Settings
from app.db.models import BotAccess

STATUS_PENDING = "pending"
STATUS_APPROVED = "approved"
STATUS_REJECTED = "rejected"

REJECT_COOLDOWN = timedelta(hours=24)


@dataclass
class AccessDecision:
    allowed: bool
    is_admin: bool
    status: str | None  # None = нет заявки
    record: BotAccess | None = None
    cooldown_until: datetime | None = None

    @property
    def can_request(self) -> bool:
        if self.allowed or self.is_admin:
            return False
        if self.status is None:
            return True
        if self.status == STATUS_PENDING:
            return False
        if self.status == STATUS_REJECTED:
            if self.cooldown_until is None:
                return True
            return datetime.now(timezone.utc) >= self.cooldown_until
        return False


class AccessService:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        # Числовые ID админов, в т.ч. обнаруженные по @username после первого визита
        self._known_admin_ids: set[int] = set(settings.admin_user_ids)

    def is_admin(self, user_id: int | None, username: str | None = None) -> bool:
        return self.settings.is_admin(user_id, username)

    def remember_admin(self, user_id: int | None, username: str | None = None) -> None:
        if user_id is not None and self.is_admin(user_id, username):
            self._known_admin_ids.add(user_id)

    def admin_notify_ids(self) -> set[int]:
        return set(self._known_admin_ids)

    async def get(
        self, session: AsyncSession, telegram_user_id: int
    ) -> BotAccess | None:
        return await session.get(BotAccess, telegram_user_id)

    async def evaluate(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        user_id: int,
        username: str | None = None,
        full_name: str | None = None,
    ) -> AccessDecision:
        if self.is_admin(user_id, username):
            self.remember_admin(user_id, username)
            return AccessDecision(allowed=True, is_admin=True, status=STATUS_APPROVED)

        async with session_factory() as session:
            record = await self.get(session, user_id)

            # Старый whitelist по username/id — один раз записываем approved
            if (
                (record is None or record.status != STATUS_APPROVED)
                and self.settings.is_bootstrap_approved(user_id, username)
            ):
                now = datetime.now(timezone.utc)
                if record is None:
                    record = BotAccess(telegram_user_id=user_id)
                    session.add(record)
                record.username = username
                record.full_name = full_name
                record.status = STATUS_APPROVED
                record.requested_at = record.requested_at or now
                record.decided_at = now
                await session.commit()
                await session.refresh(record)
                return AccessDecision(
                    allowed=True, is_admin=False, status=STATUS_APPROVED, record=record
                )

            if record is None:
                return AccessDecision(allowed=False, is_admin=False, status=None)

            if record.status == STATUS_APPROVED:
                return AccessDecision(
                    allowed=True, is_admin=False, status=STATUS_APPROVED, record=record
                )

            cooldown_until = None
            if record.status == STATUS_REJECTED and record.decided_at is not None:
                decided = record.decided_at
                if decided.tzinfo is None:
                    decided = decided.replace(tzinfo=timezone.utc)
                cooldown_until = decided + REJECT_COOLDOWN

            return AccessDecision(
                allowed=False,
                is_admin=False,
                status=record.status,
                record=record,
                cooldown_until=cooldown_until,
            )

    async def create_or_renew_request(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        *,
        user_id: int,
        username: str | None,
        full_name: str | None,
    ) -> BotAccess:
        now = datetime.now(timezone.utc)
        async with session_factory() as session:
            record = await self.get(session, user_id)
            if record is None:
                record = BotAccess(telegram_user_id=user_id)
                session.add(record)
            record.username = username
            record.full_name = full_name
            record.status = STATUS_PENDING
            record.requested_at = now
            record.decided_at = None
            record.decided_by_admin_id = None
            await session.commit()
            await session.refresh(record)
            return record

    async def decide(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        *,
        user_id: int,
        approve: bool,
        admin_id: int,
    ) -> BotAccess | None:
        async with session_factory() as session:
            record = await self.get(session, user_id)
            if record is None:
                return None
            record.status = STATUS_APPROVED if approve else STATUS_REJECTED
            record.decided_at = datetime.now(timezone.utc)
            record.decided_by_admin_id = admin_id
            await session.commit()
            await session.refresh(record)
            return record

    async def ensure_bootstrap_admins(
        self, session_factory: async_sessionmaker[AsyncSession]
    ) -> int:
        """Помечает числовые ID админов/whitelist как approved."""
        ids = self.settings.admin_user_ids | self.settings.bootstrap_approved_ids
        if not ids:
            return 0
        now = datetime.now(timezone.utc)
        created = 0
        async with session_factory() as session:
            for user_id in ids:
                record = await self.get(session, user_id)
                if record is None:
                    record = BotAccess(
                        telegram_user_id=user_id,
                        status=STATUS_APPROVED,
                        requested_at=now,
                        decided_at=now,
                    )
                    session.add(record)
                    created += 1
                elif record.status != STATUS_APPROVED:
                    record.status = STATUS_APPROVED
                    record.decided_at = now
                    created += 1
            await session.commit()
        return created


def format_cooldown(until: datetime | None) -> str:
    if until is None:
        return "через сутки"
    now = datetime.now(timezone.utc)
    if until.tzinfo is None:
        until = until.replace(tzinfo=timezone.utc)
    remaining = until - now
    if remaining.total_seconds() <= 0:
        return "сейчас"
    hours = int(remaining.total_seconds() // 3600)
    minutes = int((remaining.total_seconds() % 3600) // 60)
    if hours > 0:
        return f"через {hours} ч {minutes} мин"
    return f"через {minutes} мин"
