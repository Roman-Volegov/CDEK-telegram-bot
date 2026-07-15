from aiogram.fsm.state import State, StatesGroup


class CalcStates(StatesGroup):
    waiting_address = State()


class OrderStates(StatesGroup):
    waiting_address = State()
    confirm_address = State()
    choose_tariff = State()
    waiting_pvz = State()
    waiting_name = State()
    waiting_phone = State()
    waiting_item_cost = State()
    confirm_order = State()
