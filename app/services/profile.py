from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.db.models import UserProfile
from app.services.crypto import SecretBox
from app.services.runtime import RuntimeConfig
from app.services.settings_form import merge_section
from app.services.settings_probe import probe_cdek, probe_dadata


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

    def credentials_complete(self, profile: UserProfile | None) -> bool:
        if profile is None:
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

    def is_ready(self, profile: UserProfile | None) -> bool:
        if not profile or not profile.setup_completed:
            return False
        return self.credentials_complete(profile)

    def plain_secrets(self, profile: UserProfile | None) -> dict:
        """Расшифрованные значения. Не отдавать наружу."""
        if profile is None:
            return {
                "cdek_client_id": "",
                "cdek_client_secret": "",
                "cdek_test_mode": False,
                "cdek_shipment_point": "",
                "cdek_order_type": 1,
                "dadata_api_key": "",
                "dadata_secret_key": "",
                "sender_name": "",
                "sender_phone": "",
                "item_name": "Бижутерия",
                "weight_g": 100,
                "length_cm": 10,
                "width_cm": 10,
                "height_cm": 5,
                "item_ware_key": "JEWELRY-1",
            }
        cfg = self.to_runtime(profile)
        return {
            "cdek_client_id": cfg.cdek_client_id,
            "cdek_client_secret": cfg.cdek_client_secret,
            "cdek_test_mode": cfg.cdek_test_mode,
            "cdek_shipment_point": cfg.cdek_shipment_point,
            "cdek_order_type": cfg.cdek_order_type,
            "dadata_api_key": cfg.dadata_api_key,
            "dadata_secret_key": cfg.dadata_secret_key,
            "sender_name": cfg.cdek_sender_name,
            "sender_phone": cfg.cdek_sender_phone,
            "item_name": cfg.default_item_name,
            "weight_g": cfg.default_weight_g,
            "length_cm": cfg.default_length_cm,
            "width_cm": cfg.default_width_cm,
            "height_cm": cfg.default_height_cm,
            "item_ware_key": cfg.default_item_ware_key,
        }

    def public_view(self, profile: UserProfile | None) -> dict:
        plain = self.plain_secrets(profile)
        return {
            "ready": self.is_ready(profile),
            "cdek_client_id_mask": _mask(plain["cdek_client_id"]),
            "cdek_client_id_set": bool(plain["cdek_client_id"]),
            "cdek_client_secret_set": bool(plain["cdek_client_secret"]),
            "cdek_test_mode": bool(plain["cdek_test_mode"]),
            "cdek_shipment_point": plain["cdek_shipment_point"],
            "cdek_order_type": int(plain["cdek_order_type"] or 1),
            "dadata_api_key_mask": _mask(plain["dadata_api_key"]),
            "dadata_api_key_set": bool(plain["dadata_api_key"]),
            "dadata_secret_set": bool(plain["dadata_secret_key"]),
            "sender_name": plain["sender_name"],
            "sender_phone": plain["sender_phone"],
            "item_name": plain["item_name"],
            "weight_g": int(plain["weight_g"]),
            "length_cm": int(plain["length_cm"]),
            "width_cm": int(plain["width_cm"]),
            "height_cm": int(plain["height_cm"]),
        }

    async def update_section(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        telegram_user_id: int,
        section: str,
        body: dict,
    ) -> tuple[dict, dict[str, list[str]]]:
        """
        Сохраняет одну секцию. Пустые секреты не затирают старые.
        При ошибке проверки ничего не пишет.
        """
        async with session_factory() as session:
            profile = await self.get(session, telegram_user_id)
            current = self.plain_secrets(profile)
            updates, errors = merge_section(section, current, body)
            if errors:
                return self.public_view(profile), {section: errors}

            merged = {**current, **updates}
            probe_error = await self._probe_section(section, merged)
            if probe_error:
                return self.public_view(profile), {section: [probe_error]}

            if profile is None:
                profile = UserProfile(
                    telegram_user_id=telegram_user_id,
                    cdek_test_mode=False,
                    cdek_order_type=1,
                    weight_g=100,
                    length_cm=10,
                    width_cm=10,
                    height_cm=5,
                    item_name="Бижутерия",
                    item_ware_key="JEWELRY-1",
                    setup_completed=False,
                )
                session.add(profile)
            self._apply_updates(profile, updates)
            profile.setup_completed = self.credentials_complete(profile)
            await session.commit()
            await session.refresh(profile)
            return self.public_view(profile), {}

    async def _probe_section(self, section: str, merged: dict) -> str | None:
        if section == "cdek":
            return await probe_cdek(
                client_id=str(merged.get("cdek_client_id") or ""),
                client_secret=str(merged.get("cdek_client_secret") or ""),
                test_mode=bool(merged.get("cdek_test_mode")),
                shipment_point=str(merged.get("cdek_shipment_point") or ""),
            )
        if section == "dadata":
            return await probe_dadata(
                api_key=str(merged.get("dadata_api_key") or ""),
                secret_key=str(merged.get("dadata_secret_key") or ""),
            )
        return None

    def _apply_updates(self, profile: UserProfile, updates: dict) -> None:
        if "cdek_client_id" in updates:
            profile.cdek_client_id_enc = self._box.encrypt(updates["cdek_client_id"])
        if "cdek_client_secret" in updates:
            profile.cdek_client_secret_enc = self._box.encrypt(updates["cdek_client_secret"])
        if "dadata_api_key" in updates:
            profile.dadata_api_key_enc = self._box.encrypt(updates["dadata_api_key"])
        if "dadata_secret_key" in updates:
            profile.dadata_secret_key_enc = self._box.encrypt(updates["dadata_secret_key"])
        if "cdek_test_mode" in updates:
            profile.cdek_test_mode = bool(updates["cdek_test_mode"])
        if "cdek_shipment_point" in updates:
            profile.cdek_shipment_point = str(updates["cdek_shipment_point"]).strip().upper()
        if "cdek_order_type" in updates:
            profile.cdek_order_type = int(updates["cdek_order_type"])
        if "sender_name" in updates:
            profile.sender_name = str(updates["sender_name"]).strip()
        if "sender_phone" in updates:
            profile.sender_phone = str(updates["sender_phone"]).strip()
        if "item_name" in updates:
            profile.item_name = str(updates["item_name"]).strip() or "Бижутерия"
        if "weight_g" in updates:
            profile.weight_g = int(updates["weight_g"])
        if "length_cm" in updates:
            profile.length_cm = int(updates["length_cm"])
        if "width_cm" in updates:
            profile.width_cm = int(updates["width_cm"])
        if "height_cm" in updates:
            profile.height_cm = int(updates["height_cm"])
        if not profile.item_ware_key:
            profile.item_ware_key = "JEWELRY-1"

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
