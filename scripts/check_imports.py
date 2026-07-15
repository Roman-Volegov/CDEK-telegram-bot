#!/usr/bin/env python3
"""Проверка, что модули импортируются и настройки читаются."""

from __future__ import annotations

import sys


def main() -> int:
    try:
        from app.config import get_settings

        # не падаем, если .env нет — только сообщаем
        try:
            s = get_settings()
            print(f"OK config: shipment_point={s.cdek_shipment_point}, test={s.cdek_test_mode}")
        except Exception as exc:
            print(f"Config not loaded (нужен .env): {exc}")

        from app.db import models, numerator  # noqa: F401
        from app.services import cdek, dadata  # noqa: F401
        from app.bot.handlers import setup_routers

        router = setup_routers()
        print(f"OK routers registered, sub-routers={len(router.sub_routers)}")
        return 0
    except Exception as exc:
        print(f"FAIL: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
