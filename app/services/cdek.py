from __future__ import annotations

import asyncio
import logging
import math
import re
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx

from app.config import Settings

logger = logging.getLogger(__name__)

_STREET_NOISE_RE = re.compile(
    r"\b(ул\.?|улица|пр-кт|проспект|пер\.?|переулок|б-р|бульвар|ш\.?|шоссе|"
    r"наб\.?|набережная|пл\.?|площадь|ал\.?|аллея|проезд|туп\.?|тупик)\b",
    re.IGNORECASE,
)
_HOUSE_RE = re.compile(r"(\d+[a-zа-я]?)", re.IGNORECASE)


@dataclass
class TariffOption:
    tariff_code: int
    tariff_name: str
    delivery_mode: int
    delivery_sum: float
    period_min: int
    period_max: int

    @property
    def mode_label(self) -> str:
        modes = {
            1: "дверь-дверь",
            2: "дверь-склад",
            3: "склад-дверь",
            4: "склад-склад",
        }
        return modes.get(self.delivery_mode, f"режим {self.delivery_mode}")

    @property
    def to_pvz(self) -> bool:
        # режимы 2 и 4 — до склада/ПВЗ
        return self.delivery_mode in (2, 4)

    def label(self) -> str:
        return (
            f"{self.tariff_name} — {self.delivery_sum:.0f} ₽, "
            f"{self.period_min}–{self.period_max} дн. ({self.mode_label})"
        )


@dataclass
class CityMatch:
    code: int
    city: str
    region: str
    country_code: str = "RU"


@dataclass
class CreatedOrder:
    uuid: str
    our_number: str
    cdek_number: str | None
    status: str


@dataclass
class PickupPoint:
    code: str
    name: str
    address: str
    address_full: str
    city_code: int | None
    latitude: float | None
    longitude: float | None
    work_time: str | None
    point_type: str
    distance_km: float | None = None
    match_kind: str = "nearest"  # exact | nearest

    def button_label(self, max_len: int = 56) -> str:
        dist = f" · {self.distance_km:.1f} км" if self.distance_km is not None else ""
        text = f"{self.code}: {self.address}{dist}"
        if len(text) <= max_len:
            return text
        return text[: max_len - 1] + "…"

    def description(self) -> str:
        dist = f" (~{self.distance_km:.1f} км)" if self.distance_km is not None else ""
        work = f"\n🕒 {self.work_time}" if self.work_time else ""
        return f"<b>{self.code}</b> — {self.address}{dist}{work}"


def _haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    r = 6371.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlmb = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dlmb / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


def _normalize_street(value: str | None) -> str:
    if not value:
        return ""
    text = value.lower().replace("ё", "е")
    text = _STREET_NOISE_RE.sub(" ", text)
    text = re.sub(r"[^\w\s]", " ", text, flags=re.UNICODE)
    return re.sub(r"\s+", " ", text).strip()


def _extract_house(value: str | None) -> str:
    if not value:
        return ""
    match = _HOUSE_RE.search(value.replace(" ", ""))
    return (match.group(1) if match else value).lower()


class CdekClient:
    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._base = settings.cdek_base_url
        self._token: str | None = None
        self._token_expires_at: float = 0.0

    async def _ensure_token(self, client: httpx.AsyncClient) -> str:
        if self._token and time.time() < self._token_expires_at - 60:
            return self._token

        # OAuth token endpoint is on the same host without /v2 path suffix nuance:
        # production: https://api.cdek.ru/v2/oauth/token
        url = f"{self._base}/oauth/token"
        response = await client.post(
            url,
            data={
                "grant_type": "client_credentials",
                "client_id": self._settings.cdek_client_id,
                "client_secret": self._settings.cdek_client_secret,
            },
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
        response.raise_for_status()
        payload = response.json()
        self._token = payload["access_token"]
        self._token_expires_at = time.time() + int(payload.get("expires_in", 3600))
        return self._token

    async def _request(
        self,
        method: str,
        path: str,
        *,
        json: dict[str, Any] | None = None,
        params: dict[str, Any] | None = None,
        expect_json: bool = True,
    ) -> Any:
        async with httpx.AsyncClient(timeout=60.0) as client:
            token = await self._ensure_token(client)
            headers = {"Authorization": f"Bearer {token}"}
            if expect_json:
                headers["Content-Type"] = "application/json"
            response = await client.request(
                method,
                f"{self._base}{path}",
                headers=headers,
                json=json,
                params=params,
            )
            if response.status_code >= 400:
                logger.error("CDEK error %s %s: %s", method, path, response.text)
                response.raise_for_status()
            if not expect_json:
                return response.content
            if not response.content:
                return {}
            return response.json()

    async def find_city(self, city: str, region: str | None = None) -> list[CityMatch]:
        from app.services.dadata import normalize_locality

        city = normalize_locality(city)
        region_norm = normalize_locality(region) if region else ""
        params: dict[str, Any] = {"city": city, "size": 5}
        # Для городов-регионов (СПб/Москва) region == city — фильтр мешает поиску
        if region_norm and region_norm.casefold() != city.casefold():
            params["region"] = region_norm
        data = await self._request("GET", "/location/cities", params=params)
        matches: list[CityMatch] = []
        for item in data or []:
            matches.append(
                CityMatch(
                    code=int(item["code"]),
                    city=item.get("city") or city,
                    region=item.get("region") or "",
                    country_code=item.get("country_code") or "RU",
                )
            )
        return matches

    async def get_delivery_points(
        self,
        *,
        city_code: int,
        point_type: str = "PVZ",
        postal_code: str | None = None,
    ) -> list[PickupPoint]:
        params: dict[str, Any] = {
            "city_code": city_code,
            "type": point_type,
            "is_handout": True,
        }
        if postal_code and postal_code.isdigit():
            params["postal_code"] = int(postal_code)

        data = await self._request("GET", "/deliverypoints", params=params)
        # API может вернуть список или обёртку
        items = data if isinstance(data, list) else (data.get("deliverypoints") or data.get("items") or [])
        points: list[PickupPoint] = []
        for item in items or []:
            location = item.get("location") or {}
            lat = location.get("latitude")
            lon = location.get("longitude")
            if lat is None and isinstance(location.get("coordinates"), dict):
                lat = location["coordinates"].get("latitude")
                lon = location["coordinates"].get("longitude")
            address = location.get("address") or item.get("address") or ""
            address_full = location.get("address_full") or address
            points.append(
                PickupPoint(
                    code=str(item.get("code") or ""),
                    name=str(item.get("name") or item.get("code") or ""),
                    address=address,
                    address_full=address_full,
                    city_code=location.get("city_code") or city_code,
                    latitude=float(lat) if lat is not None else None,
                    longitude=float(lon) if lon is not None else None,
                    work_time=item.get("work_time"),
                    point_type=str(item.get("type") or point_type),
                )
            )
        return [p for p in points if p.code]

    async def find_pvz_for_address(
        self,
        *,
        city_code: int,
        address: str,
        street: str | None = None,
        house: str | None = None,
        postal_code: str | None = None,
        latitude: float | None = None,
        longitude: float | None = None,
        limit: int = 8,
    ) -> tuple[list[PickupPoint], str]:
        """
        Ищет ПВЗ по адресу. Сначала совпадения по улице/дому,
        если нет — ближайшие по координатам.
        Возвращает (список, режим: exact|nearest|none).
        """
        points = await self.get_delivery_points(city_code=city_code, point_type="PVZ")
        if not points and postal_code:
            points = await self.get_delivery_points(
                city_code=city_code, point_type="PVZ", postal_code=postal_code
            )
        if not points:
            # постаматы как запасной вариант
            points = await self.get_delivery_points(city_code=city_code, point_type="ALL")

        if latitude is not None and longitude is not None:
            for p in points:
                if p.latitude is not None and p.longitude is not None:
                    p.distance_km = _haversine_km(latitude, longitude, p.latitude, p.longitude)

        street_n = _normalize_street(street) or _normalize_street(address)
        house_n = _extract_house(house)

        exact: list[PickupPoint] = []
        if street_n:
            for p in points:
                hay = _normalize_street(f"{p.address_full} {p.address} {p.name}")
                if street_n and street_n in hay:
                    if house_n:
                        # дом есть в адресе ПВЗ или рядом (тот же дом/литера)
                        if house_n in hay.replace(" ", ""):
                            p.match_kind = "exact"
                            exact.append(p)
                    else:
                        p.match_kind = "exact"
                        exact.append(p)

        if exact:
            exact.sort(key=lambda p: (p.distance_km is None, p.distance_km or 0.0))
            return exact[:limit], "exact"

        if latitude is not None and longitude is not None:
            with_coords = [p for p in points if p.distance_km is not None]
            with_coords.sort(key=lambda p: p.distance_km or 0.0)
            for p in with_coords:
                p.match_kind = "nearest"
            if with_coords:
                return with_coords[:limit], "nearest"

        # без координат — просто первые по городу
        for p in points[:limit]:
            p.match_kind = "nearest"
        return points[:limit], ("nearest" if points else "none")

    async def calculate_tariffs(
        self,
        *,
        to_city_code: int,
        weight: int,
        length: int,
        width: int,
        height: int,
        from_city_code: int | None = None,
    ) -> list[TariffOption]:
        body: dict[str, Any] = {
            "type": self._settings.cdek_order_type,
            "from_location": {},
            "to_location": {"code": to_city_code},
            "packages": [
                {
                    "weight": weight,
                    "length": length,
                    "width": width,
                    "height": height,
                }
            ],
        }
        # Отправка всегда с ПВЗ: указываем shipment через from_location города ПВЗ
        # либо через код города склада. Если задан только shipment_point,
        # CDEK сам привяжет склад при создании заказа; для калькулятора нужен город.
        if from_city_code:
            body["from_location"] = {"code": from_city_code}
        else:
            # Без кода города from_location всё равно обязателен по контракту —
            # используем code из ПВЗ через отдельный lookup; fallback Москва.
            body["from_location"] = {"code": 44}

        data = await self._request("POST", "/calculator/tarifflist", json=body)
        tariffs: list[TariffOption] = []
        for item in data.get("tariff_codes") or []:
            tariffs.append(
                TariffOption(
                    tariff_code=int(item["tariff_code"]),
                    tariff_name=item.get("tariff_name") or str(item["tariff_code"]),
                    delivery_mode=int(item.get("delivery_mode") or 0),
                    delivery_sum=float(item.get("delivery_sum") or 0),
                    period_min=int(item.get("period_min") or 0),
                    period_max=int(item.get("period_max") or 0),
                )
            )
        tariffs.sort(key=lambda t: t.delivery_sum)
        return tariffs

    async def create_order(self, payload: dict[str, Any]) -> CreatedOrder:
        data = await self._request("POST", "/orders", json=payload)
        entity = data.get("entity") or {}
        uuid = entity.get("uuid")
        if not uuid:
            requests_info = data.get("requests") or []
            errors = []
            for req in requests_info:
                for err in req.get("errors") or []:
                    errors.append(err.get("message") or str(err))
            msg = "; ".join(errors) if errors else str(data)
            raise RuntimeError(f"Не удалось создать заказ: {msg}")

        our_number = payload.get("number") or ""
        return CreatedOrder(
            uuid=uuid,
            our_number=our_number,
            cdek_number=None,
            status="created",
        )

    async def get_order(self, order_uuid: str) -> dict[str, Any]:
        data = await self._request("GET", f"/orders/{order_uuid}")
        return data.get("entity") or {}

    async def wait_order_ready(
        self, order_uuid: str, *, attempts: int = 15, delay: float = 2.0
    ) -> dict[str, Any]:
        last: dict[str, Any] = {}
        for _ in range(attempts):
            last = await self.get_order(order_uuid)
            statuses = last.get("statuses") or []
            codes = {s.get("code") for s in statuses}
            # ACCEPTED / CREATED — можно печатать
            if "ACCEPTED" in codes or "CREATED" in codes or last.get("cdek_number"):
                return last
            await asyncio.sleep(delay)
        return last

    async def _create_print(
        self, path: str, order_uuid: str, *, format: str = "A4", copy_count: int = 1
    ) -> str:
        data = await self._request(
            "POST",
            path,
            json={
                "orders": [{"order_uuid": order_uuid}],
                "copy_count": copy_count,
                "format": format,
            },
        )
        entity = data.get("entity") or {}
        print_uuid = entity.get("uuid")
        if not print_uuid:
            raise RuntimeError(f"Не получен uuid печатной формы: {data}")
        return print_uuid

    async def _download_print_pdf(
        self, get_path_template: str, print_uuid: str, *, attempts: int = 20, delay: float = 1.5
    ) -> bytes:
        path = get_path_template.format(uuid=print_uuid)
        last_error = "timeout"
        for _ in range(attempts):
            try:
                # статус формы
                meta = await self._request("GET", path)
                entity = meta.get("entity") or {}
                url = entity.get("url")
                statuses = entity.get("statuses") or []
                codes = {s.get("code") for s in statuses}
                if "INVALID" in codes or "REMOVED" in codes:
                    raise RuntimeError(f"Печатная форма недоступна: {statuses}")
                if url:
                    # url может быть относительным или абсолютным
                    if url.startswith("http"):
                        async with httpx.AsyncClient(timeout=60.0) as client:
                            token = await self._ensure_token(client)
                            resp = await client.get(
                                url, headers={"Authorization": f"Bearer {token}"}
                            )
                            resp.raise_for_status()
                            return resp.content
                    return await self._request("GET", url if url.startswith("/") else f"/{url}", expect_json=False)
                # иногда PDF отдаётся сразу по тому же uuid
                content = await self._request(
                    "GET", f"{path}.pdf", expect_json=False
                )
                if content and content[:4] == b"%PDF":
                    return content
            except httpx.HTTPStatusError as exc:
                last_error = str(exc)
                if exc.response.status_code not in (404, 202, 204):
                    raise
            await asyncio.sleep(delay)
        raise RuntimeError(f"Не удалось скачать PDF: {last_error}")

    async def download_waybill_pdf(self, order_uuid: str, dest: Path) -> Path:
        print_uuid = await self._create_print("/print/orders", order_uuid, format="A4")
        pdf = await self._download_print_pdf("/print/orders/{uuid}", print_uuid)
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(pdf)
        return dest

    async def download_barcode_pdf(self, order_uuid: str, dest: Path) -> Path:
        print_uuid = await self._create_print("/print/barcodes", order_uuid, format="A4")
        pdf = await self._download_print_pdf("/print/barcodes/{uuid}", print_uuid)
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(pdf)
        return dest

    def build_order_payload(
        self,
        *,
        our_number: str,
        tariff_code: int,
        to_city_code: int,
        to_address: str,
        recipient_name: str,
        recipient_phone: str,
        item_cost: float,
        delivery_point: str | None = None,
        weight: int | None = None,
        length: int | None = None,
        width: int | None = None,
        height: int | None = None,
    ) -> dict[str, Any]:
        s = self._settings
        weight = weight if weight is not None else s.default_weight_g
        length = length if length is not None else s.default_length_cm
        width = width if width is not None else s.default_width_cm
        height = height if height is not None else s.default_height_cm

        payload: dict[str, Any] = {
            "type": s.cdek_order_type,
            "number": our_number,
            "tariff_code": tariff_code,
            "shipment_point": s.cdek_shipment_point,
            "sender": {
                "name": s.cdek_sender_name,
                "phones": [{"number": s.cdek_sender_phone}],
            },
            "recipient": {
                "name": recipient_name,
                "phones": [{"number": recipient_phone}],
            },
            "packages": [
                {
                    "number": "1",
                    "weight": weight,
                    "length": length,
                    "width": width,
                    "height": height,
                    "items": [
                        {
                            "name": s.default_item_name,
                            "ware_key": s.default_item_ware_key,
                            "payment": {"value": 0},
                            "cost": float(item_cost),
                            "weight": weight,
                            "amount": 1,
                        }
                    ],
                }
            ],
        }

        if delivery_point:
            payload["delivery_point"] = delivery_point
        else:
            payload["to_location"] = {
                "code": to_city_code,
                "address": to_address,
            }

        return payload
