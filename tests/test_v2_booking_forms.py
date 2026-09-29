"""User-supplied form catalog: exact identity routing, never title inference."""

import importlib
import json
from pathlib import Path

import pytest

from v2_contracts.localization import CustomerLanguage

ROOT = Path(__file__).resolve().parents[1]
EXPECTED = {
    ("pati", "pt-BR"): "https://forms.gle/bXEoQUBcwzBaMQ8t7",
    ("pati", "en"): "https://forms.gle/SsGvxtLQ6pjyV6qi8",
    ("day_excursions", "pt-BR"): "https://forms.gle/pTD7jYosU6EzuyrAA",
    ("day_excursions", "en"): "https://forms.gle/N8Lqnhg8LxT5ABr76",
    ("mixila_fumaca_below", "pt-BR"): "https://forms.gle/jgUBDfSrqDQiizhR7",
    ("mixila_fumaca_below", "en"): "https://forms.gle/AYgrYEaND8328BwN6",
}


def catalog():
    assert importlib.util.find_spec("v2_contracts.booking_forms") is not None, (
        "booking form catalog is missing"
    )
    return importlib.import_module(
        "v2_contracts.booking_forms"
    ).BookingFormCatalog.load()


@pytest.mark.parametrize(
    "product,category",
    [
        ("product:pati-3d", "pati"),
        ("product:buracao", "day_excursions"),
        ("product:mixila-1d", "mixila_fumaca_below"),
        ("product:mixila-2d", "mixila_fumaca_below"),
        ("product:fumaca-por-baixo-3d", "mixila_fumaca_below"),
    ],
)
@pytest.mark.parametrize("language", list(CustomerLanguage))
def test_exact_operator_urls_and_manychat_routes(product, category, language):
    route = catalog().route(product, language)
    assert route.url == EXPECTED[category, language.value]
    assert route.link_field_id == 14426643
    assert (
        route.flow_ns
        == {
            "pt-BR": "content20260327015856_644212",
            "en": "content20260327021027_819398",
        }[language.value]
    )


def test_all_current_canonical_products_are_explicitly_mapped():
    products = json.loads((ROOT / "config/v2_bokun_product_map.json").read_text())
    assert set(catalog().products) == set(products)
    for product in products:
        assert catalog().product_for_lookup(f"lookup:{product}:abc123") == product
    assert catalog().product_for_lookup("lookup:unknown:pati:abc123") is None
    assert catalog().route("product:unknown", CustomerLanguage.PT_BR) is None
