from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.db.models import UserProfile
from app.services.crypto import SecretBox
from app.services.runtime import RuntimeConfig


@dataclass
class ProfileDraft:
    cdek_client_id: str = ""
    cdek_client_secret: str = ""
    dadata_api_key: str = ""
    dadata_secret_key: str = ""
    cdek_test_mode: bool = False
    cdek_shipment_point: str = ""
    cdek_order_type: int = 1
    sender_name: str = ""
    sender_phone: str = ""
    weight_g: int = 100
    length_cm: int = 10
    width_cm: int = 10
    height_cm: int = 5
    item_name: str = "Бижутерия"
    item_ware_key: str = "JEWELRY-1"


class ProfileService:
    def __init__(self, box: SecretBox) -> None:
        self._box = box

    async def get(
        self, session: AsyncSession, telegram_user_id: int
    ) -> UserProfile | None:
        return await session.get(UserProfile, telegram_user_id)

    async def get_or_none(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        telegram_user_id: int,
    ) -> UserProfile | None:
        async with session_factory() as session:
            return await self.get(session, telegram_user_id)

    def to_runtime(self, profile: UserProfile) -> RuntimeConfig:
        return RuntimeConfig(
            cdek_client_id=self._box.decrypt(profile.cdek_client_id_enc) or "",
            cdek_client_secret=self._box.decrypt(profile.cdek_client_secret_enc) or "",
            cdek_test_mode=bool(profile.cdek_test_mode),
            cdek_shipment_point=profile.cdek_shipment_point or "",
            cdek_order_type=int(profile.cdek_order_type or 1),
            cdek_sender_name=profile.sender_name or "",
            cdek_sender_phone=profile.sender_phone or "",
            dadata_api_key=self._box.decrypt(profile.dadata_api_key_enc) or "",
            dadata_secret_key=self._box.decrypt(profile.dadata_secret_key_enc) or "",
            default_weight_g=int(profile.weight_g),
            default_length_cm=int(profile.length_cm),
            default_width_cm=int(profile.width_cm),
            default_height_cm=int(profile.height_cm),
            default_item_name=profile.item_name or "Бижутерия",
            default_item_ware_key=profile.item_ware_key or "JEWELRY-1",
        )

    def is_ready(self, profile: UserProfile | None) -> bool:
        if not profile or not profile.setup_completed:
            return False
        try:
            cfg = self.to_runtime(profile)
        except ValueError:
            return False
        return bool(
            cfg.cdek_client_id
            and cfg.cdek_client_secret
            and cfg.cdek_shipment_point
            and cfg.cdek_sender_name
            and cfg.cdek_sender_phone
            and cfg.dadata_api_key
            and cfg.dadata_secret_key
        )

    def summary_html(self, profile: UserProfile) -> str:
        cfg = self.to_runtime(profile)
        mode = "тест" if cfg.cdek_test_mode else "prod"
        return (
            "<b>Ваши настройки</b>\n\n"
            f"СДЭК: <code>{_mask(cfg.cdek_client_id)}</code> ({mode})\n"
            f"DaData: <code>{_mask(cfg.dadata_api_key)}</code>\n"
            f"ПВЗ отгрузки: <code>{cfg.cdek_shipment_point}</code>\n"
            f"Отправитель: {cfg.cdek_sender_name}, {cfg.cdek_sender_phone}\n"
            f"Товар: {cfg.default_item_name}\n"
            f"Габариты: {cfg.default_weight_g} г, "
            f"{cfg.default_length_cm}×{cfg.default_width_cm}×{cfg.default_height_cm} см\n"
            f"Тип заказа: {cfg.cdek_order_type} (1=ИМ)"
        )

    async def save_draft(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        telegram_user_id: int,
        draft: ProfileDraft,
    ) -> UserProfile:
        async with session_factory() as session:
            profile = await self.get(session, telegram_user_id)
            if profile is None:
                profile = UserProfile(telegram_user_id=telegram_user_id)
                session.add(profile)

            profile.cdek_client_id_enc = self._box.encrypt(draft.cdek_client_id)
            profile.cdek_client_secret_enc = self._box.encrypt(draft.cdek_client_secret)
            profile.dadata_api_key_enc = self._box.encrypt(draft.dadata_api_key)
            profile.dadata_secret_key_enc = self._box.encrypt(draft.dadata_secret_key)
            profile.cdek_test_mode = draft.cdek_test_mode
            profile.cdek_shipment_point = draft.cdek_shipment_point.strip().upper()
            profile.cdek_order_type = draft.cdek_order_type
            profile.sender_name = draft.sender_name.strip()
            profile.sender_phone = draft.sender_phone.strip()
            profile.weight_g = draft.weight_g
            profile.length_cm = draft.length_cm
            profile.width_cm = draft.width_cm
            profile.height_cm = draft.height_cm
            profile.item_name = draft.item_name.strip() or "Бижутерия"
            profile.item_ware_key = draft.item_ware_key.strip() or "JEWELRY-1"
            profile.setup_completed = True
            await session.commit()
            await session.refresh(profile)
            return profile


def _mask(value: str, keep: int = 4) -> str:
    if not value:
        return "—"
    if len(value) <= keep * 2:
        return "***"
    return f"{value[:keep]}…{value[-keep:]}"


async def load_runtime_for_user(
    session_factory: async_sessionmaker[AsyncSession],
    profiles: ProfileService,
    telegram_user_id: int,
    overrides: dict | None = None,
) -> RuntimeConfig | None:
    async with session_factory() as session:
        profile = await profiles.get(session, telegram_user_id)
        if not profiles.is_ready(profile):
            return None
        assert profile is not None
        cfg = profiles.to_runtime(profile)
    if overrides:
        cfg = cfg.with_overrides(**{k: v for k, v in overrides.items() if v is not None})
    return cfg
