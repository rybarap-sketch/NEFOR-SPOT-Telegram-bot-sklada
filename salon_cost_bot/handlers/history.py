from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message

from salon_cost_bot.config import MOSCOW_TZ, Settings
from salon_cost_bot.domain import money_text, quantity_text
from salon_cost_bot.handlers.common import is_admin, safe_answer
from salon_cost_bot.keyboards import BTN_HISTORY, BTN_SUPPLIES
from salon_cost_bot.services import SalonService

router = Router(name="history")
PAGE_SIZE = 8


def _moscow_time(value: str) -> str:
    stamp = datetime.fromisoformat(value)
    if stamp.tzinfo is None:
        stamp = stamp.replace(tzinfo=MOSCOW_TZ)
    return stamp.astimezone(MOSCOW_TZ).strftime("%d.%m.%Y %H:%M")


async def _send_history(
    target: Message | CallbackQuery,
    service: SalonService,
    *,
    owner_id: int | None,
    page: int,
) -> None:
    calculations = await service.history(
        user_id=owner_id, offset=page * PAGE_SIZE, limit=PAGE_SIZE
    )
    extra = await service.history(
        user_id=owner_id,
        offset=(page + 1) * PAGE_SIZE,
        limit=1,
    )
    if not calculations:
        text = "Расчётов пока нет."
        keyboard = None
    else:
        lines = ["История расчётов:"]
        buttons = []
        for calculation in calculations:
            lines.append(
                f"• №{calculation['id']} · {_moscow_time(calculation['created_at'])} · "
                f"{money_text(Decimal(calculation['total_cost']))} ₽"
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
        if page > 0:
            nav.append(
                InlineKeyboardButton(text="‹ Назад", callback_data=f"history:page:{page-1}")
            )
        if extra:
            nav.append(
                InlineKeyboardButton(text="Далее ›", callback_data=f"history:page:{page+1}")
            )
        if nav:
            buttons.append(nav)
        buttons.append(
            [InlineKeyboardButton(text="🏠 Главное меню", callback_data="home")]
        )
        keyboard = InlineKeyboardMarkup(inline_keyboard=buttons)
        text = "\n".join(lines)

    if isinstance(target, CallbackQuery):
        if target.message:
            await target.message.answer(text, reply_markup=keyboard)
        await safe_answer(target)
    else:
        await target.answer(text, reply_markup=keyboard)


@router.message(F.text == BTN_HISTORY)
async def my_history(message: Message, state: FSMContext, service: SalonService) -> None:
    await state.clear()
    await _send_history(
        message,
        service,
        owner_id=message.from_user.id if message.from_user else -1,
        page=0,
    )


@router.callback_query(F.data.startswith("history:page:"))
async def history_page(
    callback: CallbackQuery, service: SalonService, settings: Settings
) -> None:
    try:
        page = max(0, int(callback.data.rsplit(":", 1)[1]))
    except (ValueError, AttributeError):
        await safe_answer(callback, "Страница истории недоступна.")
        return
    await _send_history(
        callback,
        service,
        owner_id=callback.from_user.id,
        page=page,
    )


@router.callback_query(F.data.startswith("history:detail:"))
async def history_detail(
    callback: CallbackQuery,
    service: SalonService,
    settings: Settings,
) -> None:
    try:
        calculation_id = int(callback.data.rsplit(":", 1)[1])
    except (ValueError, AttributeError):
        await safe_answer(callback, "Расчёт не найден.")
        return
    calculation = await service.get_calculation(
        calculation_id,
        owner_id=None if is_admin(settings, callback.from_user.id) else callback.from_user.id,
    )
    if calculation is None:
        await safe_answer(callback, "Расчёт не найден.")
        return
    lines = [
        f"Расчёт №{calculation['id']} · {_moscow_time(calculation['created_at'])}",
    ]
    for item in calculation["items"]:
        lines.append(
            f"• {item['brand_snapshot']} {item['product_name_snapshot']} — "
            f"{quantity_text(Decimal(item['quantity']))} {item['unit']} — "
            f"{money_text(Decimal(item['line_cost']))} ₽"
        )
    lines.append(f"\nИтого: {money_text(Decimal(calculation['total_cost']))} ₽")
    await callback.message.answer("\n".join(lines))
    await safe_answer(callback)


@router.message(F.text == BTN_SUPPLIES)
async def master_supplies(
    message: Message, state: FSMContext, service: SalonService
) -> None:
    await state.clear()
    categories = await service.categories(master_only=True)
    if not categories:
        await message.answer("Материалы пока не добавлены.")
        return
    lines = ["Материалы для расчёта:"]
    for category in categories:
        lines.append(f"\n{category['name']}")
        brands = await service.brands(category["id"], master_only=True)
        for brand in brands:
            lines.append(f"  {brand['name']}")
            products = await service.products(
                category["id"], brand["id"], master_only=True, limit=250
            )
            lines.extend(f"    · {product['name']}" for product in products)
    lines.append("\nСкладские остатки и закупочные цены видны только администратору.")
    await message.answer("\n".join(lines))
