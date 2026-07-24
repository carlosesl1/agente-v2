from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from v2_host.settings import V2Settings


ACK = "ENABLE_V2_REAL_EFFECTS_FOR_CONTROLLED_TEST"


def _base(tmp_path: Path) -> dict[str, str]:
    return {
        "V2_MANYCHAT_WEBHOOK_SECRET": "secret",
        "V2_SQLITE_PATH": str(tmp_path / "v2.sqlite3"),
        "V2_RUNTIME_MODE": "controlled_write",
        "V2_CLOUDBEDS_API_KEY": "cb-read",
        "V2_CLOUDBEDS_PROPERTY_ID": "property",
        "V2_BOKUN_ACCESS_KEY": "bk-read",
        "V2_BOKUN_SECRET_KEY": "bk-secret",
        "V2_BOKUN_PRODUCT_MAP_JSON": '{"product:buracao":"123"}',
        "V2_READ_PROBE_CHECK_IN": "2099-01-01",
        "V2_READ_PROBE_CHECK_OUT": "2099-01-02",
        "V2_READ_PROBE_ACTIVITY_DATE": "2099-01-01",
        "V2_READ_PROBE_PRODUCT_ID": "product:buracao",
        "V2_MANYCHAT_API_KEY": "manychat",
        "V2_HERMES_COMMAND_JSON": '["python","child.py"]',
        "V2_HERMES_SYSTEM_PROMPT": "prompt",
        "V2_HERMES_TRANSCRIPT_KEY_HEX": "11" * 32,
        "V2_KNOWLEDGE_BASE_PATH": str(tmp_path / "kb.sqlite3"),
        "V2_PUBLIC_AUTHORITY_MANIFEST_PATH": str(tmp_path / "authority.json"),
        "V2_PUBLIC_AUTHORITY_HMAC_KEY_HEX": "22" * 32,
        "V2_ALLOWED_SUBSCRIBER_IDS": "1873018537",
        "V2_HERMES_MODEL": "openai-codex/gpt-5.6-luna",
        "V2_CANDIDATE_GIT_SHA": "a" * 40,
        "V2_CANDIDATE_IMAGE_DIGEST": "sha256:" + "b" * 64,
        "V2_GLOBAL_KILL_SWITCH": "false",
        "V2_WRITE_WINDOW_END": (
            datetime.now(timezone.utc) + timedelta(minutes=30)
        ).isoformat(),
        "V2_REAL_EFFECTS_ACK": ACK,
        "V2_STRIPE_ENVIRONMENT": "test",
    }


def test_each_real_effect_gate_is_independent(tmp_path: Path) -> None:
    names = (
        "V2_ENABLE_CLOUDBEDS_WRITES",
        "V2_ENABLE_BOKUN_WRITES",
        "V2_ENABLE_STRIPE_LINKS",
        "V2_ENABLE_MANYCHAT_DELIVERY",
        "V2_ENABLE_MANYCHAT_HANDOFF",
    )
    for selected in names:
        env = _base(tmp_path)
        for name in names:
            env[name] = "true" if name == selected else "false"
        if selected == "V2_ENABLE_STRIPE_LINKS":
            env["V2_STRIPE_SECRET_KEY"] = "sk_test_scoped"
        settings = V2Settings.from_env(env)
        assert sum(settings.real_effect_gates.values()) == 1
        assert settings.real_effect_gates[
            {
                "V2_ENABLE_CLOUDBEDS_WRITES": "cloudbeds_writes",
                "V2_ENABLE_BOKUN_WRITES": "bokun_writes",
                "V2_ENABLE_STRIPE_LINKS": "stripe_links",
                "V2_ENABLE_MANYCHAT_DELIVERY": "manychat_delivery",
                "V2_ENABLE_MANYCHAT_HANDOFF": "manychat_handoff",
            }[selected]
        ] is True


def test_closed_gates_do_not_require_an_open_window(tmp_path: Path) -> None:
    env = _base(tmp_path)
    env.pop("V2_WRITE_WINDOW_END")
    env["V2_GLOBAL_KILL_SWITCH"] = "true"
    env.pop("V2_REAL_EFFECTS_ACK")

    settings = V2Settings.from_env(env)

    assert settings.all_real_effect_gates_closed
    assert not settings.write_window_is_open(datetime.now(timezone.utc))


def test_handoff_and_delivery_do_not_collapse_into_one_gate(tmp_path: Path) -> None:
    env = _base(tmp_path)
    env["V2_ENABLE_MANYCHAT_HANDOFF"] = "true"
    env["V2_ENABLE_MANYCHAT_DELIVERY"] = "false"
    settings = V2Settings.from_env(env)

    assert settings.manychat_handoff_enabled is True
    assert settings.manychat_delivery_enabled is False


def test_duplicate_subscriber_entries_are_rejected_not_silently_deduped(tmp_path: Path) -> None:
    env = _base(tmp_path)
    env["V2_ALLOWED_SUBSCRIBER_IDS"] = "1873018537,1873018537"

    with pytest.raises(ValueError, match="exactly one subscriber"):
        V2Settings.from_env(env)
