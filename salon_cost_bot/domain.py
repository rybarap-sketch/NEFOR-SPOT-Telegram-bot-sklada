from __future__ import annotations

from decimal import Decimal, InvalidOperation, ROUND_HALF_UP


ZERO = Decimal("0")
CENT = Decimal("0.01")
MAX_QUANTITY = Decimal("999999")


def parse_decimal(value: str, *, max_value: Decimal = MAX_QUANTITY) -> Decimal:
    """Parse user-entered decimal values without passing through float."""
    normalized = value.strip().replace(" ", "").replace(",", ".")
    if not normalized:
        raise ValueError("Введите число.")
    try:
        result = Decimal(normalized)
    except (InvalidOperation, ValueError) as error:
        raise ValueError("Не получилось распознать число. Пример: 12 или 12,5.") from error
    if not result.is_finite():
        raise ValueError("Введите конечное число.")
    if result < ZERO:
        raise ValueError("Отрицательное количество вводить нельзя.")
    if result > max_value:
        raise ValueError(f"Значение слишком большое. Максимум: {max_value}.")
    return result


def money(value: Decimal) -> Decimal:
    return value.quantize(CENT, rounding=ROUND_HALF_UP)


def decimal_text(value: Decimal) -> str:
    normalized = value.normalize()
    return "0" if normalized == ZERO else format(normalized, "f")


def money_text(value: Decimal) -> str:
    return f"{money(value):,.2f}".replace(",", " ").replace(".", ",")


def quantity_text(value: Decimal) -> str:
    return decimal_text(value).replace(".", ",")
