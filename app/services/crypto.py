from __future__ import annotations

import base64
import hashlib

from cryptography.fernet import Fernet, InvalidToken


def _fernet_from_secret(secret: str) -> Fernet:
    """Строит ключ Fernet из произвольной строки ENCRYPTION_KEY."""
    digest = hashlib.sha256(secret.encode("utf-8")).digest()
    return Fernet(base64.urlsafe_b64encode(digest))


class SecretBox:
    def __init__(self, encryption_key: str) -> None:
        if not encryption_key:
            raise ValueError("ENCRYPTION_KEY не задан")
        self._fernet = _fernet_from_secret(encryption_key)

    def encrypt(self, value: str | None) -> str | None:
        if value is None or value == "":
            return None
        return self._fernet.encrypt(value.encode("utf-8")).decode("utf-8")

    def decrypt(self, token: str | None) -> str | None:
        if token is None or token == "":
            return None
        try:
            return self._fernet.decrypt(token.encode("utf-8")).decode("utf-8")
        except InvalidToken as exc:
            raise ValueError("Не удалось расшифровать секрет (неверный ENCRYPTION_KEY?)") from exc
