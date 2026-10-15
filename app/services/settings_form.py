"""Сборка и проверка одной секции настроек Mini App. Секреты не логируем."""

from __future__ import annotations

import re

SECTIONS = frozenset({"cdek", "dadata", "sender", "package"})
PHONE_RE = re.compile(r"^\+?\d{10,15}$")


def normalize_phone(raw: str) -> str:
    digits = re.sub(r"[^\d+]", "", (raw or "").strip())
    if digits.startswith("8") and len(digits) == 11:
        digits = "+7" + digits[1:]
    elif digits.startswith("7") and len(digits) == 11:
        digits = "+" + digits
    elif digits.isdigit() and len(digits) == 10:
        digits = "+7" + digits
    return digits


def merge_section(section: str, current: dict, body: dict) -> tuple[dict, list[str]]:
    """
    Возвращает поля для записи и список ошибок.
    Пустой секрет не затирает уже сохранённый.
    """
    if section not in SECTIONS:
        return {}, ["Неизвестная секция"]
    if section == "cdek":
        return _merge_cdek(current, body)
    if section == "dadata":
        return _merge_dadata(current, body)
    if section == "sender":
        return _merge_sender(body)
    return _merge_package(body)


def _text(body: dict, key: str) -> str:
    value = body.get(key)
    if value is None:
        return ""
    return str(value).strip()


def _as_bool(value: object, default: bool) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "on", "да"}
    return default


def _merge_cdek(current: dict, body: dict) -> tuple[dict, list[str]]:
    errors: list[str] = []
    client_id = _text(body, "cdek_client_id")
    secret = _text(body, "cdek_client_secret")
    point = _text(body, "cdek_shipment_point").upper()
    test_mode = _as_bool(body.get("cdek_test_mode"), bool(current.get("cdek_test_mode")))

    order_type = int(current.get("cdek_order_type") or 1)
    if body.get("cdek_order_type") not in (None, ""):
        try:
            order_type = int(body.get("cdek_order_type"))
            if order_type <= 0:
                raise ValueError
        except (TypeError, ValueError):
            errors.append("Тип заказа должен быть положительным числом")

    if len(point) < 3:
        errors.append("Укажите код ПВЗ отгрузки, например PRM17")
    if client_id and len(client_id) < 5:
        errors.append("CLIENT_ID слишком короткий")
    if secret and len(secret) < 5:
        errors.append("CLIENT_SECRET слишком короткий")

    final_id = client_id or str(current.get("cdek_client_id") or "")
    final_secret = secret or str(current.get("cdek_client_secret") or "")
    if not final_id:
        errors.append("Укажите CDEK CLIENT_ID")
    if not final_secret:
        errors.append("Укажите CDEK CLIENT_SECRET")

    updates: dict = {
        "cdek_test_mode": test_mode,
        "cdek_shipment_point": point,
        "cdek_order_type": order_type,
    }
    if client_id:
        updates["cdek_client_id"] = client_id
    if secret:
        updates["cdek_client_secret"] = secret
    return updates, errors


def _merge_dadata(current: dict, body: dict) -> tuple[dict, list[str]]:
    errors: list[str] = []
    api_key = _text(body, "dadata_api_key")
    secret = _text(body, "dadata_secret_key")
    if api_key and len(api_key) < 8:
        errors.append("API-ключ DaData слишком короткий")
    if secret and len(secret) < 8:
        errors.append("Secret DaData слишком короткий")
    final_key = api_key or str(current.get("dadata_api_key") or "")
    final_secret = secret or str(current.get("dadata_secret_key") or "")
    if not final_key:
        errors.append("Укажите DADATA API KEY")
    if not final_secret:
        errors.append("Укажите DADATA SECRET KEY")
    updates: dict = {}
    if api_key:
        updates["dadata_api_key"] = api_key
    if secret:
        updates["dadata_secret_key"] = secret
    return updates, errors


def _merge_sender(body: dict) -> tuple[dict, list[str]]:
    errors: list[str] = []
    name = _text(body, "sender_name")
    phone = normalize_phone(_text(body, "sender_phone"))
    if len(name) < 3:
        errors.append("Укажите ФИО отправителя")
    if not PHONE_RE.match(phone):
        errors.append("Телефон в формате +79001234567")
    return {"sender_name": name, "sender_phone": phone}, errors


def _positive_int(body: dict, key: str, label: str, errors: list[str]) -> int | None:
    raw = body.get(key)
    try:
        value = int(float(str(raw).replace(",", ".")))
        if value <= 0:
            raise ValueError
    except (TypeError, ValueError):
        errors.append(f"{label} должно быть числом больше нуля")
        return None
    return value


def _merge_package(body: dict) -> tuple[dict, list[str]]:
    errors: list[str] = []
    name = _text(body, "item_name")
    if len(name) < 2:
        errors.append("Укажите название товара")
    weight = _positive_int(body, "weight_g", "Вес", errors)
    length = _positive_int(body, "length_cm", "Длина", errors)
    width = _positive_int(body, "width_cm", "Ширина", errors)
    height = _positive_int(body, "height_cm", "Высота", errors)
    updates: dict = {"item_name": name or "Бижутерия"}
    if weight is not None:
        updates["weight_g"] = weight
    if length is not None:
        updates["length_cm"] = length
    if width is not None:
        updates["width_cm"] = width
    if height is not None:
        updates["height_cm"] = height
    return updates, errors
