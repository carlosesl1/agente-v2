"""Activation, truthful prompt policy and live factory wiring contracts."""

from dataclasses import replace
from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path

import pytest

from tests.test_v2_settings import _controlled_env
from v2_host.settings import V2ProcessRole, V2Settings


def test_forms_need_explicit_utc_activation_cutoff(tmp_path):
    env = _controlled_env(tmp_path)
    assert getattr(V2Settings.from_env(env), "booking_forms_from", "missing") is None
    env["V2_BOOKING_FORMS_FROM"] = "2026-09-29T12:00:00Z"
    cfg = V2Settings.from_env(env)
    assert cfg.booking_forms_from == datetime(2026, 9, 29, 12, tzinfo=UTC)
    with pytest.raises(ValueError, match="UTC"):
        replace(
            cfg,
            booking_forms_from=datetime(2026, 9, 29, 12, tzinfo=UTC).replace(
                tzinfo=None
            ),
        )
    with pytest.raises(ValueError, match="UTC"):
        replace(
            cfg,
            booking_forms_from=datetime(
                2026, 9, 29, 12, tzinfo=timezone(timedelta(hours=-3))
            ),
        )


@pytest.mark.parametrize(
    "value", ["yesterday", "2026-09-29T12:00:00", "2026-09-29T12:00:00-03:00"]
)
def test_invalid_form_cutoff_is_not_silently_ignored(tmp_path, value):
    env = {**_controlled_env(tmp_path), "V2_BOOKING_FORMS_FROM": value}
    with pytest.raises(ValueError):
        V2Settings.from_env(env)


def test_form_activation_is_worker_only(tmp_path):
    env = {**_controlled_env(tmp_path), "V2_BOOKING_FORMS_FROM": "2026-09-29T12:00:00Z"}
    assert (
        getattr(
            V2Settings.from_env(env, process_role=V2ProcessRole.API),
            "booking_forms_from",
            "missing",
        )
        is None
    )


def test_automatic_completion_preserves_authenticated_locale():
    prompt = (
        Path(__file__).resolve().parents[1] / "config/v2_terra_system_prompt.txt"
    ).read_text()
    section = prompt.split("`trigger=operation_result`", 1)[1].split("\n\n", 1)[0]
    assert "idioma confirmado em locale" in section
    assert "não escolha o idioma" in section


def test_primary_maya_prompt_explains_forms_without_claiming_submission():
    prompt = (
        Path(__file__).resolve().parents[1] / "config/v2_terra_system_prompt.txt"
    ).read_text()
    assert "FORMULÁRIO DE RESPONSABILIDADE" in prompt
    section = prompt.split("FORMULÁRIO DE RESPONSABILIDADE", 1)[1].split("\n\n", 1)[0]
    for concept in (
        "após",
        "pagamento",
        "botão",
        "um único",
        "hospedagem",
        "preenchido",
        "reply_chunks",
    ):
        assert concept in section
