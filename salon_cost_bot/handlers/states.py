from aiogram.fsm.state import State, StatesGroup


class CalculationFlow(StatesGroup):
    selecting = State()
    entering_quantity = State()
    entering_service_price = State()
    confirming = State()


class ReceiptFlow(StatesGroup):
    product = State()
    quantity = State()
    package_quantity = State()
    package_count = State()
    package_price = State()
    note = State()


class WriteOffFlow(StatesGroup):
    product = State()
    quantity = State()
    reason = State()


class InventoryCountFlow(StatesGroup):
    product = State()
    actual = State()


class InventorySearchFlow(StatesGroup):
    query = State()


class AddProductFlow(StatesGroup):
    category = State()
    brand = State()
    name = State()
    unit = State()
    package_quantity = State()
    purchase_price = State()
    calculation_rate = State()
    initial_stock = State()
    visibility = State()


class EditProductFlow(StatesGroup):
    product = State()
    field = State()
    value = State()


class PriceFlow(StatesGroup):
    product = State()
    rate = State()
