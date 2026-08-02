from __future__ import annotations

from v2_application.private_customer_collection import collect_private_customer_facts


def _facts(result) -> dict[str, object]:
    return {item.name: item.value for item in result.facts}


def test_parent_collector_extracts_and_redacts_natural_portuguese_profile() -> None:
    name = "Pessoa Exemplo Silva"
    email = "pessoa.exemplo@example.invalid"
    country = "Brasil"

    result = collect_private_customer_facts(
        f"Meu nome completo é {name}, meu e-mail é {email} e sou do {country}."
    )

    assert _facts(result) == {
        "full_name": name,
        "email": email,
        "country_code": "BR",
    }
    assert result.invalid_fact_names == ()
    assert result.phone_supplied is False
    for private_value in (name, email, country):
        assert private_value not in result.sanitized_message
        assert private_value not in repr(result)
    assert "[private full_name supplied]" in result.sanitized_message
    assert "[private email supplied]" in result.sanitized_message
    assert "[private country_code supplied]" in result.sanitized_message


def test_parent_collector_accepts_unlabelled_comma_separated_answer() -> None:
    result = collect_private_customer_facts(
        "Synthetic Guest Silva, synthetic.guest@example.invalid, United States"
    )

    assert _facts(result) == {
        "full_name": "Synthetic Guest Silva",
        "email": "synthetic.guest@example.invalid",
        "country_code": "US",
    }
    assert "Synthetic Guest Silva" not in result.sanitized_message
    assert "synthetic.guest@example.invalid" not in result.sanitized_message
    assert "United States" not in result.sanitized_message


def test_parent_collector_redacts_invalid_explicit_values_and_requests_correction() -> None:
    result = collect_private_customer_facts(
        "Nome completo: Mononym; e-mail: not-an-email; país: Narnia"
    )

    assert result.facts == ()
    assert result.invalid_fact_names == ("full_name", "email", "country_code")
    assert "Mononym" not in result.sanitized_message
    assert "not-an-email" not in result.sanitized_message
    assert "Narnia" not in result.sanitized_message


def test_phone_from_transcript_is_redacted_but_never_becomes_a_fact() -> None:
    phone = "+1" + "202" + "555" + "0188"

    result = collect_private_customer_facts(f"Meu telefone é {phone}")

    assert result.facts == ()
    assert result.invalid_fact_names == ()
    assert result.phone_supplied is True
    assert phone not in result.sanitized_message
    assert phone not in repr(result)
    assert "phone supplied but not accepted" in result.sanitized_message
