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

    # Ключ шифрования секретов пользователей (любая длинная строка)
    encryption_key: str = Field(alias="ENCRYPTION_KEY")

    # Whitelist: Telegram user id и/или @username через запятую.
    # Пусто = доступ открыт всем.
    allowed_telegram_ids: str = Field(default="", alias="ALLOWED_TELEGRAM_IDS")

    database_url: str = Field(alias="DATABASE_URL")
    redis_url: str = Field(default="redis://localhost:6379/0", alias="REDIS_URL")
    pdf_storage_path: str = Field(default="./storage/pdfs", alias="PDF_STORAGE_PATH")

    def _allowed_parts(self) -> list[str]:
        raw = (self.allowed_telegram_ids or "").strip()
        if not raw:
            return []
        return [p.strip() for p in raw.replace(";", ",").split(",") if p.strip()]

    @property
    def allowed_user_ids(self) -> set[int]:
        result: set[int] = set()
        for part in self._allowed_parts():
            if part.isdigit() or (part.startswith("-") and part[1:].isdigit()):
                result.add(int(part))
        return result

    @property
    def allowed_usernames(self) -> set[str]:
        result: set[str] = set()
        for part in self._allowed_parts():
            if part.isdigit() or (part.startswith("-") and part[1:].isdigit()):
                continue
            name = part[1:] if part.startswith("@") else part
            if name:
                result.add(name.casefold())
        return result

    def is_user_allowed(self, user_id: int | None, username: str | None = None) -> bool:
        ids = self.allowed_user_ids
        names = self.allowed_usernames
        if not ids and not names:
            return True
        if user_id is not None and user_id in ids:
            return True
        if username and username.casefold() in names:
            return True
        return False


@lru_cache
def get_settings() -> Settings:
    return Settings()
