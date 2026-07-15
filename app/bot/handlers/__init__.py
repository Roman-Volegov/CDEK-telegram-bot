from aiogram import Router

from app.bot.handlers import calc, history, orders, start


def setup_routers() -> Router:
    root = Router()
    root.include_router(start.router)
    root.include_router(calc.router)
    root.include_router(orders.router)
    root.include_router(history.router)
    return root
