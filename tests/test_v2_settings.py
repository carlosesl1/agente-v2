from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from v2_host.settings import RuntimeMode, StripeEnvironment, V2Settings


CANDIDATE_SHA = "a" * 40
CANDIDATE_DIGEST = "sha256:" + "b" * 64
TRANSCRIPT_KEY = "11" * 32
AUTHORITY_KEY = "22" * 32


def _controlled_env(tmp_path: Path) -> dict[str, str]:
    return {
        "V2_MANYCHAT_WEBHOOK_SECRET": "webhook-secret",
        "V2_SQLITE_PATH": str(tmp_path / "inbox.sqlite3"),
        "V2_RUNTIME_MODE": "controlled_write",
        "V2_CLOUDBEDS_API_KEY": "cloudbeds-read-key",
        "V2_CLOUDBEDS_PROPERTY_ID": "property-1",
        "V2_BOKUN_ACCESS_KEY": "bokun-read-key",
        "V2_BOKUN_SECRET_KEY": "bokun-read-secret",
        "V2_BOKUN_PRODUCT_MAP_JSON": '{"product:buracao":"12345"}',
        "V2_READ_PROBE_CHECK_IN": "2099-08-01",
        "V2_READ_PROBE_CHECK_OUT": "2099-08-02",
        "V2_READ_PROBE_ACTIVITY_DATE": "2099-08-01",
        "V2_READ_PROBE_PRODUCT_ID": "product:buracao",
        "V2_MANYCHAT_API_KEY": "manychat-key",
        "V2_HERMES_COMMAND_JSON": '["python","hermes_child.py"]',
        "V2_HERMES_SYSTEM_PROMPT": "closed prompt",
        "V2_HERMES_TRANSCRIPT_KEY_HEX": TRANSCRIPT_KEY,
        "V2_KNOWLEDGE_BASE_PATH": str(tmp_path / "knowledge.sqlite3"),
        "V2_PUBLIC_AUTHORITY_MANIFEST_PATH": str(tmp_path / "authority.json"),
        "V2_PUBLIC_AUTHORITY_HMAC_KEY_HEX": AUTHORITY_KEY,
        "V2_ALLOWED_SUBSCRIBER_IDS": "1873018537",
        "V2_HERMES_MODEL": "openai-codex/gpt-5.6-luna",
        "V2_CANDIDATE_GIT_SHA": CANDIDATE_SHA,
        "V2_CANDIDATE_IMAGE_DIGEST": CANDIDATE_DIGEST,
        "V2_GLOBAL_KILL_SWITCH": "true",
        "V2_STRIPE_ENVIRONMENT": "test",
    }


def _future_window(*, minutes: int = 30) -> str:
    return (datetime.now(timezone.utc) + timedelta(minutes=minutes)).isoformat()


def test_idle_controlled_canary_loads_with_all_effects_closed(tmp_path: Path) -> None:
    settings = V2Settings.from_env(_controlled_env(tmp_path))

    assert settings.runtime_mode is RuntimeMode.CONTROLLED_WRITE
    assert settings.allowed_subscriber_ids == ("1873018537",)
    assert settings.hermes_model == "openai-codex/gpt-5.6-luna"
    assert settings.candidate_git_sha == CANDIDATE_SHA
    assert settings.candidate_image_digest == CANDIDATE_DIGEST
    assert settings.global_kill_switch_engaged is True
    assert settings.write_window_end is None
    assert settings.stripe_environment is StripeEnvironment.TEST
    assert settings.all_real_effect_gates_closed is True


def test_controlled_canary_requires_exactly_one_allowed_subscriber(tmp_path: Path) -> None:
    env = _controlled_env(tmp_path)
    env["V2_ALLOWED_SUBSCRIBER_IDS"] = "1873018537,999"

    with pytest.raises(ValueError, match="exactly one subscriber"):
        V2Settings.from_env(env)

    env["V2_ALLOWED_SUBSCRIBER_IDS"] = ""
    with pytest.raises(ValueError, match="exactly one subscriber"):
        V2Settings.from_env(env)


def test_controlled_canary_requires_luna_and_immutable_candidate(tmp_path: Path) -> None:
    env = _controlled_env(tmp_path)
    env["V2_HERMES_MODEL"] = "openai-codex/gpt-5.6-sol"
    with pytest.raises(ValueError, match="gpt-5.6-luna"):
        V2Settings.from_env(env)

    env = _controlled_env(tmp_path)
    env["V2_CANDIDATE_GIT_SHA"] = "not-a-sha"
    with pytest.raises(ValueError, match="candidate git sha"):
        V2Settings.from_env(env)

    env = _controlled_env(tmp_path)
    env["V2_CANDIDATE_IMAGE_DIGEST"] = "latest"
    with pytest.raises(ValueError, match="candidate image digest"):
        V2Settings.from_env(env)


def test_effect_gate_requires_ack_kill_switch_release_and_bounded_window(tmp_path: Path) -> None:
    env = _controlled_env(tmp_path)
    env["V2_ENABLE_CLOUDBEDS_WRITES"] = "true"
    env["V2_REAL_EFFECTS_ACK"] = "ENABLE_V2_REAL_EFFECTS_FOR_CONTROLLED_TEST"
    env["V2_WRITE_WINDOW_END"] = _future_window()

    with pytest.raises(ValueError, match="kill switch"):
        V2Settings.from_env(env)

    env["V2_GLOBAL_KILL_SWITCH"] = "false"
    env["V2_WRITE_WINDOW_END"] = (
        datetime.now(timezone.utc) - timedelta(seconds=1)
    ).isoformat()
    with pytest.raises(ValueError, match="write window"):
        V2Settings.from_env(env)

    env["V2_WRITE_WINDOW_END"] = _future_window(minutes=24 * 60 + 1)
    with pytest.raises(ValueError, match="24 hours"):
        V2Settings.from_env(env)


def test_stripe_gate_accepts_only_test_environment_and_test_key(tmp_path: Path) -> None:
    env = _controlled_env(tmp_path)
    env.update(
        {
            "V2_ENABLE_STRIPE_LINKS": "true",
            "V2_REAL_EFFECTS_ACK": "ENABLE_V2_REAL_EFFECTS_FOR_CONTROLLED_TEST",
            "V2_GLOBAL_KILL_SWITCH": "false",
            "V2_WRITE_WINDOW_END": _future_window(),
            "V2_STRIPE_HOSTEL_ACCOUNT_PROFILE_ID": "stripe-account:hostel:test",
            "V2_STRIPE_AGENCY_ACCOUNT_PROFILE_ID": "stripe-account:agency:test",
            "V2_STRIPE_HOSTEL_SECRET_KEY": "sk_" + "live_forbidden_hostel",
            "V2_STRIPE_AGENCY_SECRET_KEY": "rk_" + "test_scoped_agency",
            "V2_STRIPE_ENVIRONMENT": "test",
        }
    )
    with pytest.raises(ValueError, match="test Stripe keys"):
        V2Settings.from_env(env)

    env["V2_STRIPE_HOSTEL_SECRET_KEY"] = "rk_" + "test_scoped_hostel"
    env["V2_STRIPE_ENVIRONMENT"] = "live"
    with pytest.raises(ValueError, match="test"):
        V2Settings.from_env(env)

    env["V2_STRIPE_ENVIRONMENT"] = "test"
    settings = V2Settings.from_env(env)
    assert settings.stripe_links_enabled is True
    assert settings.stripe_environment is StripeEnvironment.TEST
    assert settings.stripe_account_profiles == {
        "hostel": "stripe-account:hostel:test",
        "agency": "stripe-account:agency:test",
    }
    assert set(settings.stripe_test_secret_keys) == {
        "stripe-account:hostel:test",
        "stripe-account:agency:test",
    }
    assert settings.write_window_is_open(datetime.now(timezone.utc)) is True


def test_manychat_action_configuration_is_closed_and_strict(tmp_path: Path) -> None:
    env = _controlled_env(tmp_path)
    env.update(
        {
            "V2_MANYCHAT_REPLY_FIELD_ID": "101",
            "V2_MANYCHAT_REPLY_FLOW_NS": "content20260724_reply",
            "V2_MANYCHAT_PAYMENT_LINK_FIELD_ID": "102",
            "V2_MANYCHAT_PAYMENT_DESCRIPTION_FIELD_ID": "103",
            "V2_MANYCHAT_PAYMENT_FLOW_NS": "content20260724_payment",
            "V2_MANYCHAT_HANDOFF_TAG_ID": "104",
        }
    )
    settings = V2Settings.from_env(env)
    assert settings.manychat_reply_field_id == 101
    assert settings.manychat_payment_link_field_id == 102
    assert settings.manychat_payment_description_field_id == 103
    assert settings.manychat_handoff_tag_id == 104

    env["V2_MANYCHAT_REPLY_FIELD_ID"] = "not-an-id"
    with pytest.raises(ValueError, match="numeric V2 settings"):
        V2Settings.from_env(env)
