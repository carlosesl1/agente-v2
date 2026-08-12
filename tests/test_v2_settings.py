from __future__ import annotations

from dataclasses import replace
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
    assert settings.critical_approval_ttl_seconds == 1800


def test_general_availability_requires_empty_allowlist_and_accepts_open_gates(
    tmp_path: Path,
) -> None:
    env = _controlled_env(tmp_path)
    env.update(
        {
            "V2_RUNTIME_MODE": "general_availability",
            "V2_ALLOWED_SUBSCRIBER_IDS": "",
            "V2_PUBLIC_AUTHORITY_MANIFEST_PATH": "",
            "V2_ENABLE_CLOUDBEDS_WRITES": "true",
            "V2_ENABLE_BOKUN_WRITES": "true",
            "V2_ENABLE_STRIPE_LINKS": "true",
            "V2_ENABLE_MANYCHAT_DELIVERY": "true",
            "V2_ENABLE_MANYCHAT_HANDOFF": "true",
            "V2_REAL_EFFECTS_ACK": "ENABLE_V2_REAL_EFFECTS_FOR_CONTROLLED_TEST",
            "V2_GLOBAL_KILL_SWITCH": "false",
            "V2_WRITE_WINDOW_END": "",
            "V2_CLOUDBEDS_SOURCE_ID": "source-live",
            "V2_STRIPE_HOSTEL_ACCOUNT_PROFILE_ID": "stripe-account:hostel:test",
            "V2_STRIPE_AGENCY_ACCOUNT_PROFILE_ID": "stripe-account:agency:test",
            "V2_STRIPE_HOSTEL_SECRET_KEY": "rk_test_hostel",
            "V2_STRIPE_AGENCY_SECRET_KEY": "rk_test_agency",
            "V2_PAYMENT_RESULT_STORE_KEY_HEX": "cd" * 32,
            "V2_MANYCHAT_REPLY_FIELD_ID": "101",
            "V2_MANYCHAT_REPLY_FLOW_NS": "reply-flow",
            "V2_MANYCHAT_PAYMENT_LINK_FIELD_ID": "102",
            "V2_MANYCHAT_PAYMENT_DESCRIPTION_FIELD_ID": "103",
            "V2_MANYCHAT_PAYMENT_FLOW_NS": "payment-flow",
            "V2_MANYCHAT_HANDOFF_TAG_ID": "301",
            "V2_MANYCHAT_HANDOFF_FLOW_NS": "handoff-flow",
        }
    )

    settings = V2Settings.from_env(env)

    assert settings.runtime_mode is RuntimeMode.GENERAL_AVAILABILITY
    assert settings.allowed_subscriber_ids == ()
    assert settings.write_window_end is None
    assert settings.write_window_is_open(datetime.now(timezone.utc)) is True
    assert settings.real_effect_gates == {
        "cloudbeds_writes": True,
        "bokun_writes": True,
        "stripe_links": True,
        "wise_instructions": False,
        "pix_instructions": False,
        "manychat_delivery": True,
        "manychat_handoff": True,
    }

    with pytest.raises(ValueError, match="empty subscriber allowlist"):
        replace(settings, allowed_subscriber_ids=("1873018537",))


def test_critical_approval_ttl_is_configurable_and_strict(tmp_path: Path) -> None:
    env = _controlled_env(tmp_path)
    env["V2_CRITICAL_APPROVAL_TTL_SECONDS"] = "900"
    settings = V2Settings.from_env(env)
    assert settings.critical_approval_ttl_seconds == 900
    with pytest.raises(ValueError, match="critical_approval_ttl_seconds"):
        replace(settings, critical_approval_ttl_seconds=True)

    env["V2_CRITICAL_APPROVAL_TTL_SECONDS"] = "0"
    with pytest.raises(ValueError, match="critical_approval_ttl_seconds"):
        V2Settings.from_env(env)


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

    env["V2_WRITE_WINDOW_END"] = _future_window(minutes=30 * 24 * 60)
    env["V2_CLOUDBEDS_SOURCE_ID"] = "source-live"
    settings = V2Settings.from_env(env)
    assert settings.write_window_is_open(datetime.now(timezone.utc)) is True


def test_effect_gates_may_remain_open_without_an_auto_close_deadline(
    tmp_path: Path,
) -> None:
    env = _controlled_env(tmp_path)
    env.update(
        {
            "V2_ENABLE_CLOUDBEDS_WRITES": "true",
            "V2_ENABLE_BOKUN_WRITES": "true",
            "V2_ENABLE_STRIPE_LINKS": "true",
            "V2_ENABLE_PIX_INSTRUCTIONS": "true",
            "V2_ENABLE_WISE_INSTRUCTIONS": "true",
            "V2_ENABLE_MANYCHAT_DELIVERY": "true",
            "V2_REAL_EFFECTS_ACK": "ENABLE_V2_REAL_EFFECTS_FOR_CONTROLLED_TEST",
            "V2_GLOBAL_KILL_SWITCH": "false",
            "V2_WRITE_WINDOW_END": "",
            "V2_CLOUDBEDS_SOURCE_ID": "source-live",
            "V2_STRIPE_HOSTEL_ACCOUNT_PROFILE_ID": "stripe-account:hostel:test",
            "V2_STRIPE_AGENCY_ACCOUNT_PROFILE_ID": "stripe-account:agency:test",
            "V2_STRIPE_HOSTEL_SECRET_KEY": "sk_test_hostel",
            "V2_STRIPE_AGENCY_SECRET_KEY": "rk_test_agency",
            "V2_PAYMENT_RESULT_STORE_KEY_HEX": "cd" * 32,
            "V2_PAYMENT_INSTRUCTION_PATH": str(tmp_path / "payments.json"),
            "V2_MANYCHAT_REPLY_FIELD_ID": "101",
            "V2_MANYCHAT_REPLY_FLOW_NS": "reply-flow",
            "V2_MANYCHAT_PAYMENT_LINK_FIELD_ID": "102",
            "V2_MANYCHAT_PAYMENT_DESCRIPTION_FIELD_ID": "103",
            "V2_MANYCHAT_PAYMENT_FLOW_NS": "payment-flow",
        }
    )

    settings = V2Settings.from_env(env)

    assert settings.write_window_end is None
    assert settings.write_window_is_open(datetime.now(timezone.utc)) is True
    assert settings.enabled_payment_methods == ("stripe", "wise", "pix")
    assert settings.cloudbeds_writes_enabled is True
    assert settings.bokun_writes_enabled is True


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
            "V2_PAYMENT_RESULT_STORE_KEY_HEX": "ab" * 32,
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
    payment_key = env.pop("V2_PAYMENT_RESULT_STORE_KEY_HEX")
    with pytest.raises(ValueError, match="dedicated 32-byte result store key"):
        V2Settings.from_env(env)
    env["V2_PAYMENT_RESULT_STORE_KEY_HEX"] = payment_key
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
    assert settings.payment_result_store_key == bytes.fromhex(payment_key)
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


def test_settings_repr_redacts_every_secret_and_private_value(tmp_path: Path) -> None:
    env = _controlled_env(tmp_path)
    sentinels = {
        "V2_MANYCHAT_WEBHOOK_SECRET": "SENTINEL_WEBHOOK_PRIVATE",
        "V2_STRIPE_WEBHOOK_SECRET": "SENTINEL_STRIPE_WEBHOOK_PRIVATE",
        "V2_WISE_WEBHOOK_SECRET": "SENTINEL_WISE_WEBHOOK_PRIVATE",
        "V2_PIX_WEBHOOK_SECRET": "SENTINEL_PIX_WEBHOOK_PRIVATE",
        "V2_PIX_RECEIVER_PROFILE_ID": "SENTINEL_PIX_PROFILE_PRIVATE",
        "V2_WISE_SIGNER_PROFILE_ID": "SENTINEL_WISE_SIGNER_PRIVATE",
        "V2_WISE_ACCOUNT_PROFILE_ID": "SENTINEL_WISE_ACCOUNT_PRIVATE",
        "V2_STRIPE_ACCOUNT_PROFILE_ID": "SENTINEL_STRIPE_ACCOUNT_PRIVATE",
        "V2_CLOUDBEDS_API_KEY": "SENTINEL_CLOUDBEDS_PRIVATE",
        "V2_CLOUDBEDS_PROPERTY_ID": "SENTINEL_PROPERTY_PRIVATE",
        "V2_BOKUN_ACCESS_KEY": "SENTINEL_BOKUN_ACCESS_PRIVATE",
        "V2_BOKUN_SECRET_KEY": "SENTINEL_BOKUN_SECRET_PRIVATE",
        "V2_BOKUN_PRODUCT_MAP_JSON": (
            '{"product:buracao":"SENTINEL_BOKUN_PRODUCT_PRIVATE"}'
        ),
        "V2_MANYCHAT_API_KEY": "SENTINEL_MANYCHAT_API_PRIVATE",
        "V2_STRIPE_SECRET_KEY": "SENTINEL_STRIPE_LEGACY_PRIVATE",
        "V2_STRIPE_HOSTEL_SECRET_KEY": "SENTINEL_STRIPE_HOSTEL_PRIVATE",
        "V2_STRIPE_AGENCY_SECRET_KEY": "SENTINEL_STRIPE_AGENCY_PRIVATE",
        "V2_HERMES_SYSTEM_PROMPT": "SENTINEL_SYSTEM_PROMPT_PRIVATE",
    }
    env.update(sentinels)

    rendered = repr(V2Settings.from_env(env))

    assert rendered.startswith("V2Settings(")
    assert "process_role=" in rendered
    assert "runtime_mode=controlled_write" in rendered
    assert all(value not in rendered for value in sentinels.values())
    assert "SENTINEL_" not in rendered


def test_versioned_hermes_system_prompt_can_be_loaded_by_absolute_path(
    tmp_path: Path,
) -> None:
    env = _controlled_env(tmp_path)
    prompt = tmp_path / "v2-prompt.txt"
    prompt.write_text("Closed V2 prompt.\n", encoding="utf-8")
    env.pop("V2_HERMES_SYSTEM_PROMPT")
    env["V2_HERMES_SYSTEM_PROMPT_PATH"] = str(prompt)

    settings = V2Settings.from_env(env)

    assert settings.hermes_system_prompt == "Closed V2 prompt.\n"

    env["V2_HERMES_SYSTEM_PROMPT"] = "ambiguous inline prompt"
    with pytest.raises(ValueError, match="either inline or path"):
        V2Settings.from_env(env)


def test_hermes_system_prompt_path_fails_closed(tmp_path: Path) -> None:
    env = _controlled_env(tmp_path)
    env.pop("V2_HERMES_SYSTEM_PROMPT")
    env["V2_HERMES_SYSTEM_PROMPT_PATH"] = "relative-prompt.txt"
    with pytest.raises(ValueError, match="absolute"):
        V2Settings.from_env(env)

    env["V2_HERMES_SYSTEM_PROMPT_PATH"] = str(tmp_path / "missing.txt")
    with pytest.raises(ValueError, match="unreadable"):
        V2Settings.from_env(env)


def test_sqlite_paths_include_a_separate_deterministic_cloudbeds_audit_owner(
    tmp_path: Path,
) -> None:
    settings = V2Settings.from_env(_controlled_env(tmp_path))

    paths = settings.sqlite_paths

    assert paths["cloudbeds_audit"] == tmp_path / "v2-cloudbeds-audit.sqlite3"
    assert paths["bokun_audit"] == tmp_path / "v2-bokun-audit.sqlite3"
    assert paths["private_customer"] == tmp_path / "v2-private-customer.sqlite3"
    assert paths["cloudbeds_audit"] not in {
        paths["execution"],
        paths["payment_initiation"],
        paths["public_outbox"],
    }
    assert len(paths) == len(set(paths.values()))


def test_sqlite_paths_reject_private_customer_hardlink_alias(
    tmp_path: Path,
) -> None:
    settings = V2Settings.from_env(_controlled_env(tmp_path))
    boundary = settings.sqlite_paths["boundary"]
    boundary.write_bytes(b"boundary-owner")
    settings.sqlite_paths["private_customer"].hardlink_to(boundary)

    with pytest.raises(ValueError, match="physically distinct"):
        replace(settings)


def test_sqlite_paths_reject_existing_hardlink_aliases(tmp_path: Path) -> None:
    settings = V2Settings.from_env(_controlled_env(tmp_path))
    settings.sqlite_path.write_bytes(b"owner")
    settings.sqlite_paths["execution"].hardlink_to(settings.sqlite_path)

    with pytest.raises(ValueError, match="physically distinct"):
        replace(settings)
