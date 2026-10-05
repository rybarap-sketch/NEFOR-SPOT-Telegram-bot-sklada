from __future__ import annotations

import logging

from aiogram import F, Router
from aiogram.filters import Command, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from salon_cost_bot.config import Settings
from salon_cost_bot.keyboards import (
    ADMIN_MENU,
    ADMIN_PANEL,
    BTN_ADMIN,
    BTN_COST_INFO,
    BTN_SETTINGS,
    MASTER_MENU,
)
from salon_cost_bot.services import SalonService

logger = logging.getLogger(__name__)
router = Router(name="common")


def is_admin(settings: Settings, user_id: int | None) -> bool:
    return user_id == settings.admin_user_id


def home_keyboard(settings: Settings, user_id: int | None):
    return ADMIN_MENU if is_admin(settings, user_id) else MASTER_MENU


async def start(message: Message, state: FSMContext, settings: Settings) -> None:
    await state.clear()
    admin = is_admin(settings, message.from_user.id if message.from_user else None)
    text = (
        "Здравствуйте! Это NEFOR SPOT — внутренний калькулятор расходников и учёт процедур."
        if not admin
        else "Здравствуйте! Вы вошли как администратор NEFOR SPOT. Доступны калькулятор и управление складом."
    )
    await message.answer(text, reply_markup=home_keyboard(settings, message.from_user.id))


async def cancel(message: Message, state: FSMContext, settings: Settings) -> None:
    await state.clear()
    await message.answer(
        "Действие отменено. Данные не изменены.",
        reply_markup=home_keyboard(settings, message.from_user.id if message.from_user else None),
    )


async def go_home(callback: CallbackQuery, state: FSMContext, settings: Settings) -> None:
    await state.clear()
    if callback.message:
        await callback.message.answer(
            "Главное меню",
            reply_markup=home_keyboard(
                settings, callback.from_user.id if callback.from_user else None
            ),
        )
    await safe_answer(callback)


async def safe_answer(callback: CallbackQuery, text: str | None = None) -> None:
    try:
        await callback.answer(text)
    except Exception as error:
        # Old callback queries cannot be acknowledged; this does not affect
        # the business operation or the message already sent.
        logger.debug("Callback acknowledgement failed: %s", type(error).__name__)


async def show_settings(message: Message, state: FSMContext) -> None:
    await state.clear()
    await message.answer(
        "Настройки пользователя\n"
        "Все суммы отображаются в рублях, дата и время — по Москве.\n"
        "Незавершённый расчёт можно отменить командой /cancel."
    )


async def show_cost_info(message: Message, state: FSMContext) -> None:
    await state.clear()
    await message.answer(
        "Стоимость процедуры складывается из выбранных материалов и их количества. "
        "В расчётах используются действующие расчётные ставки. Закупочные цены и складские "
        "остатки доступны только администратору."
    )


async def show_admin_panel(
    message: Message, settings: Settings, state: FSMContext
) -> None:
    if not message.from_user or not is_admin(settings, message.from_user.id):
        await message.answer("У вас нет доступа к административной панели.")
        return
    await state.clear()
    await message.answer("Администрирование склада NEFOR SPOT", reply_markup=ADMIN_PANEL)


async def show_admin_panel_callback(
    callback: CallbackQuery, settings: Settings, state: FSMContext
) -> None:
    if not is_admin(settings, callback.from_user.id):
        await safe_answer(callback, "Нет доступа.")
        return
    await state.clear()
    if callback.message:
        await callback.message.answer(
            "Администрирование склада NEFOR SPOT", reply_markup=ADMIN_PANEL
        )
    await safe_answer(callback)


def setup_common_handlers(
    *,
    settings: Settings,
    service: SalonService,
) -> Router:
    # `service` is passed to keep router setup explicit and permit future
    # common status commands without module globals.
    del service

    router.message.register(start, CommandStart(), flags={"settings": settings})
    router.message.register(cancel, Command("cancel"), flags={"settings": settings})
    router.message.register(show_settings, F.text == BTN_SETTINGS)
    router.message.register(show_cost_info, F.text == BTN_COST_INFO)
    router.message.register(
        show_admin_panel, F.text == BTN_ADMIN, flags={"settings": settings}
    )
    router.callback_query.register(go_home, F.data == "home", flags={"settings": settings})
    router.callback_query.register(
        show_admin_panel_callback,
        F.data == "adm:home",
        flags={"settings": settings},
    )
    return router
