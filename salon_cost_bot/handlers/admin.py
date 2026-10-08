from __future__ import annotations

import logging
import tempfile
from datetime import datetime
from decimal import Decimal
from pathlib import Path

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import (
    CallbackQuery,
    FSInputFile,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)

from salon_cost_bot.backups import timestamp
from salon_cost_bot.config import MOSCOW_TZ, Settings
from salon_cost_bot.domain import decimal_text, money_text, parse_decimal, quantity_text
from salon_cost_bot.handlers.common import is_admin, safe_answer
from salon_cost_bot.handlers.states import (
    AddProductFlow,
    EditProductFlow,
    InventoryCountFlow,
    InventorySearchFlow,
    PriceFlow,
    ReceiptFlow,
    WriteOffFlow,
)
from salon_cost_bot.keyboards import ADMIN_PANEL, page_keyboard, rows_keyboard, session_item_keyboard
from salon_cost_bot.services import SalonService

logger = logging.getLogger(__name__)
router = Router(name="admin")
PAGE_SIZE = 18


async def _allowed(callback: CallbackQuery, settings: Settings) -> bool:
    if not is_admin(settings, callback.from_user.id):
        await safe_answer(callback, "У вас нет доступа к этой функции.")
        return False
    return True


async def _allowed_message(message: Message, settings: Settings) -> bool:
    if not message.from_user or not is_admin(settings, message.from_user.id):
        await message.answer("У вас нет доступа к этой функции.")
        return False
    return True


async def _send(callback: CallbackQuery, text: str, keyboard=None) -> None:
    if callback.message:
        await callback.message.answer(text, reply_markup=keyboard)
    if isinstance(callback, CallbackQuery):
        await safe_answer(callback)


def _moscow_time(value: str) -> str:
    stamp = datetime.fromisoformat(value)
    if stamp.tzinfo is None:
        stamp = stamp.replace(tzinfo=MOSCOW_TZ)
    return stamp.astimezone(MOSCOW_TZ).strftime("%d.%m.%Y %H:%M")


def _admin_root_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="📋 Все остатки", callback_data="adm:stock:all:0"),
                InlineKeyboardButton(text="🔎 Найти позицию", callback_data="adm:search"),
            ],
            [
                InlineKeyboardButton(text="⚠️ Требует внимания", callback_data="adm:attention:0"),
                InlineKeyboardButton(text="➕ Приход", callback_data="adm:pick:receipt:0"),
            ],
            [
                InlineKeyboardButton(text="➖ Списание", callback_data="adm:pick:writeoff:0"),
                InlineKeyboardButton(text="🧾 Пересчёт позиции", callback_data="adm:pick:count:0"),
            ],
            [
                InlineKeyboardButton(text="➕ Новая позиция", callback_data="adm:add_product"),
                InlineKeyboardButton(text="🗂 Каталог", callback_data="adm:stock:categories"),
            ],
            [InlineKeyboardButton(text="⬅️ К панели", callback_data="adm:home")],
        ]
    )


def _product_picker_keyboard(
    rows: list[dict],
    *,
    action: str,
    page: int,
    more: bool,
) -> InlineKeyboardMarkup:
    buttons = [
        [
            InlineKeyboardButton(
                text=f"{row['brand_name']} · {row['name']}"[:60],
                callback_data=f"adm:select:{action}:{row['id']}",
            )
        ]
        for row in rows
    ]
    navigation = []
    if page:
        navigation.append(
            InlineKeyboardButton(
                text="‹ Назад", callback_data=f"adm:pick:{action}:{page - 1}"
            )
        )
    if more:
        navigation.append(
            InlineKeyboardButton(
                text="Далее ›", callback_data=f"adm:pick:{action}:{page + 1}"
            )
        )
    if navigation:
        buttons.append(navigation)
    buttons.extend(
        [
            [InlineKeyboardButton(text="⬅️ К складу", callback_data="adm:stock")],
            [InlineKeyboardButton(text="🏠 Главное меню", callback_data="home")],
        ]
    )
    return InlineKeyboardMarkup(inline_keyboard=buttons)


async def _pick_products(
    callback: CallbackQuery,
    service: SalonService,
    *,
    action: str,
    page: int,
    search: str | None = None,
) -> None:
    if search is not None:
        products = await service.search_products(search, limit=PAGE_SIZE + 1)
        more = len(products) > PAGE_SIZE
        products = products[:PAGE_SIZE]
        page = 0
    else:
        rows = await service.product_picker(
            limit=PAGE_SIZE + 1, offset=page * PAGE_SIZE
        )
        products = rows[:PAGE_SIZE]
        more = len(rows) > PAGE_SIZE
    if not products:
        await _send(callback, "Подходящие материалы не найдены.")
        return
    labels = {
        "receipt": "Выберите материал для прихода:",
        "writeoff": "Выберите материал для списания:",
        "count": "Выберите материал для пересчёта:",
        "price": "Выберите материал для изменения расчётной ставки:",
        "edit": "Выберите материал для изменения:",
        "archive": "Выберите материал для архивации или восстановления:",
        "detail": "Выберите материал:",
    }
    await _send(
        callback,
        labels.get(action, "Выберите материал:"),
        _product_picker_keyboard(products, action=action, page=page, more=more),
    )


async def _product_detail(
    target: CallbackQuery, service: SalonService, product_id: int
) -> None:
    product = await service.get_product(product_id)
    if product is None:
        await safe_answer(target, "Материал не найден.")
        return
    stock = Decimal(product["current_stock"])
    purchase = (
        Decimal(product["purchase_price_per_unit"])
        if product["purchase_price_per_unit"] is not None
        else None
    )
    details = await service.admin_product_details(product_id)
    rate = (
        Decimal(details["calculation_rate"])
        if details["calculation_rate"] is not None
        else None
    )
    estimated = money_text(stock * purchase) if purchase is not None else "неизвестна"
    attention = (
        "\n⚠️ Требует инвентаризации"
        if product["needs_inventory"] or stock < 0
        else ""
    )
    package = (
        quantity_text(Decimal(product["package_quantity"]))
        if product["package_quantity"]
        else "не указана"
    )
    purchase_line = (
        f"{money_text(purchase)} ₽/{product['unit']}"
        if purchase is not None
        else "неизвестна"
    )
    package_price = (
        f"{money_text(Decimal(product['purchase_price_per_package']))} ₽"
        if product["purchase_price_per_package"]
        else "не указана"
    )
    rate_line = f"{money_text(rate)} ₽/{product['unit']}" if rate is not None else "не задана"
    text = (
        f"{product['name']}\n"
        f"Бренд: {product['brand_name']}\n"
        f"Категория: {product['category_name']}\n"
        f"Статус: {'активна' if product['is_active'] else 'в архиве'}\n"
        f"Остаток: {quantity_text(stock)} {product['unit']}\n"
        f"Единица: {product['unit']}\n"
        f"Масса упаковки: {package} {product['unit']}\n"
        f"Последняя цена упаковки: {package_price}\n"
        f"Закупочная цена: {purchase_line}\n"
        f"Расчётная ставка: {rate_line}\n"
        f"Оценка остатка по закупочной цене: {estimated} ₽\n"
        f"Видна мастерам: {'да' if product['visible_to_masters'] else 'нет'}\n"
        f"Последнее изменение позиции: {_moscow_time(product['updated_at'])}\n"
        f"Последнее движение: {_moscow_time(details['last_movement_at']) if details['last_movement_at'] else 'нет'}"
        f"{attention}"
    )
    active_label = "📥 Восстановить" if not product["is_active"] else "🗄 В архив"
    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="➕ Приход", callback_data=f"adm:receipt:{product_id}"),
                InlineKeyboardButton(text="➖ Списание", callback_data=f"adm:writeoff:{product_id}"),
            ],
            [
                InlineKeyboardButton(text="🧾 Пересчитать", callback_data=f"adm:countproduct:{product_id}"),
                InlineKeyboardButton(text="💰 Ставка", callback_data=f"adm:price:{product_id}"),
            ],
            [
                InlineKeyboardButton(text="📝 Изменить", callback_data=f"adm:editmenu:{product_id}"),
                InlineKeyboardButton(text=active_label, callback_data=f"adm:archive:{product_id}"),
            ],
            [InlineKeyboardButton(text="⬅️ К складу", callback_data="adm:stock")],
        ]
    )
    await _send(target, text, keyboard)


@router.callback_query(F.data == "adm:stock")
async def stock_root(callback: CallbackQuery, settings: Settings) -> None:
    if not await _allowed(callback, settings):
        return
    await _send(callback, "Управление складом:", _admin_root_keyboard())


@router.callback_query(F.data == "adm:stock:categories")
async def stock_categories(
    callback: CallbackQuery, settings: Settings, service: SalonService
) -> None:
    if not await _allowed(callback, settings):
        return
    categories = await service.categories()
    items = [
        (f"{row['name']} · {row['product_count']}", f"adm:stock:cat:{row['id']}")
        for row in categories
    ]
    await _send(callback, "Склад · выберите категорию:", rows_keyboard(items, columns=1))


@router.callback_query(F.data.startswith("adm:stock:cat:"))
async def stock_category(
    callback: CallbackQuery, settings: Settings, service: SalonService
) -> None:
    if not await _allowed(callback, settings):
        return
    try:
        category_id = int(callback.data.rsplit(":", 1)[1])
    except (ValueError, AttributeError):
        await safe_answer(callback, "Категория не найдена.")
        return
    brands = await service.inventory_brands(category_id)
    if not brands:
        await _send(callback, "В категории нет активных материалов.")
        return
    await _send(
        callback,
        "Склад · выберите бренд:",
        rows_keyboard(
            [
                (f"{row['name']} · {row['total']}", f"adm:stock:brand:{category_id}:{row['id']}")
                for row in brands
            ],
            back="adm:stock:categories",
        ),
    )


@router.callback_query(F.data.startswith("adm:stock:brand:"))
async def stock_brand(
    callback: CallbackQuery, settings: Settings, service: SalonService
) -> None:
    if not await _allowed(callback, settings):
        return
    try:
        _, _, _, category_raw, brand_raw = callback.data.split(":")
        category_id, brand_id = int(category_raw), int(brand_raw)
    except (ValueError, AttributeError):
        await safe_answer(callback, "Список материалов устарел.")
        return
    products = await service.inventory_products(category_id, brand_id)
    await _send(
        callback,
        "Выберите складскую позицию:",
        rows_keyboard(
            [
                (
                    f"{row['name']} · {quantity_text(Decimal(row['current_stock']))} {row['unit']}",
                    f"adm:stock:product:{row['id']}",
                )
                for row in products
            ],
            back=f"adm:stock:cat:{category_id}",
            columns=1,
        ),
    )


@router.callback_query(F.data.startswith("adm:stock:product:"))
async def stock_product_detail(
    callback: CallbackQuery, settings: Settings, service: SalonService
) -> None:
    if not await _allowed(callback, settings):
        return
    try:
        product_id = int(callback.data.rsplit(":", 1)[1])
    except (ValueError, AttributeError):
        await safe_answer(callback, "Материал не найден.")
        return
    await _product_detail(callback, service, product_id)


@router.callback_query(F.data.startswith("adm:stock:all:"))
async def inventory_page(
    callback: CallbackQuery, settings: Settings, service: SalonService
) -> None:
    if not await _allowed(callback, settings):
        return
    try:
        page = int(callback.data.rsplit(":", 1)[1])
    except (ValueError, AttributeError):
        page = 0
    items = await service.inventory(offset=page * PAGE_SIZE, limit=PAGE_SIZE + 1)
    more = len(items) > PAGE_SIZE
    items = items[:PAGE_SIZE]
    if not items:
        await _send(callback, "Склад пуст.")
        return
    lines = [f"Остатки · страница {page + 1}:"]
    buttons = []
    for item in items:
        balance = Decimal(item["current_stock"])
        marker = (
            " ⚠️"
            if balance < 0
            or item["needs_inventory"]
            or item["below_low_stock_threshold"]
            else ""
        )
        value = (
            f" · {money_text(Decimal(item['estimated_value']))} ₽"
            if item["estimated_value"] is not None
            else ""
        )
        lines.append(
            f"• {item['brand_name']} {item['name']} — "
            f"{quantity_text(balance)} {item['unit']}{value}{marker}"
        )
        buttons.append(
            [
                InlineKeyboardButton(
                    text=f"{item['brand_name']} · {item['name']}"[:60],
                    callback_data=f"adm:stock:product:{item['id']}",
                )
            ]
        )
    nav = []
    if page:
        nav.append(
            InlineKeyboardButton(text="‹ Назад", callback_data=f"adm:stock:all:{page - 1}")
        )
    if more:
        nav.append(
            InlineKeyboardButton(text="Далее ›", callback_data=f"adm:stock:all:{page + 1}")
        )
    if nav:
        buttons.append(nav)
    buttons.append([InlineKeyboardButton(text="⬅️ К складу", callback_data="adm:stock")])
    await _send(callback, "\n".join(lines), InlineKeyboardMarkup(inline_keyboard=buttons))


@router.callback_query(F.data.startswith("adm:attention:"))
async def attention_page(
    callback: CallbackQuery, settings: Settings, service: SalonService
) -> None:
    if not await _allowed(callback, settings):
        return
    try:
        page = int(callback.data.rsplit(":", 1)[1])
    except (ValueError, AttributeError):
        page = 0
    items = await service.inventory(
        offset=page * PAGE_SIZE, limit=PAGE_SIZE + 1, attention_only=True
    )
    if not items:
        await _send(callback, "Позиции, требующие внимания, не найдены.")
        return
    more = len(items) > PAGE_SIZE
    items = items[:PAGE_SIZE]
    lines = ["Требует внимания:"]
    buttons = []
    for item in items:
        warnings = []
        if Decimal(item["current_stock"]) < 0:
            warnings.append("отрицательный остаток")
        if item["needs_inventory"]:
            warnings.append("нужен пересчёт")
        if item["below_low_stock_threshold"]:
            warnings.append("ниже порога")
        warning_text = f" ({', '.join(warnings)})" if warnings else ""
        lines.append(
            f"• {item['brand_name']} {item['name']} — "
            f"{quantity_text(Decimal(item['current_stock']))} {item['unit']}"
            f"{warning_text}"
        )
        buttons.append(
            [
                InlineKeyboardButton(
                    text=f"{item['brand_name']} · {item['name']}"[:60],
                    callback_data=f"adm:stock:product:{item['id']}",
                )
            ]
        )
    nav = []
    if page:
        nav.append(
            InlineKeyboardButton(text="‹ Назад", callback_data=f"adm:attention:{page-1}")
        )
    if more:
        nav.append(
            InlineKeyboardButton(text="Далее ›", callback_data=f"adm:attention:{page+1}")
        )
    if nav:
        buttons.append(nav)
    buttons.append([InlineKeyboardButton(text="⬅️ К складу", callback_data="adm:stock")])
    await _send(callback, "\n".join(lines), InlineKeyboardMarkup(inline_keyboard=buttons))


@router.callback_query(F.data == "adm:search")
async def begin_search(
    callback: CallbackQuery, settings: Settings, state: FSMContext
) -> None:
    if not await _allowed(callback, settings):
        return
    await state.set_state(InventorySearchFlow.query)
    await _send(callback, "Введите название, бренд или категорию для поиска.")


@router.message(InventorySearchFlow.query)
async def search_message(
    message: Message, settings: Settings, state: FSMContext, service: SalonService
) -> None:
    if not await _allowed_message(message, settings):
        return
    query = (message.text or "").strip()
    if not query or len(query) < 2:
        await message.answer("Введите не менее двух символов.")
        return
    await state.clear()
    results = await service.search_products(query, limit=PAGE_SIZE)
    if not results:
        await message.answer("Материалы не найдены.")
        return
    await message.answer(
        "Результаты поиска:",
        reply_markup=rows_keyboard(
            [
                (
                    f"{row['brand_name']} · {row['name']} · "
                    f"{quantity_text(Decimal(row['current_stock']))} {row['unit']}",
                    f"adm:stock:product:{row['id']}",
                )
                for row in results
            ],
            back="adm:stock",
            columns=1,
        ),
    )


@router.callback_query(F.data.startswith("adm:pick:"))
async def pick_product_page(
    callback: CallbackQuery, settings: Settings, service: SalonService
) -> None:
    if not await _allowed(callback, settings):
        return
    try:
        _, _, action, page_raw = callback.data.split(":")
        page = max(0, int(page_raw))
    except (ValueError, AttributeError):
        await safe_answer(callback, "Список материалов устарел.")
        return
    await _pick_products(callback, service, action=action, page=page)


@router.callback_query(F.data.startswith("adm:select:"))
async def select_product_action(
    callback: CallbackQuery,
    settings: Settings,
    state: FSMContext,
    service: SalonService,
) -> None:
    if not await _allowed(callback, settings):
        return
    try:
        _, _, action, product_raw = callback.data.split(":")
        product_id = int(product_raw)
    except (ValueError, AttributeError):
        await safe_answer(callback, "Материал не найден.")
        return
    if action == "receipt":
        await _start_receipt(callback, state, service, product_id)
    elif action == "writeoff":
        await _start_writeoff(callback, state, service, product_id)
    elif action == "count":
        await _start_single_count(callback, state, service, product_id)
    elif action == "price":
        await _start_price(callback, state, service, product_id)
    elif action == "edit":
        await _show_edit_menu(callback, service, product_id)
    elif action == "archive":
        await _ask_archive_confirmation(callback, service, product_id)
    else:
        await _product_detail(callback, service, product_id)


@router.callback_query(F.data == "adm:receipt:start")
async def receipt_start(
    callback: CallbackQuery, settings: Settings, service: SalonService
) -> None:
    if not await _allowed(callback, settings):
        return
    await _pick_products(callback, service, action="receipt", page=0)


async def _start_receipt(
    callback: CallbackQuery,
    state: FSMContext,
    service: SalonService,
    product_id: int,
) -> None:
    product = await service.get_product(product_id)
    if not product or not product["is_active"]:
        await safe_answer(callback, "Материал не найден или архивирован.")
        return
    await state.clear()
    await state.update_data(product_id=product_id, product=product)
    await state.set_state(ReceiptFlow.product)
    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=f"Указать количество ({product['unit']})",
                    callback_data="adm:receiptmode:unit",
                )
            ],
            [
                InlineKeyboardButton(
                    text="Указать упаковки и цену",
                    callback_data="adm:receiptmode:package",
                )
            ],
            [InlineKeyboardButton(text="✖️ Отмена", callback_data="adm:stock")],
        ]
    )
    await _send(
        callback,
        f"Приход: {product['brand_name']} {product['name']}\nВыберите способ ввода:",
        keyboard,
    )


@router.callback_query(F.data.startswith("adm:receiptmode:"))
async def receipt_mode(
    callback: CallbackQuery, settings: Settings, state: FSMContext
) -> None:
    if not await _allowed(callback, settings):
        return
    data = await state.get_data()
    product = data.get("product")
    if not product:
        await safe_answer(callback, "Шаг устарел. Начните приход заново.")
        return
    mode = callback.data.rsplit(":", 1)[1]
    if mode == "unit":
        await state.set_state(ReceiptFlow.quantity)
        await _send(
            callback,
            f"Введите количество в {product['unit']}. Для дробного значения используйте запятую.",
        )
    elif mode == "package":
        await state.set_state(ReceiptFlow.package_count)
        await _send(callback, "Введите количество упаковок.")
    else:
        await safe_answer(callback, "Неизвестный способ ввода.")


@router.message(ReceiptFlow.quantity)
async def receipt_quantity_message(
    message: Message, settings: Settings, state: FSMContext, service: SalonService
) -> None:
    if not await _allowed_message(message, settings):
        return
    try:
        quantity = parse_decimal(message.text or "")
        if quantity <= 0:
            raise ValueError("Количество прихода должно быть больше нуля.")
    except ValueError as error:
        await message.answer(str(error))
        return
    data = await state.get_data()
    product = data.get("product")
    if not product:
        await state.clear()
        await message.answer("Приход устарел. Начните заново.")
        return
    before, after = await service.receipt(
        product_id=int(product["id"]),
        quantity=quantity,
        admin_user_id=message.from_user.id,
    )
    await state.clear()
    await message.answer(
        f"Приход записан: +{quantity_text(quantity)} {product['unit']}.\n"
        f"Остаток: {quantity_text(before)} → {quantity_text(after)} {product['unit']}."
    )


@router.message(ReceiptFlow.package_count)
async def receipt_package_count(
    message: Message, settings: Settings, state: FSMContext
) -> None:
    if not await _allowed_message(message, settings):
        return
    try:
        count = parse_decimal(message.text or "")
        if count <= 0:
            raise ValueError("Количество упаковок должно быть больше нуля.")
    except ValueError as error:
        await message.answer(str(error))
        return
    await state.update_data(package_count=decimal_text(count))
    await state.set_state(ReceiptFlow.package_quantity)
    product = (await state.get_data()).get("product", {})
    await message.answer(f"Введите массу/объём одной упаковки в {product.get('unit', 'g')}.")


@router.message(ReceiptFlow.package_quantity)
async def receipt_package_quantity(
    message: Message, settings: Settings, state: FSMContext
) -> None:
    if not await _allowed_message(message, settings):
        return
    try:
        package_quantity = parse_decimal(message.text or "")
        if package_quantity <= 0:
            raise ValueError("Масса/объём упаковки должен быть больше нуля.")
    except ValueError as error:
        await message.answer(str(error))
        return
    await state.update_data(package_quantity=decimal_text(package_quantity))
    await state.set_state(ReceiptFlow.package_price)
    await message.answer(
        "Введите цену одной упаковки в рублях или «-», если цена неизвестна."
    )


@router.message(ReceiptFlow.package_price)
async def receipt_package_price(
    message: Message, settings: Settings, state: FSMContext
) -> None:
    if not await _allowed_message(message, settings):
        return
    raw = (message.text or "").strip()
    if raw == "-":
        price = None
    else:
        try:
            price = parse_decimal(raw)
            if not price.is_finite() or price < 0:
                raise ValueError("Цена упаковки не может быть отрицательной.")
        except ValueError as error:
            await message.answer(str(error))
            return
    await state.update_data(price_per_package=decimal_text(price) if price is not None else None)
    await state.set_state(ReceiptFlow.note)
    await message.answer("Введите примечание к приходу или «-», если не нужно.")


@router.message(ReceiptFlow.note)
async def receipt_note(
    message: Message, settings: Settings, state: FSMContext, service: SalonService
) -> None:
    if not await _allowed_message(message, settings):
        return
    data = await state.get_data()
    product = data.get("product")
    if not product:
        await state.clear()
        await message.answer("Приход устарел. Начните заново.")
        return
    package_count = Decimal(data["package_count"])
    package_quantity = Decimal(data["package_quantity"])
    price = Decimal(data["price_per_package"]) if data.get("price_per_package") else None
    quantity = package_count * package_quantity
    note = (message.text or "").strip()
    if note == "-":
        note = ""
    before, after = await service.receipt(
        product_id=int(product["id"]),
        quantity=quantity,
        admin_user_id=message.from_user.id,
        package_quantity=package_quantity,
        package_count=package_count,
        price_per_package=price,
        note=note or None,
    )
    await state.clear()
    await message.answer(
        f"Приход записан: {quantity_text(package_count)} уп. × "
        f"{quantity_text(package_quantity)} {product['unit']} = "
        f"+{quantity_text(quantity)} {product['unit']}.\n"
        f"Остаток: {quantity_text(before)} → {quantity_text(after)} {product['unit']}."
    )


async def _start_writeoff(
    callback: CallbackQuery,
    state: FSMContext,
    service: SalonService,
    product_id: int,
) -> None:
    product = await service.get_product(product_id)
    if not product or not product["is_active"]:
        await safe_answer(callback, "Материал не найден или архивирован.")
        return
    await state.clear()
    await state.update_data(product_id=product_id, product=product)
    await state.set_state(WriteOffFlow.quantity)
    await _send(
        callback,
        f"Списание: {product['brand_name']} {product['name']}.\n"
        f"Введите количество в {product['unit']}.",
    )


@router.callback_query(F.data.startswith("adm:writeoff:"))
async def writeoff_from_detail(
    callback: CallbackQuery,
    settings: Settings,
    state: FSMContext,
    service: SalonService,
) -> None:
    if not await _allowed(callback, settings):
        return
    try:
        product_id = int(callback.data.rsplit(":", 1)[1])
    except (ValueError, AttributeError):
        await safe_answer(callback, "Материал не найден.")
        return
    await _start_writeoff(callback, state, service, product_id)


@router.callback_query(F.data.startswith("adm:receipt:"))
async def receipt_from_detail(
    callback: CallbackQuery,
    settings: Settings,
    state: FSMContext,
    service: SalonService,
) -> None:
    if not await _allowed(callback, settings):
        return
    try:
        product_id = int(callback.data.rsplit(":", 1)[1])
    except (ValueError, AttributeError):
        await safe_answer(callback, "Материал не найден.")
        return
    await _start_receipt(callback, state, service, product_id)


async def _ask_archive_confirmation(
    callback: CallbackQuery, service: SalonService, product_id: int
) -> None:
    product = await service.get_product(product_id)
    if product is None:
        await safe_answer(callback, "Материал не найден.")
        return
    # The target status is fixed in the button, so pressing it twice cannot
    # accidentally undo the change.
    target_active = 0 if product["is_active"] else 1
    action_label = "архивировать" if target_active == 0 else "восстановить"
    await _send(
        callback,
        f"Вы действительно хотите {action_label} материал "
        f"«{product['brand_name']} · {product['name']}»?",
        InlineKeyboardMarkup(
            inline_keyboard=[
                [InlineKeyboardButton(
                    text="✅ Подтвердить",
                    callback_data=f"adm:archive:apply:{product_id}:{target_active}",
                )],
                [InlineKeyboardButton(
                    text="✖️ Отмена",
                    callback_data=f"adm:stock:product:{product_id}",
                )],
            ]
        ),
    )


@router.callback_query(F.data.startswith("adm:archive:apply:"))
async def apply_archive_status(
    callback: CallbackQuery, settings: Settings, service: SalonService
) -> None:
    if not await _allowed(callback, settings):
        return
    try:
        _, _, _, product_raw, active_raw = callback.data.split(":")
        product_id, target_active = int(product_raw), int(active_raw)
        if target_active not in {0, 1}:
            raise ValueError
    except (ValueError, AttributeError):
        await safe_answer(callback, "Действие устарело.")
        return
    product = await service.get_product(product_id)
    if product is None:
        await safe_answer(callback, "Материал не найден.")
        return
    if int(bool(product["is_active"])) == target_active:
        await _send(callback, "Статус материала уже обновлён.")
        return
    await service.set_product_field(product_id, "is_active", target_active)
    status = "восстановлена" if target_active else "перемещена в архив"
    await _send(callback, f"Позиция {status}. История и движения сохранены.")


@router.callback_query(F.data.startswith("adm:archive:"))
async def archive_or_restore_product(
    callback: CallbackQuery, settings: Settings, service: SalonService
) -> None:
    if not await _allowed(callback, settings):
        return
    try:
        product_id = int(callback.data.rsplit(":", 1)[1])
    except (ValueError, AttributeError):
        await safe_answer(callback, "Материал не найден.")
        return
    await _ask_archive_confirmation(callback, service, product_id)


@router.message(WriteOffFlow.quantity)
async def writeoff_quantity(
    message: Message, settings: Settings, state: FSMContext
) -> None:
    if not await _allowed_message(message, settings):
        return
    try:
        quantity = parse_decimal(message.text or "")
        if quantity <= 0:
            raise ValueError("Количество списания должно быть больше нуля.")
    except ValueError as error:
        await message.answer(str(error))
        return
    await state.update_data(writeoff_quantity=decimal_text(quantity))
    await state.set_state(WriteOffFlow.reason)
    await message.answer(
        "Укажите причину списания: пролито, повреждено, тест-прядь, срок годности или другое."
    )


@router.message(WriteOffFlow.reason)
async def writeoff_reason(
    message: Message, settings: Settings, state: FSMContext, service: SalonService
) -> None:
    if not await _allowed_message(message, settings):
        return
    reason = (message.text or "").strip()
    if len(reason) < 2 or len(reason) > 200:
        await message.answer("Напишите короткую причину списания (2–200 символов).")
        return
    data = await state.get_data()
    product = data.get("product")
    if not product:
        await state.clear()
        await message.answer("Списание устарело. Начните заново.")
        return
    quantity = Decimal(data["writeoff_quantity"])
    before, after = await service.write_off(
        product_id=int(product["id"]),
        quantity=quantity,
        reason=reason,
        admin_user_id=message.from_user.id,
    )
    await state.clear()
    await message.answer(
        f"Списание записано: −{quantity_text(quantity)} {product['unit']}.\n"
        f"Остаток: {quantity_text(before)} → {quantity_text(after)} {product['unit']}."
    )


async def _start_single_count(
    callback: CallbackQuery,
    state: FSMContext,
    service: SalonService,
    product_id: int,
) -> None:
    product = await service.get_product(product_id)
    if not product or not product["is_active"]:
        await safe_answer(callback, "Материал не найден или архивирован.")
        return
    await state.clear()
    await state.update_data(product_id=product_id, product=product, count_mode="single")
    await state.set_state(InventoryCountFlow.actual)
    await _send(
        callback,
        f"Система: {quantity_text(Decimal(product['current_stock']))} {product['unit']}.\n"
        "Введите фактический остаток.",
    )


@router.callback_query(F.data.startswith("adm:countproduct:"))
async def count_product_from_detail(
    callback: CallbackQuery,
    settings: Settings,
    state: FSMContext,
    service: SalonService,
) -> None:
    if not await _allowed(callback, settings):
        return
    try:
        product_id = int(callback.data.rsplit(":", 1)[1])
    except (ValueError, AttributeError):
        await safe_answer(callback, "Материал не найден.")
        return
    await _start_single_count(callback, state, service, product_id)


@router.callback_query(F.data == "adm:count")
async def start_full_count(
    callback: CallbackQuery,
    settings: Settings,
    state: FSMContext,
    service: SalonService,
) -> None:
    if not await _allowed(callback, settings):
        return
    active_id = await service.active_inventory_session()
    session_id = active_id or await service.start_inventory_session(callback.from_user.id)
    await state.clear()
    await state.update_data(session_id=session_id, count_mode="session")
    await _send_next_session_product(callback, state, service, session_id)


async def _send_next_session_product(
    callback: CallbackQuery,
    state: FSMContext,
    service: SalonService,
    session_id: int,
) -> None:
    item = await service.next_session_item(session_id)
    if item is None:
        summary = await service.finish_inventory_session(session_id, callback.from_user.id)
        data = summary.get("summary")
        if not data:
            await _send(callback, "Инвентаризация уже завершена.")
            return
        await state.clear()
        await _send(
            callback,
            "Инвентаризация завершена.\n"
            f"Начата: {_moscow_time(data['started_at'])}\n"
            f"Завершена: {_moscow_time(data['completed_at'])}\n"
            f"Проверено: {data['checked_count']}\n"
            f"Скорректировано: {data['adjusted_count']}\n"
            f"Пропущено: {data['skipped_count']}\n"
            f"Списано по корректировке: {quantity_text(Decimal(data['total_negative']))}\n"
            f"Добавлено по корректировке: {quantity_text(Decimal(data['total_positive']))}",
        )
        return
    await state.set_state(InventoryCountFlow.actual)
    await state.update_data(session_id=session_id, product_id=item["product_id"], count_mode="session")
    await _send(
        callback,
        f"Полная инвентаризация\n"
        f"{item['category_name']} · {item['brand_name']} · {item['name']}\n"
        f"Системный остаток: {quantity_text(Decimal(item['system_balance']))} {item['unit']}\n\n"
        "Введите фактический остаток или выберите действие:",
        session_item_keyboard(session_id, int(item["product_id"])),
    )


@router.message(InventoryCountFlow.actual)
async def inventory_actual_message(
    message: Message, settings: Settings, state: FSMContext, service: SalonService
) -> None:
    if not await _allowed_message(message, settings):
        return
    try:
        actual = parse_decimal(message.text or "")
        if not actual.is_finite() or actual < 0:
            raise ValueError("Фактический остаток не может быть отрицательным.")
    except ValueError as error:
        await message.answer(str(error))
        return
    data = await state.get_data()
    if data.get("count_mode") == "session":
        session_id = int(data["session_id"])
        product_id = int(data["product_id"])
        await service.inventory_session_action(
            session_id=session_id,
            product_id=product_id,
            admin_user_id=message.from_user.id,
            actual_balance=actual,
        )
        callback = _message_callback_adapter(message)
        await _send_next_session_product(callback, state, service, session_id)
        return

    product = data.get("product")
    if not product:
        await state.clear()
        await message.answer("Инвентаризация устарела. Начните заново.")
        return
    before, after, delta = await service.adjust_inventory(
        product_id=int(product["id"]),
        actual_balance=actual,
        admin_user_id=message.from_user.id,
    )
    await state.clear()
    await message.answer(
        f"Инвентаризация записана.\n"
        f"Система: {quantity_text(before)} {product['unit']}\n"
        f"Факт: {quantity_text(after)} {product['unit']}\n"
        f"Корректировка: {quantity_text(delta)} {product['unit']}"
    )


class _MessageCallback:
    """Small adapter so the shared session renderer can answer a new message."""

    def __init__(self, message: Message) -> None:
        self.message = message
        self.from_user = message.from_user


def _message_callback_adapter(message: Message) -> CallbackQuery:
    # The renderer only needs .message and .from_user and never acknowledges
    # this synthetic update.
    return _MessageCallback(message)  # type: ignore[return-value]


@router.callback_query(F.data.startswith("session:same:"))
async def session_no_change(
    callback: CallbackQuery, settings: Settings, state: FSMContext, service: SalonService
) -> None:
    if not await _allowed(callback, settings):
        return
    try:
        _, _, session_raw, product_raw = callback.data.split(":")
        session_id, product_id = int(session_raw), int(product_raw)
    except (ValueError, AttributeError):
        await safe_answer(callback, "Позиция инвентаризации устарела.")
        return
    await service.inventory_session_action(
        session_id=session_id,
        product_id=product_id,
        admin_user_id=callback.from_user.id,
        actual_balance=None,
    )
    await safe_answer(callback, "Без изменений.")
    await _send_next_session_product(callback, state, service, session_id)


@router.callback_query(F.data.startswith("session:skip:"))
async def session_skip(
    callback: CallbackQuery, settings: Settings, state: FSMContext, service: SalonService
) -> None:
    if not await _allowed(callback, settings):
        return
    try:
        _, _, session_raw, product_raw = callback.data.split(":")
        session_id, product_id = int(session_raw), int(product_raw)
    except (ValueError, AttributeError):
        await safe_answer(callback, "Позиция инвентаризации устарела.")
        return
    await service.inventory_session_action(
        session_id=session_id,
        product_id=product_id,
        admin_user_id=callback.from_user.id,
        actual_balance=None,
        skip=True,
    )
    await safe_answer(callback, "Позиция пропущена.")
    await _send_next_session_product(callback, state, service, session_id)


@router.callback_query(F.data.startswith("session:finish:"))
async def session_finish(
    callback: CallbackQuery, settings: Settings, state: FSMContext, service: SalonService
) -> None:
    if not await _allowed(callback, settings):
        return
    try:
        session_id = int(callback.data.rsplit(":", 1)[1])
    except (ValueError, AttributeError):
        await safe_answer(callback, "Сессия не найдена.")
        return
    result = await service.finish_inventory_session(session_id, callback.from_user.id)
    data = result.get("summary")
    await state.clear()
    if not data:
        await safe_answer(callback, "Инвентаризация уже завершена.")
        return
    await _send(
        callback,
        "Инвентаризация завершена.\n"
        f"Начата: {_moscow_time(data['started_at'])}\n"
        f"Завершена: {_moscow_time(data['completed_at'])}\n"
        f"Проверено: {data['checked_count']}\n"
        f"Скорректировано: {data['adjusted_count']}\n"
        f"Пропущено: {data['skipped_count']}\n"
        f"Списано по корректировке: {quantity_text(Decimal(data['total_negative']))}\n"
        f"Добавлено по корректировке: {quantity_text(Decimal(data['total_positive']))}",
    )


@router.callback_query(F.data == "adm:add_product")
async def add_product_start(
    callback: CallbackQuery,
    settings: Settings,
    state: FSMContext,
    service: SalonService,
) -> None:
    if not await _allowed(callback, settings):
        return
    await state.clear()
    await state.set_state(AddProductFlow.category)
    categories = await service.categories()
    await _send(
        callback,
        "Создание складской позиции · выберите категорию:",
        rows_keyboard(
            [(row["name"], f"adm:addcat:{row['id']}") for row in categories],
            back="adm:stock",
        ),
    )


@router.callback_query(F.data.startswith("adm:addcat:"))
async def add_product_category(
    callback: CallbackQuery, settings: Settings, state: FSMContext
) -> None:
    if not await _allowed(callback, settings):
        return
    try:
        category_id = int(callback.data.rsplit(":", 1)[1])
    except (ValueError, AttributeError):
        await safe_answer(callback, "Категория не найдена.")
        return
    await state.update_data(category_id=category_id)
    await state.set_state(AddProductFlow.brand)
    await _send(callback, "Введите бренд.")


@router.message(AddProductFlow.brand)
async def add_product_brand(
    message: Message, settings: Settings, state: FSMContext
) -> None:
    if not await _allowed_message(message, settings):
        return
    brand = (message.text or "").strip()
    if not brand or len(brand) > 80:
        await message.answer("Введите название бренда (не более 80 символов).")
        return
    await state.update_data(brand_name=brand)
    await state.set_state(AddProductFlow.name)
    await message.answer("Введите название материала или оттенок.")


@router.message(AddProductFlow.name)
async def add_product_name(
    message: Message, settings: Settings, state: FSMContext
) -> None:
    if not await _allowed_message(message, settings):
        return
    name = (message.text or "").strip()
    if not name or len(name) > 120:
        await message.answer("Введите название (не более 120 символов).")
        return
    await state.update_data(product_name=name)
    await state.set_state(AddProductFlow.unit)
    await message.answer(
        "Выберите единицу:",
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(text="граммы", callback_data="adm:addunit:g"),
                    InlineKeyboardButton(text="миллилитры", callback_data="adm:addunit:ml"),
                    InlineKeyboardButton(text="штуки", callback_data="adm:addunit:pcs"),
                ]
            ]
        ),
    )


@router.callback_query(F.data.startswith("adm:addunit:"))
async def add_product_unit(
    callback: CallbackQuery, settings: Settings, state: FSMContext
) -> None:
    if not await _allowed(callback, settings):
        return
    unit = callback.data.rsplit(":", 1)[1]
    if unit not in {"g", "ml", "pcs"}:
        await safe_answer(callback, "Единица измерения не поддерживается.")
        return
    await state.update_data(unit=unit)
    await state.set_state(AddProductFlow.package_quantity)
    await _send(callback, f"Введите массу/объём упаковки в {unit} или «-», если неизвестна.")


@router.message(AddProductFlow.package_quantity)
async def add_product_package_quantity(
    message: Message, settings: Settings, state: FSMContext
) -> None:
    if not await _allowed_message(message, settings):
        return
    raw = (message.text or "").strip()
    if raw == "-":
        package_quantity = None
    else:
        try:
            parsed = parse_decimal(raw)
            if parsed <= 0:
                raise ValueError("Значение должно быть больше нуля.")
            package_quantity = decimal_text(parsed)
        except ValueError as error:
            await message.answer(str(error))
            return
    await state.update_data(package_quantity=package_quantity)
    await state.set_state(AddProductFlow.purchase_price)
    await message.answer("Введите закупочную цену за единицу или «-», если неизвестна.")


@router.message(AddProductFlow.purchase_price)
async def add_product_purchase_price(
    message: Message, settings: Settings, state: FSMContext
) -> None:
    if not await _allowed_message(message, settings):
        return
    raw = (message.text or "").strip()
    if raw == "-":
        price = None
    else:
        try:
            price = parse_decimal(raw)
            if not price.is_finite() or price < 0:
                raise ValueError("Закупочная цена не может быть отрицательной.")
        except ValueError as error:
            await message.answer(str(error))
            return
    await state.update_data(purchase_price=decimal_text(price) if price is not None else None)
    await state.set_state(AddProductFlow.calculation_rate)
    await message.answer("Введите расчётную ставку за единицу или «-», если не нужна.")


@router.message(AddProductFlow.calculation_rate)
async def add_product_rate(
    message: Message, settings: Settings, state: FSMContext
) -> None:
    if not await _allowed_message(message, settings):
        return
    raw = (message.text or "").strip()
    if raw == "-":
        rate = None
    else:
        try:
            rate = parse_decimal(raw)
            if not rate.is_finite() or rate < 0:
                raise ValueError("Расчётная ставка не может быть отрицательной.")
        except ValueError as error:
            await message.answer(str(error))
            return
    await state.update_data(calculation_rate=decimal_text(rate) if rate is not None else None)
    await state.set_state(AddProductFlow.initial_stock)
    await message.answer("Введите начальный остаток; если его нет, введите 0.")


@router.message(AddProductFlow.initial_stock)
async def add_product_stock(
    message: Message, settings: Settings, state: FSMContext
) -> None:
    if not await _allowed_message(message, settings):
        return
    try:
        stock = parse_decimal(message.text or "")
    except ValueError as error:
        await message.answer(str(error))
        return
    await state.update_data(initial_stock=decimal_text(stock))
    await state.set_state(AddProductFlow.visibility)
    await message.answer(
        "Показывать материал мастерам в калькуляторе?",
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(text="Да", callback_data="adm:addvisible:yes"),
                    InlineKeyboardButton(text="Нет", callback_data="adm:addvisible:no"),
                ]
            ]
        ),
    )


@router.callback_query(F.data.startswith("adm:addvisible:"))
async def add_product_visibility(
    callback: CallbackQuery,
    settings: Settings,
    state: FSMContext,
    service: SalonService,
) -> None:
    if not await _allowed(callback, settings):
        return
    data = await state.get_data()
    if not data.get("category_id"):
        await safe_answer(callback, "Шаг создания устарел. Начните заново.")
        return
    visible = callback.data.endswith(":yes")
    try:
        product_id = await service.create_product(
            category_id=int(data["category_id"]),
            brand_name=str(data["brand_name"]),
            name=str(data["product_name"]),
            unit=str(data["unit"]),
            package_quantity=Decimal(data["package_quantity"])
            if data.get("package_quantity")
            else None,
            purchase_price_per_unit=Decimal(data["purchase_price"])
            if data.get("purchase_price")
            else None,
            calculation_rate=Decimal(data["calculation_rate"])
            if data.get("calculation_rate")
            else None,
            initial_stock=Decimal(data["initial_stock"]),
            visible_to_masters=visible,
            admin_user_id=callback.from_user.id,
        )
    except Exception:
        logger.exception("Failed to create product")
        await safe_answer(callback, "Не удалось создать позицию.")
        return
    await state.clear()
    await _send(callback, f"Позиция создана. ID: {product_id}. Начальный остаток внесён в журнал.")


@router.callback_query(F.data == "adm:prices")
async def price_rules(
    callback: CallbackQuery, settings: Settings, service: SalonService
) -> None:
    if not await _allowed(callback, settings):
        return
    await _show_pricing_rules(callback, service, page=0)


async def _show_pricing_rules(
    callback: CallbackQuery, service: SalonService, *, page: int
) -> None:
    page_size = 18
    rows = await service.pricing_rules(
        offset=page * page_size, limit=page_size + 1
    )
    if not rows:
        await _send(callback, "Правила цен пока не заданы.")
        return
    more = len(rows) > page_size
    rows = rows[:page_size]
    lines = ["Расчётные ставки (изменение откроет отдельный override позиции):"]
    buttons = []
    for row in rows:
        product_label = f" · {row['product_name']}" if row["product_name"] else ""
        lines.append(
            f"• {row['category_name']} / {row['brand_name']}{product_label}: "
            f"{money_text(Decimal(row['calculation_rate']))} ₽/{row['target_unit'] or 'ед.'}"
        )
        if row["target_product_id"]:
            buttons.append(
                [
                    InlineKeyboardButton(
                        text=f"{row['brand_name']}{product_label} · изменить",
                        callback_data=f"adm:price:{row['target_product_id']}",
                    )
                ]
            )
    navigation = []
    if page:
        navigation.append(
            InlineKeyboardButton(
                text="‹ Назад", callback_data=f"adm:prices:{page - 1}"
            )
        )
    if more:
        navigation.append(
            InlineKeyboardButton(
                text="Далее ›", callback_data=f"adm:prices:{page + 1}"
            )
        )
    if navigation:
        buttons.append(navigation)
    buttons.append([InlineKeyboardButton(text="⬅️ К панели", callback_data="adm:home")])
    await _send(callback, "\n".join(lines), InlineKeyboardMarkup(inline_keyboard=buttons))


@router.callback_query(F.data.startswith("adm:prices:"))
async def price_rules_page(
    callback: CallbackQuery, settings: Settings, service: SalonService
) -> None:
    if not await _allowed(callback, settings):
        return
    try:
        page = max(0, int(callback.data.rsplit(":", 1)[1]))
    except (ValueError, AttributeError):
        await safe_answer(callback, "Список ставок устарел.")
        return
    await _show_pricing_rules(callback, service, page=page)


@router.callback_query(F.data.startswith("adm:price:"))
async def price_product_from_detail(
    callback: CallbackQuery,
    settings: Settings,
    state: FSMContext,
    service: SalonService,
) -> None:
    if not await _allowed(callback, settings):
        return
    try:
        product_id = int(callback.data.rsplit(":", 1)[1])
    except (ValueError, AttributeError):
        await safe_answer(callback, "Материал не найден.")
        return
    await _start_price(callback, state, service, product_id)


async def _start_price(
    callback: CallbackQuery,
    state: FSMContext,
    service: SalonService,
    product_id: int,
) -> None:
    product = await service.get_product(product_id)
    if not product or not product["is_active"]:
        await safe_answer(callback, "Материал не найден или архивирован.")
        return
    await state.clear()
    await state.update_data(product_id=product_id, product=product)
    await state.set_state(PriceFlow.rate)
    await _send(
        callback,
        f"{product['brand_name']} · {product['name']}\n"
        "Введите новую расчётную ставку за единицу. "
        "Старые расчёты останутся без изменений.",
    )


@router.message(PriceFlow.rate)
async def set_price_rate(
    message: Message, settings: Settings, state: FSMContext, service: SalonService
) -> None:
    if not await _allowed_message(message, settings):
        return
    try:
        rate = parse_decimal(message.text or "")
        if not rate.is_finite() or rate < 0:
            raise ValueError("Расчётная ставка не может быть отрицательной.")
    except ValueError as error:
        await message.answer(str(error))
        return
    data = await state.get_data()
    product = data.get("product")
    if not product:
        await state.clear()
        await message.answer("Изменение цены устарело. Начните заново.")
        return
    previous, updated = await service.change_price(
        int(product["id"]), rate, message.from_user.id
    )
    await state.clear()
    previous_text = f"{money_text(previous)} ₽" if previous is not None else "не задана"
    await message.answer(
        f"Расчётная ставка обновлена: {previous_text} → {money_text(updated)} ₽/"
        f"{product['unit']}.\nСтарые расчёты не изменены."
    )


@router.callback_query(F.data.startswith("adm:editmenu:"))
async def edit_product_menu(
    callback: CallbackQuery, settings: Settings, service: SalonService
) -> None:
    if not await _allowed(callback, settings):
        return
    try:
        product_id = int(callback.data.rsplit(":", 1)[1])
    except (ValueError, AttributeError):
        await safe_answer(callback, "Материал не найден.")
        return
    await _show_edit_menu(callback, service, product_id)


async def _show_edit_menu(
    callback: CallbackQuery, service: SalonService, product_id: int
) -> None:
    product = await service.get_product(product_id)
    if not product:
        await safe_answer(callback, "Материал не найден.")
        return
    buttons = [
        [
            InlineKeyboardButton(text="Название", callback_data=f"adm:edit:name:{product_id}"),
            InlineKeyboardButton(text="Бренд", callback_data=f"adm:edit:brand:{product_id}"),
        ],
        [
            InlineKeyboardButton(text="Категория", callback_data=f"adm:edit:category:{product_id}"),
            InlineKeyboardButton(text="Масса упаковки", callback_data=f"adm:edit:package_quantity:{product_id}"),
        ],
        [
            InlineKeyboardButton(text="Закупочная цена", callback_data=f"adm:edit:purchase_price_per_unit:{product_id}"),
            InlineKeyboardButton(text="Порог остатка", callback_data=f"adm:edit:low_stock_threshold:{product_id}"),
        ],
        [
            InlineKeyboardButton(
                text=f"Мастерам: {'да' if product['visible_to_masters'] else 'нет'}",
                callback_data=f"adm:toggle_visibility:{product_id}",
            ),
            InlineKeyboardButton(text="Расчётная ставка", callback_data=f"adm:price:{product_id}"),
        ],
        [InlineKeyboardButton(text="⬅️ К позиции", callback_data=f"adm:stock:product:{product_id}")],
    ]
    await _send(callback, f"Редактирование: {product['name']}", InlineKeyboardMarkup(inline_keyboard=buttons))


@router.callback_query(F.data.startswith("adm:edit:"))
async def edit_product_field(
    callback: CallbackQuery, settings: Settings, state: FSMContext, service: SalonService
) -> None:
    if not await _allowed(callback, settings):
        return
    try:
        _, _, field, product_raw = callback.data.split(":")
        product_id = int(product_raw)
    except (ValueError, AttributeError):
        await safe_answer(callback, "Поле не найдено.")
        return
    if field == "category":
        categories = await service.categories()
        await _send(
            callback,
            "Выберите новую категорию:",
            rows_keyboard(
                [(row["name"], f"adm:setcat:{product_id}:{row['id']}") for row in categories],
                back=f"adm:editmenu:{product_id}",
            ),
        )
        return
    prompts = {
        "name": "Введите новое название.",
        "brand": "Введите новое название бренда.",
        "package_quantity": "Введите массу/объём упаковки или «-», чтобы очистить.",
        "purchase_price_per_unit": "Введите закупочную цену за единицу или «-», если неизвестна.",
        "low_stock_threshold": "Введите минимальный остаток или «-», чтобы отключить порог.",
    }
    if field not in prompts:
        await safe_answer(callback, "Это поле нельзя изменить.")
        return
    await state.clear()
    await state.set_state(EditProductFlow.value)
    await state.update_data(product_id=product_id, field=field)
    await _send(callback, prompts[field])


@router.callback_query(F.data.startswith("adm:setcat:"))
async def set_product_category(
    callback: CallbackQuery, settings: Settings, service: SalonService
) -> None:
    if not await _allowed(callback, settings):
        return
    try:
        _, _, product_raw, category_raw = callback.data.split(":")
        product_id, category_id = int(product_raw), int(category_raw)
    except (ValueError, AttributeError):
        await safe_answer(callback, "Категория не найдена.")
        return
    await service.set_product_category(product_id, category_id)
    await _send(callback, "Категория обновлена. Старые расчёты не изменены.")


@router.callback_query(F.data.startswith("adm:toggle_visibility:"))
async def toggle_visibility(
    callback: CallbackQuery, settings: Settings, service: SalonService
) -> None:
    if not await _allowed(callback, settings):
        return
    try:
        product_id = int(callback.data.rsplit(":", 1)[1])
    except (ValueError, AttributeError):
        await safe_answer(callback, "Материал не найден.")
        return
    product = await service.get_product(product_id)
    if not product:
        await safe_answer(callback, "Материал не найден.")
        return
    await service.set_product_field(
        product_id, "visible_to_masters", int(not product["visible_to_masters"])
    )
    await _send(callback, "Доступ мастеров обновлён.")


@router.message(EditProductFlow.value)
async def save_product_field(
    message: Message, settings: Settings, state: FSMContext, service: SalonService
) -> None:
    if not await _allowed_message(message, settings):
        return
    data = await state.get_data()
    field = data.get("field")
    product_id = data.get("product_id")
    raw = (message.text or "").strip()
    if not product_id:
        await state.clear()
        await message.answer("Изменение устарело. Начните заново.")
        return
    if field == "name":
        if not raw or len(raw) > 120:
            await message.answer("Название должно содержать 1–120 символов.")
            return
        value = raw
    elif field == "brand":
        try:
            await service.set_product_brand(int(product_id), raw)
        except ValueError as error:
            await message.answer(str(error))
            return
        await state.clear()
        await message.answer("Бренд обновлён. История расчётов и движений сохранена.")
        return
    elif field in {"package_quantity", "purchase_price_per_unit", "low_stock_threshold"}:
        if raw == "-":
            value = None
        else:
            try:
                parsed = parse_decimal(raw)
                if not parsed.is_finite() or (field == "package_quantity" and parsed <= 0):
                    raise ValueError("Масса упаковки должна быть больше нуля.")
                if field != "package_quantity" and parsed < 0:
                    raise ValueError("Цена или порог остатка не может быть отрицательным.")
                value = decimal_text(parsed)
            except ValueError as error:
                await message.answer(str(error))
                return
    else:
        await state.clear()
        await message.answer("Это поле нельзя изменить.")
        return
    await service.set_product_field(int(product_id), str(field), value)
    await state.clear()
    await message.answer("Данные материала обновлены.")


@router.callback_query(F.data == "adm:history")
async def global_history(
    callback: CallbackQuery, settings: Settings, service: SalonService
) -> None:
    if not await _allowed(callback, settings):
        return
    await _global_history_page(callback, service, 0)


async def _global_history_page(
    callback: CallbackQuery, service: SalonService, page: int
) -> None:
    calculations = await service.history(user_id=None, offset=page * 10, limit=11)
    more = len(calculations) > 10
    calculations = calculations[:10]
    if not calculations:
        await _send(callback, "Расчётов пока нет.")
        return
    lines = ["Общая история расчётов:"]
    buttons = []
    for calculation in calculations:
        lines.append(
            f"• №{calculation['id']} · {calculation['full_name']} · "
            f"{money_text(Decimal(calculation['total_cost']))} ₽ · "
            f"{_moscow_time(calculation['created_at'])}"
        )
        buttons.append(
            [
                InlineKeyboardButton(
                    text=f"№{calculation['id']} · детали",
                    callback_data=f"history:detail:{calculation['id']}",
                )
            ]
        )
    nav = []
    if page:
        nav.append(
            InlineKeyboardButton(text="‹ Назад", callback_data=f"adm:historypage:{page-1}")
        )
    if more:
        nav.append(
            InlineKeyboardButton(text="Далее ›", callback_data=f"adm:historypage:{page+1}")
        )
    if nav:
        buttons.append(nav)
    buttons.append([InlineKeyboardButton(text="⬅️ К панели", callback_data="adm:home")])
    await _send(callback, "\n".join(lines), InlineKeyboardMarkup(inline_keyboard=buttons))


@router.callback_query(F.data.startswith("adm:historypage:"))
async def global_history_page(
    callback: CallbackQuery, settings: Settings, service: SalonService
) -> None:
    if not await _allowed(callback, settings):
        return
    try:
        page = max(0, int(callback.data.rsplit(":", 1)[1]))
    except (ValueError, AttributeError):
        page = 0
    await _global_history_page(callback, service, page)


@router.callback_query(F.data.startswith("adm:movements:"))
async def movement_page(
    callback: CallbackQuery, settings: Settings, service: SalonService
) -> None:
    if not await _allowed(callback, settings):
        return
    try:
        page = max(0, int(callback.data.rsplit(":", 1)[1]))
    except (ValueError, AttributeError):
        page = 0
    rows = await service.movements(offset=page * 12, limit=13)
    more = len(rows) > 12
    rows = rows[:12]
    if not rows:
        await _send(callback, "Движений склада пока нет.")
        return
    lines = ["Движения склада:"]
    for row in rows:
        delta = Decimal(row["quantity_delta"])
        lines.append(
            f"• {row['movement_type']} · {row['product_name']} · "
            f"{quantity_text(delta)} {row['unit']} · "
            f"{quantity_text(Decimal(row['balance_before']))} → "
            f"{quantity_text(Decimal(row['balance_after']))} · "
            f"{_moscow_time(row['created_at'])}"
        )
    buttons = []
    nav = []
    if page:
        nav.append(
            InlineKeyboardButton(text="‹ Назад", callback_data=f"adm:movements:{page-1}")
        )
    if more:
        nav.append(
            InlineKeyboardButton(text="Далее ›", callback_data=f"adm:movements:{page+1}")
        )
    if nav:
        buttons.append(nav)
    buttons.append([InlineKeyboardButton(text="⬅️ К панели", callback_data="adm:home")])
    await _send(callback, "\n".join(lines), InlineKeyboardMarkup(inline_keyboard=buttons))


@router.callback_query(F.data == "adm:exports")
async def exports_menu(callback: CallbackQuery, settings: Settings) -> None:
    if not await _allowed(callback, settings):
        return
    await _send(
        callback,
        "Резервная копия и выгрузки доступны только администратору.",
        InlineKeyboardMarkup(
            inline_keyboard=[
                [InlineKeyboardButton(text="💾 База SQLite", callback_data="adm:export:backup")],
                [InlineKeyboardButton(text="📦 Остатки CSV", callback_data="adm:export:inventory")],
                [InlineKeyboardButton(text="📜 Движения CSV", callback_data="adm:export:movements")],
                [InlineKeyboardButton(text="📊 Расчёты CSV", callback_data="adm:export:calculations")],
                [InlineKeyboardButton(text="⬅️ К панели", callback_data="adm:home")],
            ]
        ),
    )


@router.callback_query(F.data.startswith("adm:export:"))
async def send_export(
    callback: CallbackQuery,
    settings: Settings,
    service: SalonService,
) -> None:
    if not await _allowed(callback, settings):
        return
    kind = callback.data.rsplit(":", 1)[1]
    if kind not in {"backup", "inventory", "movements", "calculations"}:
        await safe_answer(callback, "Тип выгрузки неизвестен.")
        return
    await safe_answer(callback, "Готовлю файл…")
    with tempfile.TemporaryDirectory(prefix="nefor-export-") as temp:
        suffix = timestamp()
        if kind == "backup":
            path = Path(temp) / f"nefor-backup-{suffix}.sqlite3"
            await service.create_database_backup(path)
            caption = "Согласованная резервная копия базы SQLite."
        else:
            path = Path(temp) / f"nefor-{kind}-{suffix}.csv"
            await service.export_csv(kind, path)
            caption = "Экспорт данных. CSV с разделителем «;»."
        try:
            await callback.message.answer_document(
                FSInputFile(path), caption=caption
            )
        except Exception:
            logger.exception("Failed to send admin export")
            await callback.message.answer("Не удалось отправить файл. Повторите попытку.")


@router.callback_query(F.data == "adm:settings")
async def admin_settings(callback: CallbackQuery, settings: Settings) -> None:
    if not await _allowed(callback, settings):
        return
    await _send(
        callback,
        "Настройки бота\n"
        f"Часовой пояс интерфейса: Europe/Moscow\n"
        f"ID администратора: {settings.admin_user_id}\n"
        f"Режим приложения: {settings.app_env}\n"
        "Хранилище данных и секреты задаются переменными окружения сервера.\n"
        "При работе через Render база может находиться в Neon.",
    )
