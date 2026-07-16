from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    bot_token: str = Field(alias="BOT_TOKEN")

    # Whitelist: Telegram user id и/или @username через запятую.
    # Пусто = доступ открыт всем.
    # Пример: ALLOWED_TELEGRAM_IDS=123456789,@RomanVolegov,IraVolego
    allowed_telegram_ids: str = Field(default="", alias="ALLOWED_TELEGRAM_IDS")

    cdek_client_id: str = Field(alias="CDEK_CLIENT_ID")
    cdek_client_secret: str = Field(alias="CDEK_CLIENT_SECRET")
    cdek_test_mode: bool = Field(default=True, alias="CDEK_TEST_MODE")
    cdek_shipment_point: str = Field(alias="CDEK_SHIPMENT_POINT")
    # 1 — интернет-магазин, 2 — доставка
    cdek_order_type: int = Field(default=1, alias="CDEK_ORDER_TYPE")

    cdek_sender_name: str = Field(
        default="Волегов Роман Андреевич", alias="CDEK_SENDER_NAME"
    )
    cdek_sender_phone: str = Field(default="+79127814466", alias="CDEK_SENDER_PHONE")

    dadata_api_key: str = Field(alias="DADATA_API_KEY")
    dadata_secret_key: str = Field(alias="DADATA_SECRET_KEY")

    default_weight_g: int = Field(default=100, alias="DEFAULT_WEIGHT_G")
    default_length_cm: int = Field(default=10, alias="DEFAULT_LENGTH_CM")
    default_width_cm: int = Field(default=10, alias="DEFAULT_WIDTH_CM")
    default_height_cm: int = Field(default=5, alias="DEFAULT_HEIGHT_CM")

    default_item_name: str = Field(default="Бижутерия", alias="DEFAULT_ITEM_NAME")
    default_item_ware_key: str = Field(default="JEWELRY-1", alias="DEFAULT_ITEM_WARE_KEY")

    database_url: str = Field(alias="DATABASE_URL")
    redis_url: str = Field(default="redis://localhost:6379/0", alias="REDIS_URL")
    pdf_storage_path: str = Field(default="./storage/pdfs", alias="PDF_STORAGE_PATH")

    @property
    def cdek_base_url(self) -> str:
        if self.cdek_test_mode:
            return "https://api.edu.cdek.ru/v2"
        return "https://api.cdek.ru/v2"

    @property
    def allowed_user_ids(self) -> set[int]:
        raw = (self.allowed_telegram_ids or "").strip()
        if not raw:
            return set()
        result: set[int] = set()
        for part in raw.replace(";", ",").split(","):
            part = part.strip()
            if part.isdigit() or (part.startswith("-") and part[1:].isdigit()):
                result.add(int(part))
        return result

    def is_user_allowed(self, user_id: int | None) -> bool:
        allowed = self.allowed_user_ids
        if not allowed:
            # пустой список = без ограничений
            return True
        return user_id is not None and user_id in allowed


@lru_cache
def get_settings() -> Settings:
    return Settings()
