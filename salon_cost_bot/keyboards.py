from __future__ import annotations

from collections.abc import Sequence

from aiogram.types import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    KeyboardButton,
    ReplyKeyboardMarkup,
)


BTN_NEW = "🧮 Новый расчёт"
BTN_SUPPLIES = "📦 Расходники"
BTN_COST_INFO = "ℹ️ Расчёт стоимости"
BTN_HISTORY = "📊 История"
BTN_SETTINGS = "⚙️ Настройки"
BTN_ADMIN = "🛠 Админ-панель"

MASTER_MENU = ReplyKeyboardMarkup(
    keyboard=[
        [KeyboardButton(text=BTN_NEW), KeyboardButton(text=BTN_SUPPLIES)],
        [KeyboardButton(text=BTN_COST_INFO), KeyboardButton(text=BTN_HISTORY)],
        [KeyboardButton(text=BTN_SETTINGS)],
    ],
    resize_keyboard=True,
    input_field_placeholder="Выберите действие",
)

ADMIN_MENU = ReplyKeyboardMarkup(
    keyboard=[
        [KeyboardButton(text=BTN_NEW), KeyboardButton(text=BTN_SUPPLIES)],
        [KeyboardButton(text=BTN_COST_INFO), KeyboardButton(text=BTN_HISTORY)],
        [KeyboardButton(text=BTN_SETTINGS), KeyboardButton(text=BTN_ADMIN)],
    ],
    resize_keyboard=True,
    input_field_placeholder="Выберите действие",
)

ADMIN_PANEL = InlineKeyboardMarkup(
    inline_keyboard=[
        [
            InlineKeyboardButton(text="📦 Склад", callback_data="adm:stock"),
            InlineKeyboardButton(text="💰 Правила цен", callback_data="adm:prices"),
        ],
        [
            InlineKeyboardButton(
                text="📝 Материалы", callback_data="adm:stock:all:0"
            ),
            InlineKeyboardButton(text="📊 История расчётов", callback_data="adm:history"),
        ],
        [
            InlineKeyboardButton(text="📜 Движения склада", callback_data="adm:movements:0"),
            InlineKeyboardButton(text="🧾 Инвентаризация", callback_data="adm:count"),
        ],
        [
            InlineKeyboardButton(text="💾 Резервная копия / экспорт", callback_data="adm:exports"),
            InlineKeyboardButton(text="⚙️ Настройки", callback_data="adm:settings"),
        ],
        [InlineKeyboardButton(text="⬅️ Главное меню", callback_data="adm:home")],
    ]
)


def rows_keyboard(
    items: Sequence[tuple[str, str]],
    *,
    back: str | None = None,
    columns: int = 2,
) -> InlineKeyboardMarkup:
    buttons = [
        InlineKeyboardButton(text=label[:60], callback_data=data)
        for label, data in items
    ]
    rows = [buttons[index : index + columns] for index in range(0, len(buttons), columns)]
    if back:
        rows.append([InlineKeyboardButton(text="⬅️ Назад", callback_data=back)])
    rows.append([InlineKeyboardButton(text="🏠 Главное меню", callback_data="home")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def page_keyboard(
    items: Sequence[tuple[str, str]],
    *,
    page: int,
    more: bool,
    back: str,
    columns: int = 1,
) -> InlineKeyboardMarkup:
    buttons = [
        InlineKeyboardButton(text=label[:60], callback_data=data)
        for label, data in items
    ]
    rows = [buttons[index : index + columns] for index in range(0, len(buttons), columns)]
    navigation = []
    if page > 0:
        navigation.append(
            InlineKeyboardButton(text="‹ Назад", callback_data=f"page:{page - 1}:{back}")
        )
    if more:
        navigation.append(
            InlineKeyboardButton(text="Далее ›", callback_data=f"page:{page + 1}:{back}")
        )
    if navigation:
        rows.append(navigation)
    rows.append([InlineKeyboardButton(text="⬅️ Назад", callback_data=back)])
    rows.append([InlineKeyboardButton(text="🏠 Главное меню", callback_data="home")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def calc_action_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="➕ Добавить ещё", callback_data="calc:add"),
                InlineKeyboardButton(text="✅ Завершить расчёт", callback_data="calc:finish"),
            ],
            [InlineKeyboardButton(text="✖️ Отменить", callback_data="calc:cancel")],
        ]
    )


def acid_action_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="✅ Добавить смывку", callback_data="acid:add")],
            [InlineKeyboardButton(text="✖️ Отменить", callback_data="calc:cancel")],
        ]
    )


def session_item_keyboard(session_id: int, product_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="✅ Без изменений",
                    callback_data=f"session:same:{session_id}:{product_id}",
                ),
                InlineKeyboardButton(
                    text="⏭ Пропустить",
                    callback_data=f"session:skip:{session_id}:{product_id}",
                ),
            ],
            [
                InlineKeyboardButton(
                    text="⏹ Завершить инвентаризацию",
                    callback_data=f"session:finish:{session_id}",
                )
            ],
        ]
    )
