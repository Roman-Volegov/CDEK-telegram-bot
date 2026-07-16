from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


def _split_parts(raw: str) -> list[str]:
    text = (raw or "").strip()
    if not text:
        return []
    return [p.strip() for p in text.replace(";", ",").split(",") if p.strip()]


def _parse_ids(parts: list[str]) -> set[int]:
    result: set[int] = set()
    for part in parts:
        if part.isdigit() or (part.startswith("-") and part[1:].isdigit()):
            result.add(int(part))
    return result


def _parse_usernames(parts: list[str]) -> set[str]:
    result: set[str] = set()
    for part in parts:
        if part.isdigit() or (part.startswith("-") and part[1:].isdigit()):
            continue
        name = part[1:] if part.startswith("@") else part
        if name:
            result.add(name.casefold())
    return result


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    bot_token: str = Field(alias="BOT_TOKEN")

    # Ключ шифрования секретов пользователей (любая длинная строка)
    encryption_key: str = Field(alias="ENCRYPTION_KEY")

    # Администраторы: получают заявки и могут approve/reject.
    # Если пусто — берётся ALLOWED_TELEGRAM_IDS (обратная совместимость).
    admin_telegram_ids: str = Field(default="", alias="ADMIN_TELEGRAM_IDS")

    # Устаревший whitelist: числовые ID автоматически получают approved при старте.
    # Username из списка считаются администраторами, если ADMIN пуст.
    allowed_telegram_ids: str = Field(default="", alias="ALLOWED_TELEGRAM_IDS")

    database_url: str = Field(alias="DATABASE_URL")
    redis_url: str = Field(default="redis://localhost:6379/0", alias="REDIS_URL")
    pdf_storage_path: str = Field(default="./storage/pdfs", alias="PDF_STORAGE_PATH")

    def _admin_parts(self) -> list[str]:
        parts = _split_parts(self.admin_telegram_ids)
        if parts:
            return parts
        return _split_parts(self.allowed_telegram_ids)

    @property
    def admin_user_ids(self) -> set[int]:
        return _parse_ids(self._admin_parts())

    @property
    def admin_usernames(self) -> set[str]:
        return _parse_usernames(self._admin_parts())

    @property
    def bootstrap_approved_ids(self) -> set[int]:
        """Числовые ID из старого whitelist — сразу approved."""
        return _parse_ids(_split_parts(self.allowed_telegram_ids))

    @property
    def bootstrap_approved_usernames(self) -> set[str]:
        """Username из старого whitelist — auto-approve при первом визите."""
        return _parse_usernames(_split_parts(self.allowed_telegram_ids))

    def is_admin(self, user_id: int | None, username: str | None = None) -> bool:
        if user_id is not None and user_id in self.admin_user_ids:
            return True
        if username and username.casefold() in self.admin_usernames:
            return True
        return False

    def is_bootstrap_approved(
        self, user_id: int | None, username: str | None = None
    ) -> bool:
        if user_id is not None and user_id in self.bootstrap_approved_ids:
            return True
        if username and username.casefold() in self.bootstrap_approved_usernames:
            return True
        return False


@lru_cache
def get_settings() -> Settings:
    return Settings()
