from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup, KeyboardButton, ReplyKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder

from app.services.cdek import PickupPoint, TariffOption


def main_menu() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="📦 Рассчитать"), KeyboardButton(text="🚚 Создать заказ")],
            [KeyboardButton(text="📋 Мои заказы"), KeyboardButton(text="ℹ️ Помощь")],
        ],
        resize_keyboard=True,
    )


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
        InlineKeyboardButton(text="❌ Отмена", callback_data="order:cancel"),
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
