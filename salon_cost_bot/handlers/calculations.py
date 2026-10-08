from __future__ import annotations

import logging
from decimal import Decimal
from uuid import uuid4

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message

from salon_cost_bot.domain import decimal_text, money_text, parse_decimal, quantity_text
from salon_cost_bot.keyboards import (
    BTN_NEW,
    acid_action_keyboard,
    calc_action_keyboard,
    page_keyboard,
    rows_keyboard,
)
from salon_cost_bot.services import CalculationLine, SalonService
from salon_cost_bot.payouts import calculate_payout
from salon_cost_bot.handlers.common import safe_answer
from salon_cost_bot.handlers.states import CalculationFlow

logger = logging.getLogger(__name__)
router = Router(name="calculations")
PAGE_SIZE = 16


async def _category_id(service: SalonService, key: str) -> int | None:
    return await service.category_id(key)


async def _begin_calculation(
    message: Message, state: FSMContext, service: SalonService
) -> None:
    await state.clear()
    await state.set_state(CalculationFlow.selecting)
    await state.update_data(items=[], token=uuid4().hex, acid_phase=0)
    await _send_categories(message, state, service)


async def _send_categories(
    message: Message, state: FSMContext, service: SalonService
) -> None:
    categories = await service.categories(master_only=True)
    items = [
        (f"{row['name']} · {row['product_count']}", f"calc:cat:{row['id']}")
        for row in categories
    ]
    items.append(("Кислотная смывка · 2 фазы", "calc:acid"))
    await message.answer(
        "Выберите категорию материала:",
        reply_markup=rows_keyboard(items, columns=2),
    )


async def _show_brands(
    callback: CallbackQuery,
    state: FSMContext,
    service: SalonService,
    category_id: int,
    phase: int = 0,
) -> None:
    brands = await service.brands(category_id, master_only=True)
    if not brands:
        await safe_answer(callback, "В этой категории пока нет доступных материалов.")
        return
    await state.update_data(
        category_id=category_id, brand_id=None, page=0, acid_phase=phase
    )
    prefix = "acid" if phase else "calc"
    items = [
        (f"{row['name']} · {row['product_count']}", f"{prefix}:brand:{row['id']}")
        for row in brands
    ]
    if callback.message:
        await callback.message.answer(
            "Выберите бренд:",
            reply_markup=rows_keyboard(items, back="calc:back_categories"),
        )
    await safe_answer(callback)


async def _show_products(
    callback: CallbackQuery,
    state: FSMContext,
    service: SalonService,
    brand_id: int,
    *,
    page: int = 0,
) -> None:
    data = await state.get_data()
    category_id = data.get("category_id")
    phase = int(data.get("acid_phase", 0))
    if not category_id:
        await safe_answer(callback, "Шаг выбора устарел. Начните расчёт заново.")
        return
    products = await service.products(
        int(category_id),
        brand_id,
        master_only=True,
        limit=PAGE_SIZE,
        offset=page * PAGE_SIZE,
    )
    if not products and page > 0:
        page -= 1
        products = await service.products(
            int(category_id),
            brand_id,
            master_only=True,
            limit=PAGE_SIZE,
            offset=page * PAGE_SIZE,
        )
    total = await service.product_count(
        int(category_id), brand_id, master_only=True
    )
    more = (page + 1) * PAGE_SIZE < total
    await state.update_data(brand_id=brand_id, page=page)
    items = [
        (f"{row['name']} ({row['unit']})", f"calc:product:{row['id']}")
        for row in products
    ]
    if not products:
        await safe_answer(callback, "В этом бренде пока нет материалов.")
        return
    nav = []
    if page > 0:
        nav.append(
            InlineKeyboardButton(
                text="‹ Назад",
                callback_data=f"calc:page:{brand_id}:{page - 1}",
            )
        )
    if more:
        nav.append(
            InlineKeyboardButton(
                text="Далее ›",
                callback_data=f"calc:page:{brand_id}:{page + 1}",
            )
        )
    keyboard = page_keyboard(
        items,
        page=0,
        more=False,
        back="calc:back_brands",
        columns=1,
    )
    if nav:
        rows = [list(row) for row in keyboard.inline_keyboard]
        rows.insert(-2, nav)
        keyboard = InlineKeyboardMarkup(inline_keyboard=rows)
    if callback.message:
        prompt = "Выберите продукт или оттенок:"
        if phase == 1:
            prompt = "Выберите продукт для фазы 2:"
        await callback.message.answer(prompt, reply_markup=keyboard)
    await safe_answer(callback)


async def _show_actions(message: Message, items: list[dict[str, str]]) -> None:
    lines = ["Добавлено в расчёт:"]
    for item in items:
        lines.append(
            f"• {item['brand']} {item['name']} — "
            f"{quantity_text(Decimal(item['quantity']))} {item['unit']}"
        )
    await message.answer("\n".join(lines), reply_markup=calc_action_keyboard())


async def _add_item(message: Message, state: FSMContext, item: dict[str, str]) -> None:
    data = await state.get_data()
    items = list(data.get("items", []))
    items.append(item)
    await state.update_data(items=items, acid_phase=0)
    await _show_actions(message, items)


@router.message(F.text == BTN_NEW)
async def start_calculation(
    message: Message, state: FSMContext, service: SalonService
) -> None:
    await _begin_calculation(message, state, service)


@router.callback_query(F.data == "calc:back_categories")
async def back_categories(
    callback: CallbackQuery, state: FSMContext, service: SalonService
) -> None:
    if callback.message:
        await state.set_state(CalculationFlow.selecting)
        await state.update_data(acid_phase=0)
        await _send_categories(callback.message, state, service)
    await safe_answer(callback)


@router.callback_query(F.data.startswith("calc:cat:"))
async def select_category(
    callback: CallbackQuery, state: FSMContext, service: SalonService
) -> None:
    try:
        category_id = int(callback.data.rsplit(":", 1)[1])
    except (ValueError, AttributeError):
        await safe_answer(callback, "Категория недоступна.")
        return
    await state.set_state(CalculationFlow.selecting)
    await _show_brands(callback, state, service, category_id)


@router.callback_query(F.data == "calc:acid")
async def begin_acid_remover(
    callback: CallbackQuery, state: FSMContext, service: SalonService
) -> None:
    phase_one = await _category_id(service, "acid_phase1")
    if phase_one is None:
        await safe_answer(callback, "Кислотная смывка пока недоступна.")
        return
    await state.set_state(CalculationFlow.selecting)
    await _show_brands(callback, state, service, phase_one, phase=1)


@router.callback_query(F.data.startswith("calc:brand:"))
async def select_brand(
    callback: CallbackQuery, state: FSMContext, service: SalonService
) -> None:
    try:
        brand_id = int(callback.data.rsplit(":", 1)[1])
    except (ValueError, AttributeError):
        await safe_answer(callback, "Бренд недоступен.")
        return
    await _show_products(callback, state, service, brand_id)


@router.callback_query(F.data.startswith("acid:brand:"))
async def select_acid_brand(
    callback: CallbackQuery, state: FSMContext, service: SalonService
) -> None:
    try:
        brand_id = int(callback.data.rsplit(":", 1)[1])
    except (ValueError, AttributeError):
        await safe_answer(callback, "Бренд недоступен.")
        return
    await _show_products(callback, state, service, brand_id)


@router.callback_query(F.data.startswith("calc:page:"))
async def product_page(
    callback: CallbackQuery, state: FSMContext, service: SalonService
) -> None:
    try:
        _, _, brand_raw, page_raw = callback.data.split(":")
        brand_id, page = int(brand_raw), int(page_raw)
    except (ValueError, AttributeError):
        await safe_answer(callback, "Список устарел.")
        return
    await _show_products(callback, state, service, brand_id, page=page)


@router.callback_query(F.data == "calc:back_brands")
async def back_brands(
    callback: CallbackQuery, state: FSMContext, service: SalonService
) -> None:
    data = await state.get_data()
    category_id = data.get("category_id")
    if category_id and callback.message:
        await _show_brands(callback, state, service, int(category_id), int(data.get("acid_phase", 0)))
    else:
        await safe_answer(callback, "Начните расчёт заново.")


@router.callback_query(F.data.startswith("calc:product:"))
async def select_product(
    callback: CallbackQuery, state: FSMContext, service: SalonService
) -> None:
    try:
        product_id = int(callback.data.rsplit(":", 1)[1])
    except (ValueError, AttributeError):
        await safe_answer(callback, "Материал недоступен.")
        return
    product = await service.get_product(product_id)
    data = await state.get_data()
    if (
        product is None
        or not product["is_active"]
        or not product["visible_to_masters"]
        or product["category_id"] != data.get("category_id")
        or product["brand_id"] != data.get("brand_id")
    ):
        await safe_answer(callback, "Этот материал больше недоступен.")
        return
    await state.update_data(product_id=product_id, product=product)
    await state.set_state(CalculationFlow.entering_quantity)
    unit = {"g": "граммах", "ml": "миллилитрах", "pcs": "штуках"}.get(
        product["unit"], product["unit"]
    )
    phase = int(data.get("acid_phase", 0))
    prompt = f"Введите количество материала в {unit}."
    if phase == 1:
        prompt = f"Фаза 1 — {product['name']}. Введите количество в {unit}."
    elif phase == 2:
        prompt = f"Фаза 2 — {product['name']}. Введите количество в {unit}."
    if callback.message:
        await callback.message.answer(prompt + "\nМожно использовать запятую: 12,5.")
    await safe_answer(callback)


@router.message(CalculationFlow.entering_quantity)
async def enter_quantity(
    message: Message, state: FSMContext, service: SalonService
) -> None:
    try:
        quantity = parse_decimal(message.text or "")
        if quantity <= 0:
            raise ValueError("Количество должно быть больше нуля.")
    except ValueError as error:
        await message.answer(str(error))
        return

    data = await state.get_data()
    product = data.get("product")
    if not product:
        await state.clear()
        await message.answer("Расчёт устарел. Начните новый расчёт командой /start.")
        return
    item = {
        "product_id": str(product["id"]),
        "name": str(product["name"]),
        "brand": str(product["brand_name"]),
        "category": str(product["category_name"]),
        "quantity": decimal_text(quantity),
        "unit": str(product["unit"]),
    }
    phase = int(data.get("acid_phase", 0))
    if phase == 1:
        await state.set_state(CalculationFlow.selecting)
        await state.update_data(acid_phase1=item, acid_phase=2)
        category_id = await _category_id(service, "acid_phase2")
        if category_id is None:
            await message.answer("Не найдена фаза 2 смывки. Обратитесь к администратору.")
            return
        brands = await service.brands(category_id, master_only=True)
        if not brands:
            await message.answer("Фаза 2 смывки пока недоступна.")
            return
        await state.update_data(category_id=category_id, brand_id=None, page=0)
        await message.answer(
            f"Фаза 1 сохранена: {item['name']} — {quantity_text(quantity)} {item['unit']}.\n"
            "Теперь выберите продукт для фазы 2:",
            reply_markup=rows_keyboard(
                [
                    (row["name"], f"acid:brand:{row['id']}")
                    for row in brands
                ],
                back="calc:back_categories",
            ),
        )
        return
    if phase == 2:
        first = data.get("acid_phase1")
        if not first:
            await state.clear()
            await message.answer("Расчёт смывки устарел. Начните заново.")
            return
        combined = [first, item]
        await state.set_state(CalculationFlow.selecting)
        await state.update_data(pending_acid=combined, acid_phase=0)
        await message.answer(
            "Обе фазы указаны:\n"
            f"• Фаза 1: {first['name']} — {quantity_text(Decimal(first['quantity']))} {first['unit']}\n"
            f"• Фаза 2: {item['name']} — {quantity_text(quantity)} {item['unit']}\n"
            "Позиции попадут в расчёт вместе.",
            reply_markup=acid_action_keyboard(),
        )
        return

    await state.set_state(CalculationFlow.selecting)
    await _add_item(message, state, item)


@router.callback_query(F.data == "acid:add")
async def add_acid_pair(callback: CallbackQuery, state: FSMContext) -> None:
    data = await state.get_data()
    pair = data.get("pending_acid")
    if not pair or len(pair) != 2:
        await safe_answer(callback, "Не удалось добавить фазы. Начните расчёт заново.")
        return
    items = list(data.get("items", [])) + list(pair)
    await state.update_data(items=items, pending_acid=None, acid_phase=0)
    if callback.message:
        await _show_actions(callback.message, items)
    await safe_answer(callback, "Обе фазы добавлены.")


@router.callback_query(F.data == "calc:add")
async def add_more(
    callback: CallbackQuery, state: FSMContext, service: SalonService
) -> None:
    await state.set_state(CalculationFlow.selecting)
    await state.update_data(acid_phase=0)
    if callback.message:
        await _send_categories(callback.message, state, service)
    await safe_answer(callback)


@router.callback_query(F.data == "calc:cancel")
async def cancel_calculation(callback: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    if callback.message:
        await callback.message.answer(
            "Расчёт отменён. Склад не изменён."
        )
    await safe_answer(callback)


@router.callback_query(F.data == "calc:finish")
async def finish_calculation(
    callback: CallbackQuery, state: FSMContext, service: SalonService
) -> None:
    data = await state.get_data()
    if not data.get("items") or not data.get("token"):
        await safe_answer(callback, "Добавьте материалы перед завершением.")
        return
    if await state.get_state() != CalculationFlow.selecting.state:
        await safe_answer(callback, "Завершите текущий шаг или отмените расчёт.")
        return
    await state.set_state(CalculationFlow.entering_service_price)
    if callback.message:
        await callback.message.answer(
            "💰 Сколько клиент заплатил за окрашивание?\n"
            "Введите полную стоимость окрашивания в рублях, например: 8000.\n"
            "Стрижку, оплаченную отдельно, здесь не учитывайте."
        )
    await safe_answer(callback)


@router.message(CalculationFlow.entering_service_price)
async def enter_service_price(
    message: Message, state: FSMContext, service: SalonService
) -> None:
    try:
        price = parse_decimal(message.text or "")
        if not price.is_finite() or price <= 0:
            raise ValueError("Введите положительную стоимость окрашивания в рублях.")
        data = await state.get_data()
        raw_items = data.get("items", [])
        if not raw_items or not data.get("token"):
            await state.clear()
            await message.answer("Расчёт устарел. Начните заново.")
            return
        lines = [
            CalculationLine(product_id=int(item["product_id"]),
                            quantity=Decimal(item["quantity"]))
            for item in raw_items
        ]
        # Preview reads current rates and does not change inventory.
        _prepared, materials_total = await service.preview_calculation(lines)
        payout = calculate_payout(price, materials_total)
    except ValueError as error:
        await message.answer(str(error))
        return
    except Exception:
        logger.exception("Coloring payout preview failed")
        await message.answer("Не удалось посчитать стоимость. Попробуйте ещё раз.")
        return

    await state.update_data(service_price=decimal_text(payout.service_price))
    await state.set_state(CalculationFlow.confirming)
    await message.answer(
        "✂️ NEFOR SPOT · Проверьте расчёт\n\n"
        f"Окрашивание: {money_text(payout.service_price)} ₽\n"
        f"Расходники: {money_text(payout.materials_cost)} ₽\n"
        f"После вычета расходников: {money_text(payout.distributable)} ₽\n\n"
        f"Мастер ({money_text(payout.master_percent)}%): "
        f"{money_text(payout.master_share)} ₽\n"
        f"Салон ({money_text(payout.salon_percent)}%): "
        f"{money_text(payout.salon_share)} ₽\n\n"
        f"На расходники в кассу: {money_text(payout.materials_cost)} ₽\n"
        f"💳 ИТОГО ПЕРЕДАТЬ САЛОНУ: {money_text(payout.payable_to_salon)} ₽\n\n"
        "Это сумма, если оплату от клиента получил мастер.\n"
        "Склад спишется только после подтверждения.",
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[
                [InlineKeyboardButton(text="✅ Сохранить и списать", callback_data="calc:confirm_save")],
                [InlineKeyboardButton(text="✏️ Изменить цену", callback_data="calc:change_price")],
                [InlineKeyboardButton(text="❌ Отмена", callback_data="calc:cancel")],
            ]
        ),
    )


@router.callback_query(F.data == "calc:change_price")
async def change_service_price(callback: CallbackQuery, state: FSMContext) -> None:
    if await state.get_state() != CalculationFlow.confirming.state:
        await safe_answer(callback, "Этот расчёт уже неактуален.")
        return
    await state.set_state(CalculationFlow.entering_service_price)
    if callback.message:
        await callback.message.answer("Введите новую стоимость окрашивания в рублях:")
    await safe_answer(callback)


@router.callback_query(F.data == "calc:confirm_save")
async def confirm_calculation(
    callback: CallbackQuery, state: FSMContext, service: SalonService
) -> None:
    if await state.get_state() != CalculationFlow.confirming.state:
        await safe_answer(callback, "Этот расчёт уже сохранён или отменён.")
        return
    data = await state.get_data()
    raw_items = data.get("items", [])
    if not raw_items or not data.get("token") or not data.get("service_price"):
        await safe_answer(callback, "Расчёт устарел. Начните заново.")
        return
    lines = [
        CalculationLine(
            product_id=int(item["product_id"]),
            quantity=Decimal(item["quantity"]),
        )
        for item in raw_items
    ]
    try:
        calculation_id, total, saved_lines, created = await service.complete_calculation(
            completion_token=str(data["token"]),
            telegram_user_id=callback.from_user.id,
            username=callback.from_user.username,
            full_name=callback.from_user.full_name,
            lines=lines,
            service_price=Decimal(data["service_price"]),
        )
        settlement = await service.get_calculation_settlement(calculation_id)
    except ValueError as error:
        await safe_answer(callback, str(error))
        if callback.message:
            await callback.message.answer(str(error) + "\nИзмените цену или проверьте материалы.")
        return
    except Exception:
        logger.exception("Calculation completion failed")
        await safe_answer(callback, "Ошибка сохранения. Проверьте историю перед повтором.")
        if callback.message:
            await callback.message.answer(
                "Не удалось подтвердить результат. Проверьте историю расчётов "
                "перед повторной отправкой."
            )
        return
    result = [f"✅ Расчёт №{calculation_id} сохранён:"]
    for item in saved_lines:
        quantity = Decimal(str(item["quantity"]))
        cost = Decimal(str(item["cost"]))
        result.append(
            f"• {item['brand']} {item['name']} — "
            f"{quantity_text(quantity)} {item['unit']} — {money_text(cost)} ₽"
        )
    result.append(f"\nРасходники: {money_text(total)} ₽")
    if settlement is not None:
        price = Decimal(settlement["service_price"])
        net = Decimal(settlement["distributable"])
        master = Decimal(settlement["master_share"])
        salon = Decimal(settlement["salon_share"])
        payable = Decimal(settlement["payable_to_salon"])
        result.extend([
            f"Окрашивание: {money_text(price)} ₽",
            f"К распределению: {money_text(net)} ₽",
            f"Мастер ({settlement['master_percent']}%): {money_text(master)} ₽",
            f"Салон ({settlement['salon_percent']}%): {money_text(salon)} ₽",
            f"Расходники вернуть: {money_text(total)} ₽",
            f"💳 ИТОГО ПЕРЕДАТЬ САЛОНУ: {money_text(payable)} ₽",
            "(если оплату от клиента получил мастер)",
        ])
    if not created:
        result.append("\nЭтот расчёт уже был сохранён. Повторного списания нет.")
    await state.clear()
    if callback.message:
        await callback.message.answer("\n".join(result))
    await safe_answer(callback, f"Расчёт №{calculation_id} сохранён.")
