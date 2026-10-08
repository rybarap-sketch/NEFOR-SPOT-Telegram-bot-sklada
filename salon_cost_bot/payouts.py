from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, ROUND_HALF_UP

# Current NEFOR SPOT split. Change only after owner approval.
MASTER_PERCENT = Decimal("55")
SALON_PERCENT = Decimal("45")
KOPECK = Decimal("0.01")


def rubles(value: Decimal) -> Decimal:
    return value.quantize(KOPECK, rounding=ROUND_HALF_UP)


@dataclass(frozen=True)
class Payout:
    service_price: Decimal
    materials_cost: Decimal
    distributable: Decimal
    master_percent: Decimal
    salon_percent: Decimal
    master_share: Decimal
    salon_share: Decimal
    payable_to_salon: Decimal


def calculate_payout(service_price: Decimal, materials_cost: Decimal) -> Payout:
    """Distribute net revenue and account for materials if master collected payment.

    All parts reconcile to service_price to the kopeck. No balance is changed.
    """
    if not service_price.is_finite() or not materials_cost.is_finite():
        raise ValueError("Введите конечную стоимость услуги и расходников.")
    if service_price <= 0:
        raise ValueError("Стоимость окрашивания должна быть больше нуля.")
    if materials_cost < 0:
        raise ValueError("Стоимость расходников не может быть отрицательной.")
    price = rubles(service_price)
    supplies = rubles(materials_cost)
    if price < supplies:
        raise ValueError(
            f"Цена окрашивания ({price} ₽) меньше стоимости расходников "
            f"({supplies} ₽). Проверьте сумму и укажите правильную цену."
        )
    if MASTER_PERCENT + SALON_PERCENT != Decimal("100"):
        raise RuntimeError("Сумма процентов мастера и салона должна быть 100%.")
    distributable = price - supplies
    master = rubles(distributable * MASTER_PERCENT / Decimal("100"))
    salon = distributable - master  # prevents a one-kopeck rounding discrepancy
    return Payout(
        service_price=price,
        materials_cost=supplies,
        distributable=distributable,
        master_percent=MASTER_PERCENT,
        salon_percent=SALON_PERCENT,
        master_share=master,
        salon_share=salon,
        payable_to_salon=salon + supplies,
    )
