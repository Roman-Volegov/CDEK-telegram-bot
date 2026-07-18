from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup, KeyboardButton, ReplyKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder

from app.services.cdek import PickupPoint, TariffOption


def main_menu() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="📦 Рассчитать"), KeyboardButton(text="🚚 Создать заказ")],
            [KeyboardButton(text="📋 Мои заказы"), KeyboardButton(text="💳 Оплатить за СДЭК")],
            [KeyboardButton(text="⚙️ Настройки"), KeyboardButton(text="ℹ️ Помощь")],
        ],
        resize_keyboard=True,
    )


def payment_confirm_kb() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(text="✅ Подтвердить оплату", callback_data="payment:confirm"),
        InlineKeyboardButton(text="❌ Отменить", callback_data="payment:cancel"),
    )
    return builder.as_markup()


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


def history_page_kb(
    page: int,
    total: int,
    order_ids: list[int],
    *,
    page_size: int = 3,
) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    for idx, order_id in enumerate(order_ids, start=1):
        builder.row(
            InlineKeyboardButton(
                text=f"Открыть {idx}",
                callback_data=f"hist:view:{order_id}:{page}",
            )
        )
    nav: list[InlineKeyboardButton] = []
    if page > 0:
        nav.append(
            InlineKeyboardButton(
                text="◀️ Предыдущие 3",
                callback_data=f"hist:page:{page - 1}",
            )
        )
    if (page + 1) * page_size < total:
        nav.append(
            InlineKeyboardButton(
                text="Следующие 3 ▶️",
                callback_data=f"hist:page:{page + 1}",
            )
        )
    if nav:
        builder.row(*nav)
    return builder.as_markup()


def history_order_kb(
    order_id: int,
    *,
    editable: bool,
    has_pdfs: bool,
    page: int = 0,
    can_fetch_pdf: bool = False,
) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    if editable:
        builder.row(
            InlineKeyboardButton(text="✏️ Редактировать", callback_data=f"hist:edit:{order_id}"),
            InlineKeyboardButton(text="🚫 Отменить", callback_data=f"hist:cancel:{order_id}"),
        )
        builder.row(
            InlineKeyboardButton(text="🗑 Удалить", callback_data=f"hist:delete:{order_id}"),
        )
    if has_pdfs or can_fetch_pdf:
        label = "📄 PDF" if has_pdfs else "📄 Выгрузить PDF"
        builder.row(
            InlineKeyboardButton(text=label, callback_data=f"hist:pdf:{order_id}"),
        )
    builder.row(
        InlineKeyboardButton(text="⬅️ К списку", callback_data=f"hist:page:{page}"),
    )
    return builder.as_markup()
