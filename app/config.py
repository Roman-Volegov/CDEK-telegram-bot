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

    cdek_client_id: str = Field(alias="CDEK_CLIENT_ID")
    cdek_client_secret: str = Field(alias="CDEK_CLIENT_SECRET")
    cdek_test_mode: bool = Field(default=True, alias="CDEK_TEST_MODE")
    cdek_shipment_point: str = Field(alias="CDEK_SHIPMENT_POINT")

    dadata_api_key: str = Field(alias="DADATA_API_KEY")
    dadata_secret_key: str = Field(alias="DADATA_SECRET_KEY")

    default_weight_g: int = Field(default=1000, alias="DEFAULT_WEIGHT_G")
    default_length_cm: int = Field(default=30, alias="DEFAULT_LENGTH_CM")
    default_width_cm: int = Field(default=20, alias="DEFAULT_WIDTH_CM")
    default_height_cm: int = Field(default=10, alias="DEFAULT_HEIGHT_CM")

    default_item_name: str = Field(default="Товар", alias="DEFAULT_ITEM_NAME")
    default_item_ware_key: str = Field(default="ITEM-1", alias="DEFAULT_ITEM_WARE_KEY")

    database_url: str = Field(alias="DATABASE_URL")
    redis_url: str = Field(default="redis://localhost:6379/0", alias="REDIS_URL")
    pdf_storage_path: str = Field(default="./storage/pdfs", alias="PDF_STORAGE_PATH")

    @property
    def cdek_base_url(self) -> str:
        if self.cdek_test_mode:
            return "https://api.edu.cdek.ru/v2"
        return "https://api.cdek.ru/v2"


@lru_cache
def get_settings() -> Settings:
    return Settings()
