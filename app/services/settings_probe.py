"""Живая проверка ключей перед сохранением секции настроек."""

from __future__ import annotations

import logging

import httpx

from app.services.cdek import CdekClient
from app.services.dadata import DaDataClient
from app.services.runtime import RuntimeConfig

logger = logging.getLogger(__name__)

_PROBE_ADDRESS = "Москва, Красная площадь, 1"


def _runtime(**kwargs: object) -> RuntimeConfig:
    base = dict(
        cdek_client_id="probe",
        cdek_client_secret="probe",
        cdek_test_mode=False,
        cdek_shipment_point="PRM17",
        cdek_order_type=1,
        cdek_sender_name="probe",
        cdek_sender_phone="+79000000000",
        dadata_api_key="probe",
        dadata_secret_key="probe",
        default_weight_g=100,
        default_length_cm=10,
        default_width_cm=10,
        default_height_cm=5,
        default_item_name="probe",
        default_item_ware_key="ITEM-1",
    )
    base.update(kwargs)
    return RuntimeConfig(**base)  # type: ignore[arg-type]


async def probe_cdek(
    *,
    client_id: str,
    client_secret: str,
    test_mode: bool,
    shipment_point: str,
) -> str | None:
    client = CdekClient(
        _runtime(
            cdek_client_id=client_id,
            cdek_client_secret=client_secret,
            cdek_test_mode=test_mode,
            cdek_shipment_point=shipment_point,
        )
    )
    try:
        point = await client.get_delivery_point_by_code(shipment_point)
    except httpx.HTTPStatusError as exc:
        code = exc.response.status_code
        if code in {400, 401, 403}:
            return "СДЭК не принял ключи. Проверьте CLIENT_ID, SECRET и режим тест/prod."
        logger.warning("CDEK probe HTTP %s", code)
        return f"СДЭК ответил ошибкой {code}"
    except Exception:
        logger.exception("CDEK probe failed")
        return "Не удалось связаться со СДЭК"
    if point is None:
        return f"ПВЗ {shipment_point} не найден в СДЭК"
    return None


async def probe_dadata(*, api_key: str, secret_key: str) -> str | None:
    client = DaDataClient(
        _runtime(dadata_api_key=api_key, dadata_secret_key=secret_key)
    )
    try:
        clean = await client.clean_address(_PROBE_ADDRESS)
    except httpx.HTTPStatusError as exc:
        code = exc.response.status_code
        if code in {401, 403}:
            return "DaData не приняла ключи"
        logger.warning("DaData probe HTTP %s", code)
        return f"DaData ответила ошибкой {code}"
    except Exception:
        logger.exception("DaData probe failed")
        return "Не удалось связаться с DaData"
    if not (clean.result or "").strip():
        return "DaData не распознала проверочный адрес"
    return None
