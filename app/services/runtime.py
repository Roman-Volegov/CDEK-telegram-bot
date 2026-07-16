from __future__ import annotations

from dataclasses import dataclass, replace


@dataclass
class RuntimeConfig:
    """Рабочие настройки пользователя для СДЭК / DaData / посылки."""

    cdek_client_id: str
    cdek_client_secret: str
    cdek_test_mode: bool
    cdek_shipment_point: str
    cdek_order_type: int
    cdek_sender_name: str
    cdek_sender_phone: str
    dadata_api_key: str
    dadata_secret_key: str
    default_weight_g: int
    default_length_cm: int
    default_width_cm: int
    default_height_cm: int
    default_item_name: str
    default_item_ware_key: str

    @property
    def cdek_base_url(self) -> str:
        if self.cdek_test_mode:
            return "https://api.edu.cdek.ru/v2"
        return "https://api.cdek.ru/v2"

    def with_overrides(self, **kwargs: object) -> RuntimeConfig:
        return replace(self, **kwargs)  # type: ignore[arg-type]
