from aiogram.fsm.state import State, StatesGroup


class SetupStates(StatesGroup):
    cdek_client_id = State()
    cdek_client_secret = State()
    cdek_test_mode = State()
    shipment_point = State()
    sender_name = State()
    sender_phone = State()
    dadata_api_key = State()
    dadata_secret_key = State()
    weight = State()
    dimensions = State()
    item_name = State()
    confirm = State()


class CalcStates(StatesGroup):
    waiting_address = State()


class OrderStates(StatesGroup):
    waiting_address = State()
    confirm_address = State()
    choose_tariff = State()
    choose_pvz = State()
    waiting_pvz = State()
    waiting_name = State()
    waiting_phone = State()
    waiting_item_cost = State()
    confirm_order = State()
    edit_menu = State()
    edit_dimensions = State()
    edit_sender_name = State()
    edit_sender_phone = State()
    edit_shipment_point = State()
