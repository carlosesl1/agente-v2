from __future__ import annotations

import pytest

from v2_contracts.localization import (
    CustomerLanguage,
    customer_language_from_phone,
)


BR_PHONE = "+" + "55" + "75" + "9" * 9
US_PHONE = "+" + "1" + "202" + "555" + "0123"
UK_PHONE = "+" + "44" + "7700" + "900123"
ES_PHONE = "+" + "34" + "612" + "345678"


@pytest.mark.parametrize(
    ("phone", "expected"),
    (
        (BR_PHONE, CustomerLanguage.PT_BR),
        (US_PHONE, CustomerLanguage.EN),
        (UK_PHONE, CustomerLanguage.EN),
        (ES_PHONE, CustomerLanguage.EN),
    ),
)
def test_customer_language_comes_only_from_canonical_phone(
    phone: str,
    expected: CustomerLanguage,
) -> None:
    assert customer_language_from_phone(phone) is expected


@pytest.mark.parametrize(
    "phone",
    (
        "55" + "75" + "9" * 9,
        "",
        "+0" + "55" + "75" + "9" * 9,
        "+55 " + "75" + "9" * 9,
        "+55-" + "75" + "9" * 9,
        None,
    ),
)
def test_customer_language_rejects_non_e164(phone: object) -> None:
    with pytest.raises(ValueError, match="E.164"):
        customer_language_from_phone(phone)  # type: ignore[arg-type]
