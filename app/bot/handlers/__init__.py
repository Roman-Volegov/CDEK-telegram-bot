from aiogram import Router

from app.bot.handlers import calc, history, orders, setup, start


router = Router()
router.include_router(start.router)
router.include_router(setup.router)
router.include_router(calc.router)
router.include_router(orders.router)
router.include_router(history.router)
