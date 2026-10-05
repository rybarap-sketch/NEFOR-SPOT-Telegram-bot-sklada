from __future__ import annotations

from decimal import Decimal


CATEGORIES: tuple[tuple[str, str, bool, int], ...] = (
    ("paint", "Краска", True, 10),
    ("corrector", "Корректор", True, 20),
    ("powder", "Осветление", True, 30),
    ("pigment", "Прямой пигмент", True, 40),
    ("acid_phase1", "Кислотная смывка · фаза 1", False, 50),
    ("acid_phase2", "Кислотная смывка · фаза 2", False, 60),
    ("oxide", "Оксид", False, 70),
    ("shampoo", "Шампунь", False, 80),
    ("conditioner", "Кондиционер", False, 90),
    ("mask", "Маска", False, 100),
    ("deep_clean_shampoo", "Глубоко очищающий шампунь", False, 110),
    ("stabilizer", "Стабилизатор", False, 120),
    ("small_consumable", "Мелкие расходники", False, 130),
    ("other", "Прочее", False, 140),
)


def _product(
    category: str,
    brand: str,
    name: str,
    rate: str | None = None,
    purchase: str | None = None,
    stock: str = "0",
    *,
    unit: str = "g",
    visible: bool | None = None,
    needs_inventory: bool = False,
) -> dict[str, object]:
    category_visibility = category in {
        "paint",
        "corrector",
        "powder",
        "pigment",
        "acid_phase1",
        "acid_phase2",
    }
    return {
        "sku": f"{category}:{brand.casefold().replace(' ', '-')}:"
        f"{name.casefold().replace(' ', '-').replace('/', '-')}",
        "category": category,
        "brand": brand,
        "name": name,
        "rate": rate,
        "purchase": purchase,
        "stock": stock,
        "unit": unit,
        "visible": category_visibility if visible is None else visible,
        "needs_inventory": needs_inventory,
    }


PRODUCTS: tuple[dict[str, object], ...] = (
    *(
        _product("paint", "Insight", name, "11.01", stock=stock)
        for name, stock in (
            ("8.2", "86"), ("8.1", "180"), ("8.0", "135"),
            ("7.43", "37"), ("7.0", "30"), ("11.10", "300"),
            ("10.22", "70"), ("9.1", "35"), ("9.0", "23"),
            ("11.21", "54"), ("11.11", "178"), ("7.34", "38"),
            ("1.0", "0"),
        )
    ),
    *(
        _product(
            "pigment",
            "Bad Girl",
            name,
            "6.51",
            "3.32",
            stock=stock,
            needs_inventory=stock.startswith("-"),
        )
        for name, stock in (
            ("Absinthe (Зелёный)", "298"),
            ("Queen of Hearts (Красный)", "55"),
            ("Blue Devil (Синий)", "237"),
            ("Star in Shock (Ярко-розовый / Маджента)", "106"),
            ("Sugar Baby (Пастельно-розовый)", "39"),
            ("Sea Fairy (Бирюзовый / Голубой)", "25"),
            ("Purple Storm (Фиолетовый)", "-50"),
            ("Neon Shock (Оранжево-розовый / неон)", "8"),
            ("Wild Wild Rose (Розовый)", "-185"),
            ("Ice Dragon (Тёмно-синий / Индиго)", "16"),
            ("Fairy Queen (Лавандовый)", "97"),
            ("Electric Vibe (Неоновый жёлтый)", "135"),
        )
    ),
    *(
        _product("paint", "ESTEL", name, "6.20", "2.65", stock=stock)
        for name, stock in (
            ("Princess Essex 3/0", "50"),
            ("Princess Essex 10/0", "34"),
            ("Princess Essex 10/36", "54"),
            ("Princess Essex 8/0", "30"),
            ("Princess Essex 7/7", "60"),
            ("Princess Essex 9/65", "20"),
            ("7/4", "40"),
        )
    ),
    *(
        _product("corrector", "ESTEL", name, "1.80", "1.80")
        for name in ("0/11", "0/66", "0/33")
    ),
    _product("paint", "KAPOUS", "Красный", "8.90", stock="250"),
    _product("paint", "KAPOUS", "10/31", "8.90", stock="75"),
    *(
        _product("paint", "EPICA", name, "8.90", "5")
        for name in (
            "10.18", "9.81", "9.18", "9.21", "9.32",
            "10.0", "8.0", "7.4", "7.0", "6.0",
        )
    ),
    _product("corrector", "EPICA", "Anti-orange", "5.00", "5"),
    _product("powder", "Secta", "Порошок", "7.63", "3", stock="431"),
    _product(
        "acid_phase1", "Secta", "Фаза 1", "5.60", "2", stock="1340"
    ),
    _product(
        "acid_phase2", "Secta", "Фаза 2", "5.60", "2", stock="1340"
    ),
    # Складские позиции, скрытые от мастеров в обычном выборе.
    _product("shampoo", "Anti-Frizz", "Shampoo", purchase="9"),
    _product("conditioner", "Anti-Frizz", "Conditioner", purchase="6"),
    _product("mask", "Blonder", "Mask", purchase="5.06"),
    _product("deep_clean_shampoo", "Post Chemistry", "SHGO", purchase="7.85"),
    *(
        _product("oxide", "InColor", f"Activator {strength}", purchase="1.3" if strength == "3%" else None)
        for strength in ("1.8%", "3%", "6%", "12%")
    ),
    _product("oxide", "ESTEL", "Activator 1.5%", "1", stock="726"),
    _product("oxide", "ESTEL", "Activator 3%", "1", stock="530"),
    _product("shampoo", "EPICA", "Shampoo for curly hair", purchase="0.8", stock="10000"),
    _product("conditioner", "EPICA", "Conditioner for curly hair", purchase="0.9", stock="4000"),
    _product("conditioner", "EPICA", "Conditioner for colored hair", purchase="0.9", stock="1000"),
    *(
        _product("oxide", "EPICA", f"Oxide {strength}", purchase="0.6", stock=stock)
        for strength, stock in (
            ("1.5%", "2420"), ("3%", "2698"), ("6%", "2845"),
            ("9%", "850"), ("12%", "2757"),
        )
    ),
    _product("mask", "EPICA", "Moisturizing mask", purchase="0.9", stock="2000"),
    _product("mask", "EPICA", "Damaged hair mask", purchase="0.9", stock="1000"),
    _product("mask", "EPICA", "Colored hair mask", purchase="0.9", stock="1000"),
    _product("stabilizer", "Secta", "Remover stabilizer", purchase="3.5"),
    _product("small_consumable", "Salon", "Small consumables", unit="pcs"),
    _product("small_consumable", "Salon", "Towel", "30", "30", unit="pcs"),
    _product("small_consumable", "Salon", "Neck collar", "2", "2", unit="pcs"),
)


BRAND_RATES: tuple[tuple[str, str, str], ...] = (
    ("paint", "ESTEL", "6.20"),
    ("paint", "Insight", "11.01"),
    ("paint", "KAPOUS", "8.90"),
    ("paint", "EPICA", "8.90"),
    ("corrector", "ESTEL", "1.80"),
    ("corrector", "EPICA", "5.00"),
    ("powder", "Secta", "7.63"),
    ("pigment", "Bad Girl", "6.51"),
    ("acid_phase1", "Secta", "5.60"),
    ("acid_phase2", "Secta", "5.60"),
)


def as_decimal(value: object | None) -> Decimal | None:
    if value is None:
        return None
    return Decimal(str(value))
