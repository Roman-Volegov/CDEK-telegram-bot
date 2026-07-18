#!/usr/bin/env python3
"""Проверка, что модули импортируются и настройки читаются."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def main() -> int:
    try:
        from app.config import Settings, get_settings
        from app.db import models, numerator  # noqa: F401
        from app.services import access, cdek, crypto, dadata, profile, runtime  # noqa: F401
        from app.bot.handlers import router

        # не падаем, если .env нет — только сообщаем
        try:
            s = get_settings()
            admins = sorted(s.admin_user_ids) or sorted(s.admin_usernames)
            print(
                f"OK config: admins={admins or '—'}, "
                f"db={s.database_url.split('@')[-1] if '@' in s.database_url else s.database_url}"
            )
        except Exception as exc:
            print(f"Config not loaded (нужен .env): {exc}")
            # Проверяем, что класс Settings собирается из полей
            assert hasattr(Settings, "model_fields")
            required = {"BOT_TOKEN", "ENCRYPTION_KEY", "DATABASE_URL"}
            aliases = {
                (f.alias or name)
                for name, f in Settings.model_fields.items()
            }
            missing = required - aliases
            if missing:
                raise RuntimeError(f"Settings missing aliases: {missing}") from exc

        print(f"OK routers registered, sub-routers={len(router.sub_routers)}")
        return 0
    except Exception as exc:
        print(f"FAIL: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
