from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup, KeyboardButton, ReplyKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder

from app.services.cdek import PickupPoint, TariffOption


def main_menu() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="📦 Рассчитать"), KeyboardButton(text="🚚 Создать заказ")],
            [KeyboardButton(text="📋 Мои заказы"), KeyboardButton(text="⚙️ Настройки")],
            [KeyboardButton(text="ℹ️ Помощь")],
        ],
        resize_keyboard=True,
    )


def request_access_kb() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="🔑 Запросить доступ")],
            [KeyboardButton(text="ℹ️ Помощь")],
        ],
        resize_keyboard=True,
    )


def admin_access_kb(user_id: int) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(
            text="✅ Подтвердить",
            callback_data=f"access:approve:{user_id}",
        ),
        InlineKeyboardButton(
            text="❌ Отклонить",
            callback_data=f"access:reject:{user_id}",
        ),
    )
    return builder.as_markup()


def confirm_address_kb() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(text="✅ Верно", callback_data="addr:ok"),
        InlineKeyboardButton(text="✏️ Другой адрес", callback_data="addr:retry"),
    )
    return builder.as_markup()


def tariffs_kb(tariffs: list[TariffOption], prefix: str = "tariff") -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    for idx, t in enumerate(tariffs[:10]):
        builder.row(
            InlineKeyboardButton(
                text=f"{t.delivery_sum:.0f} ₽ · {t.tariff_name[:40]}",
                callback_data=f"{prefix}:{idx}",
            )
        )
    builder.row(InlineKeyboardButton(text="❌ Отмена", callback_data=f"{prefix}:cancel"))
    return builder.as_markup()


def confirm_order_kb() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(text="✅ Создать заказ", callback_data="order:create"),
    )
    builder.row(
        InlineKeyboardButton(text="✏️ Изменить параметры", callback_data="order:edit"),
    )
    builder.row(
        InlineKeyboardButton(text="❌ Отмена", callback_data="order:cancel"),
    )
    return builder.as_markup()


def edit_order_kb(*, show_delivery_pvz: bool = True) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(text="📍 Адрес доставки", callback_data="edit:address"),
        InlineKeyboardButton(text="🚚 Тариф", callback_data="edit:tariff"),
    )
    if show_delivery_pvz:
        builder.row(
            InlineKeyboardButton(text="🏬 ПВЗ получения", callback_data="edit:delivery_pvz"),
        )
    builder.row(
        InlineKeyboardButton(text="🧍 Получатель", callback_data="edit:recipient"),
        InlineKeyboardButton(text="💰 Стоимость товара", callback_data="edit:item_cost"),
    )
    builder.row(
        InlineKeyboardButton(text="🏷 Название товара", callback_data="edit:item_name"),
        InlineKeyboardButton(text="📦 Габариты", callback_data="edit:dims"),
    )
    builder.row(
        InlineKeyboardButton(text="👤 Отправитель", callback_data="edit:sender"),
        InlineKeyboardButton(text="📤 ПВЗ отправки", callback_data="edit:shipment"),
    )
    builder.row(
        InlineKeyboardButton(text="⬅️ К подтверждению", callback_data="edit:back"),
    )
    return builder.as_markup()


def yes_no_kb(prefix: str) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(text="🚀 Prod", callback_data=f"{prefix}:no"),
        InlineKeyboardButton(text="🧪 Тест", callback_data=f"{prefix}:yes"),
    )
    return builder.as_markup()


def setup_confirm_kb() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(text="✅ Сохранить", callback_data="setup:save"),
        InlineKeyboardButton(text="🔄 Заново", callback_data="setup:restart"),
    )
    return builder.as_markup()


def pvz_kb(points: list[PickupPoint], prefix: str = "pvz") -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    for idx, point in enumerate(points[:8]):
        builder.row(
            InlineKeyboardButton(
                text=point.button_label(),
                callback_data=f"{prefix}:{idx}",
            )
        )
    builder.row(
        InlineKeyboardButton(text="⌨️ Ввести код вручную", callback_data=f"{prefix}:manual")
    )
    builder.row(InlineKeyboardButton(text="❌ Отмена", callback_data=f"{prefix}:cancel"))
    return builder.as_markup()
