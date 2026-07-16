from __future__ import annotations

import logging
import re
from dataclasses import dataclass

import httpx

from app.services.runtime import RuntimeConfig

logger = logging.getLogger(__name__)

_PREFIX_RE = re.compile(
    r"^(г\.?|гор\.?|город|пгт\.?|пос\.?|п\.?|с\.?|село|д\.?|деревня|рп\.?)\s+",
    re.IGNORECASE,
)


def normalize_locality(name: str | None) -> str:
    """Убирает тип населённого пункта: 'г Санкт-Петербург' → 'Санкт-Петербург'."""
    if not name:
        return ""
    value = name.strip()
    value = _PREFIX_RE.sub("", value).strip()
    return value


@dataclass
class CleanAddress:
    source: str
    result: str
    postal_code: str | None
    region: str | None
    region_type: str | None
    city: str | None
    city_fias_id: str | None
    settlement: str | None
    street: str | None
    house: str | None
    flat: str | None
    geo_lat: str | None
    geo_lon: str | None
    qc: int | None

    @property
    def display(self) -> str:
        return self.result or self.source

    @property
    def city_name(self) -> str:
        """
        Город/НП для поиска в СДЭК.
        Для Москвы/СПб/Севастополя DaData кладёт название в region, city пустой.
        """
        if self.city:
            return normalize_locality(self.city)
        if self.settlement:
            return normalize_locality(self.settlement)
        # город-регион (г / город)
        region_type = (self.region_type or "").lower().rstrip(".")
        if region_type in {"г", "гор", "город"} or (
            self.region and self.region.lower().startswith(("г ", "г.", "город "))
        ):
            return normalize_locality(self.region)
        # fallback: если в result есть федеральный город
        result = (self.result or "").lower()
        for name in ("санкт-петербург", "москва", "севастополь"):
            if name in result:
                return name.title() if name != "санкт-петербург" else "Санкт-Петербург"
        return normalize_locality(self.region)


class DaDataClient:
    def __init__(self, settings: RuntimeConfig) -> None:
        self._api_key = settings.dadata_api_key
        self._secret = settings.dadata_secret_key
        self._clean_url = "https://cleaner.dadata.ru/api/v1/clean/address"

    async def clean_address(self, raw: str) -> CleanAddress:
        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json",
            "Authorization": f"Token {self._api_key}",
            "X-Secret": self._secret,
        }
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.post(self._clean_url, headers=headers, json=[raw])
            response.raise_for_status()
            data = response.json()

        if not data:
            raise ValueError("DaData вернула пустой ответ")

        item = data[0]
        return CleanAddress(
            source=item.get("source") or raw,
            result=item.get("result") or raw,
            postal_code=item.get("postal_code"),
            region=item.get("region_with_type") or item.get("region"),
            region_type=item.get("region_type") or item.get("region_type_full"),
            city=item.get("city_with_type") or item.get("city"),
            city_fias_id=item.get("city_fias_id") or item.get("settlement_fias_id"),
            settlement=item.get("settlement_with_type") or item.get("settlement"),
            street=item.get("street_with_type") or item.get("street"),
            house=item.get("house"),
            flat=item.get("flat"),
            geo_lat=item.get("geo_lat"),
            geo_lon=item.get("geo_lon"),
            qc=item.get("qc"),
        )
