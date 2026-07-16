from __future__ import annotations

from aiogram.types import CallbackQuery, Message
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.bot.handlers.setup import start_setup
from app.services.cdek import CdekClient
from app.services.dadata import DaDataClient
from app.services.profile import ProfileService, load_runtime_for_user
from app.services.runtime import RuntimeConfig


async def ensure_runtime(
    event: Message | CallbackQuery,
    *,
    state,
    profiles: ProfileService,
    session_factory: async_sessionmaker[AsyncSession],
    overrides: dict | None = None,
) -> tuple[RuntimeConfig, CdekClient, DaDataClient] | None:
    user = event.from_user
    user_id = user.id if user else 0
    cfg = await load_runtime_for_user(session_factory, profiles, user_id, overrides)
    if cfg is None:
        msg = event if isinstance(event, Message) else event.message
        if msg is None:
            return None
        await msg.answer(
            "Сначала нужно пройти начальную настройку — укажите секреты и параметры отправки."
        )
        await start_setup(msg, state)
        return None
    return cfg, CdekClient(cfg), DaDataClient(cfg)


def order_overrides_from_state(data: dict) -> dict:
    mapping = {
        "override_weight_g": "default_weight_g",
        "override_length_cm": "default_length_cm",
        "override_width_cm": "default_width_cm",
        "override_height_cm": "default_height_cm",
        "override_sender_name": "cdek_sender_name",
        "override_sender_phone": "cdek_sender_phone",
        "override_shipment_point": "cdek_shipment_point",
        "override_item_name": "default_item_name",
    }
    out: dict = {}
    for src, dst in mapping.items():
        if src in data and data[src] is not None:
            out[dst] = data[src]
    return out


def effective_package(cfg: RuntimeConfig, data: dict) -> tuple[int, int, int, int]:
    return (
        int(data.get("override_weight_g") or cfg.default_weight_g),
        int(data.get("override_length_cm") or cfg.default_length_cm),
        int(data.get("override_width_cm") or cfg.default_width_cm),
        int(data.get("override_height_cm") or cfg.default_height_cm),
    )


def effective_item_name(cfg: RuntimeConfig, data: dict) -> str:
    return str(data.get("override_item_name") or cfg.default_item_name)


def build_order_summary(cfg: RuntimeConfig, data: dict, cost: float) -> str:
    tariff = data["chosen_tariff"]
    pvz = data.get("delivery_point")
    pvz_addr = data.get("delivery_point_address")
    address = data.get("clean_address") or data.get("raw_address") or "—"
    if pvz:
        dest = f"{address}\nПВЗ получения: <code>{pvz}</code>"
        if pvz_addr:
            dest += f" — {pvz_addr}"
    else:
        dest = address

    weight, length, width, height = effective_package(cfg, data)
    sender_name = data.get("override_sender_name") or cfg.cdek_sender_name
    sender_phone = data.get("override_sender_phone") or cfg.cdek_sender_phone
    shipment = data.get("override_shipment_point") or cfg.cdek_shipment_point
    item_name = effective_item_name(cfg, data)

    return (
        "<b>Проверьте заказ</b>\n\n"
        f"Куда: {dest}\n"
        f"Тариф: {tariff['tariff_name']} — {tariff['delivery_sum']:.0f} ₽\n"
        f"Получатель: {data['recipient_name']}, {data['recipient_phone']}\n"
        f"Товар: {item_name}, cost={cost:.0f} ₽, НП=0\n"
        f"Место: {weight} г, {length}×{width}×{height} см\n"
        f"Отправитель / seller: {sender_name}, {sender_phone}\n"
        f"Отгрузка: ПВЗ {shipment}\n"
        f"Номер будет вида <code>2026-000001</code>"
    )
