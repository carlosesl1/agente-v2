"""Environment-owned settings for the standalone V2 host."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import date
from enum import Enum
import json
import os
from pathlib import Path


_REAL_EFFECTS_ACK = "ENABLE_V2_REAL_EFFECTS_FOR_CONTROLLED_TEST"


class RuntimeMode(str, Enum):
    API_ONLY = "api_only"
    DARK_READ_ONLY = "dark_read_only"
    SHADOW = "shadow"
    CONTROLLED_WRITE = "controlled_write"


def _env_bool(source: Mapping[str, str], name: str, *, default: bool = False) -> bool:
    raw = source.get(name, "true" if default else "false").strip().casefold()
    if raw in {"false", "0"}:
        return False
    if raw in {"true", "1"}:
        return True
    raise ValueError(f"{name} must be true/false or 1/0")


def _json_string_map(raw: str, name: str) -> dict[str, str]:
    if not raw:
        return {}
    try:
        value = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ValueError(f"{name} must be a JSON object") from exc
    if type(value) is not dict or any(
        type(key) is not str or not key or type(item) is not str or not item
        for key, item in value.items()
    ):
        raise ValueError(f"{name} must map non-empty strings to non-empty strings")
    return dict(value)


def _json_command(raw: str) -> tuple[str, ...]:
    if not raw:
        return ()
    try:
        value = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ValueError("V2_HERMES_COMMAND_JSON must be a JSON array") from exc
    if type(value) is not list or not value or any(
        type(item) is not str or not item or "\x00" in item for item in value
    ):
        raise ValueError("V2_HERMES_COMMAND_JSON must contain non-empty strings")
    return tuple(value)


def _hex_key(raw: str) -> bytes:
    if not raw:
        return b""
    try:
        value = bytes.fromhex(raw)
    except ValueError as exc:
        raise ValueError("V2_HERMES_TRANSCRIPT_KEY_HEX must be hexadecimal") from exc
    return value


@dataclass(frozen=True, slots=True)
class V2Settings:
    """Fail-closed settings for API and productive worker roles."""

    webhook_secret: str
    sqlite_path: Path
    stripe_webhook_secret: str = ""
    wise_webhook_secret: str = ""
    pix_webhook_secret: str = ""
    pix_receiver_profile_id: str = ""
    wise_signer_profile_id: str = ""
    wise_account_profile_id: str = ""
    stripe_account_profile_id: str = ""
    max_body_bytes: int = 65_536
    cloudbeds_writes_enabled: bool = False
    bokun_writes_enabled: bool = False
    stripe_links_enabled: bool = False
    manychat_delivery_enabled: bool = False
    real_effects_ack: str = ""
    runtime_mode: RuntimeMode = RuntimeMode.API_ONLY
    cloudbeds_api_key: str = ""
    cloudbeds_property_id: str = ""
    cloudbeds_base_url: str = "https://api.cloudbeds.com"
    bokun_access_key: str = ""
    bokun_secret_key: str = ""
    bokun_product_map: dict[str, str] = field(default_factory=dict)
    bokun_base_url: str = "https://api.bokun.io"
    manychat_api_key: str = ""
    manychat_base_url: str = "https://api.manychat.com"
    hermes_command: tuple[str, ...] = ()
    hermes_system_prompt: str = ""
    hermes_transcript_key: bytes = b""
    hermes_timeout_seconds: int = 45
    knowledge_base_path: Path | None = None
    public_authority_manifest_path: Path | None = None
    public_authority_hmac_key: bytes = b""
    require_worker_heartbeat: bool = False
    worker_heartbeat_max_age_seconds: int = 10
    read_probe_check_in: str = ""
    read_probe_check_out: str = ""
    read_probe_activity_date: str = ""
    read_probe_product_id: str = ""
    read_probe_interval_seconds: int = 60

    def __post_init__(self) -> None:
        if type(self.webhook_secret) is not str or not self.webhook_secret.strip():
            raise ValueError("webhook_secret is required")
        if "\x00" in self.webhook_secret:
            raise ValueError("webhook_secret may not contain NUL")
        financial_values = (
            self.stripe_webhook_secret,
            self.wise_webhook_secret,
            self.pix_webhook_secret,
            self.pix_receiver_profile_id,
            self.wise_signer_profile_id,
            self.wise_account_profile_id,
            self.stripe_account_profile_id,
        )
        if any(type(value) is not str or "\x00" in value for value in financial_values):
            raise ValueError("financial webhook settings must be NUL-free exact text")
        if any(bool(value) for value in financial_values) and not all(
            bool(value) for value in financial_values
        ):
            raise ValueError("financial webhook secrets and trust profiles are all-or-none")
        if not isinstance(self.sqlite_path, Path) or not self.sqlite_path.is_absolute():
            raise ValueError("sqlite_path must be an absolute pathlib.Path")
        if type(self.max_body_bytes) is not int or self.max_body_bytes < 1:
            raise ValueError("max_body_bytes must be a positive exact integer")
        if type(self.runtime_mode) is not RuntimeMode:
            raise TypeError("runtime_mode must be exact RuntimeMode")
        gates = (
            self.cloudbeds_writes_enabled,
            self.bokun_writes_enabled,
            self.stripe_links_enabled,
            self.manychat_delivery_enabled,
        )
        if any(type(value) is not bool for value in gates):
            raise TypeError("real effect gates must be exact booleans")
        if any(gates) and self.real_effects_ack != _REAL_EFFECTS_ACK:
            raise ValueError("real effects require exact operational acknowledgment")
        if self.runtime_mode is not RuntimeMode.CONTROLLED_WRITE and any(gates):
            raise ValueError("real effect gates require controlled_write runtime mode")
        if type(self.bokun_product_map) is not dict or any(
            type(key) is not str or not key or type(value) is not str or not value
            for key, value in self.bokun_product_map.items()
        ):
            raise ValueError("bokun_product_map must map exact non-empty strings")
        object.__setattr__(self, "bokun_product_map", dict(self.bokun_product_map))
        for name in ("cloudbeds_base_url", "bokun_base_url", "manychat_base_url"):
            value = getattr(self, name)
            if type(value) is not str or not value.startswith("https://") or "\x00" in value:
                raise ValueError(f"{name} must be an HTTPS URL")
        if type(self.hermes_command) is not tuple or any(
            type(item) is not str or not item or "\x00" in item for item in self.hermes_command
        ):
            raise ValueError("hermes_command must be an exact string tuple")
        if type(self.hermes_transcript_key) is not bytes:
            raise TypeError("hermes_transcript_key must be exact bytes")
        if type(self.hermes_timeout_seconds) is not int or self.hermes_timeout_seconds < 1:
            raise ValueError("hermes_timeout_seconds must be positive")
        for name in ("knowledge_base_path", "public_authority_manifest_path"):
            path = getattr(self, name)
            if path is not None and (
                not isinstance(path, Path) or not path.is_absolute()
            ):
                raise ValueError(f"{name} must be absolute")
        if type(self.public_authority_hmac_key) is not bytes:
            raise TypeError("public_authority_hmac_key must be exact bytes")
        if type(self.require_worker_heartbeat) is not bool:
            raise TypeError("require_worker_heartbeat must be exact bool")
        if (
            type(self.worker_heartbeat_max_age_seconds) is not int
            or self.worker_heartbeat_max_age_seconds < 1
        ):
            raise ValueError("worker heartbeat max age must be positive")
        if (
            type(self.read_probe_interval_seconds) is not int
            or self.read_probe_interval_seconds < 10
            or self.read_probe_interval_seconds > 3_600
        ):
            raise ValueError("read probe interval must be between 10 and 3600 seconds")
        if self.runtime_mode in {
            RuntimeMode.DARK_READ_ONLY,
            RuntimeMode.SHADOW,
            RuntimeMode.CONTROLLED_WRITE,
        }:
            missing = []
            if not self.cloudbeds_api_key or not self.cloudbeds_property_id:
                missing.append("Cloudbeds read credentials")
            if not self.bokun_access_key or not self.bokun_secret_key or not self.bokun_product_map:
                missing.append("Bókun read credentials/product map")
            probe_values = (
                self.read_probe_check_in,
                self.read_probe_check_out,
                self.read_probe_activity_date,
                self.read_probe_product_id,
            )
            if any(type(value) is not str or not value for value in probe_values):
                missing.append("read probe dates/product")
            else:
                try:
                    check_in = date.fromisoformat(self.read_probe_check_in)
                    check_out = date.fromisoformat(self.read_probe_check_out)
                    date.fromisoformat(self.read_probe_activity_date)
                except ValueError as exc:
                    raise ValueError("read probe dates must be ISO calendar dates") from exc
                if check_out <= check_in:
                    raise ValueError("read probe checkout must be after checkin")
                if self.read_probe_product_id not in self.bokun_product_map:
                    raise ValueError("read probe product must be a configured canonical ID")
            if missing:
                raise ValueError("read runtime requires " + ", ".join(missing))
        if self.runtime_mode in {RuntimeMode.SHADOW, RuntimeMode.CONTROLLED_WRITE}:
            missing = []
            if not self.manychat_api_key:
                missing.append("ManyChat profile credential")
            if not self.hermes_command or not self.hermes_system_prompt or len(self.hermes_transcript_key) < 32:
                missing.append("Hermes model command/prompt/transcript key")
            if self.knowledge_base_path is None:
                missing.append("knowledge base")
            if self.public_authority_manifest_path is None or len(self.public_authority_hmac_key) < 32:
                missing.append("authenticated public authority manifest")
            if missing:
                raise ValueError("shadow runtime requires " + ", ".join(missing))
        paths = tuple(self.sqlite_paths.values())
        if len(set(paths)) != len(paths):
            raise ValueError("sqlite owner paths must be distinct")

    @property
    def real_effect_gates(self) -> dict[str, bool]:
        return {
            "bokun_writes": self.bokun_writes_enabled,
            "cloudbeds_writes": self.cloudbeds_writes_enabled,
            "manychat_delivery": self.manychat_delivery_enabled,
            "stripe_links": self.stripe_links_enabled,
        }

    @property
    def all_real_effect_gates_closed(self) -> bool:
        return not any(self.real_effect_gates.values())

    @property
    def financial_webhooks_configured(self) -> bool:
        return all(
            (
                self.stripe_webhook_secret,
                self.wise_webhook_secret,
                self.pix_webhook_secret,
                self.pix_receiver_profile_id,
                self.wise_signer_profile_id,
                self.wise_account_profile_id,
                self.stripe_account_profile_id,
            )
        )

    @property
    def read_providers_configured(self) -> bool:
        return bool(
            self.cloudbeds_api_key
            and self.cloudbeds_property_id
            and self.bokun_access_key
            and self.bokun_secret_key
            and self.bokun_product_map
        )

    @property
    def sqlite_paths(self) -> dict[str, Path]:
        parent = self.sqlite_path.parent
        return {
            "inbox": self.sqlite_path,
            "boundary": parent / "v2-boundary.sqlite3",
            "execution": parent / "v2-execution.sqlite3",
            "followup": parent / "v2-followup.sqlite3",
            "payment_initiation": parent / "v2-payment-initiation.sqlite3",
            "public_outbox": parent / "v2-public-outbox.sqlite3",
        }

    @property
    def worker_heartbeat_path(self) -> Path:
        return self.sqlite_path.parent / "v2-worker-heartbeat.json"

    @classmethod
    def from_env(cls, environ: Mapping[str, str] | None = None) -> "V2Settings":
        source = os.environ if environ is None else environ
        raw_path = source.get("V2_SQLITE_PATH", "")
        if not raw_path:
            raise ValueError("V2_SQLITE_PATH is required and must be absolute")
        try:
            limit = int(source.get("V2_MAX_WEBHOOK_BODY_BYTES", "65536"))
            timeout = int(source.get("V2_HERMES_TIMEOUT_SECONDS", "45"))
            heartbeat_age = int(source.get("V2_WORKER_HEARTBEAT_MAX_AGE_SECONDS", "10"))
            read_probe_interval = int(source.get("V2_READ_PROBE_INTERVAL_SECONDS", "60"))
        except ValueError as exc:
            raise ValueError("numeric V2 settings must be integers") from exc
        try:
            mode = RuntimeMode(source.get("V2_RUNTIME_MODE", RuntimeMode.API_ONLY.value))
        except ValueError as exc:
            raise ValueError("V2_RUNTIME_MODE is outside the closed catalog") from exc
        authority_path = source.get("V2_PUBLIC_AUTHORITY_MANIFEST_PATH", "")
        knowledge_path = source.get("V2_KNOWLEDGE_BASE_PATH", "")
        return cls(
            webhook_secret=source.get("V2_MANYCHAT_WEBHOOK_SECRET", ""),
            sqlite_path=Path(raw_path),
            stripe_webhook_secret=source.get("V2_STRIPE_WEBHOOK_SECRET", ""),
            wise_webhook_secret=source.get("V2_WISE_WEBHOOK_SECRET", ""),
            pix_webhook_secret=source.get("V2_PIX_WEBHOOK_SECRET", ""),
            pix_receiver_profile_id=source.get("V2_PIX_RECEIVER_PROFILE_ID", ""),
            wise_signer_profile_id=source.get("V2_WISE_SIGNER_PROFILE_ID", ""),
            wise_account_profile_id=source.get("V2_WISE_ACCOUNT_PROFILE_ID", ""),
            stripe_account_profile_id=source.get("V2_STRIPE_ACCOUNT_PROFILE_ID", ""),
            max_body_bytes=limit,
            cloudbeds_writes_enabled=_env_bool(source, "V2_ENABLE_CLOUDBEDS_WRITES"),
            bokun_writes_enabled=_env_bool(source, "V2_ENABLE_BOKUN_WRITES"),
            stripe_links_enabled=_env_bool(source, "V2_ENABLE_STRIPE_LINKS"),
            manychat_delivery_enabled=_env_bool(source, "V2_ENABLE_MANYCHAT_DELIVERY"),
            real_effects_ack=source.get("V2_REAL_EFFECTS_ACK", ""),
            runtime_mode=mode,
            cloudbeds_api_key=source.get("V2_CLOUDBEDS_API_KEY", ""),
            cloudbeds_property_id=source.get("V2_CLOUDBEDS_PROPERTY_ID", ""),
            cloudbeds_base_url=source.get("V2_CLOUDBEDS_BASE_URL", "https://api.cloudbeds.com"),
            bokun_access_key=source.get("V2_BOKUN_ACCESS_KEY", ""),
            bokun_secret_key=source.get("V2_BOKUN_SECRET_KEY", ""),
            bokun_product_map=_json_string_map(
                source.get("V2_BOKUN_PRODUCT_MAP_JSON", ""),
                "V2_BOKUN_PRODUCT_MAP_JSON",
            ),
            bokun_base_url=source.get("V2_BOKUN_BASE_URL", "https://api.bokun.io"),
            manychat_api_key=source.get("V2_MANYCHAT_API_KEY", ""),
            manychat_base_url=source.get("V2_MANYCHAT_BASE_URL", "https://api.manychat.com"),
            hermes_command=_json_command(source.get("V2_HERMES_COMMAND_JSON", "")),
            hermes_system_prompt=source.get("V2_HERMES_SYSTEM_PROMPT", ""),
            hermes_transcript_key=_hex_key(source.get("V2_HERMES_TRANSCRIPT_KEY_HEX", "")),
            hermes_timeout_seconds=timeout,
            knowledge_base_path=Path(knowledge_path) if knowledge_path else None,
            public_authority_manifest_path=Path(authority_path) if authority_path else None,
            public_authority_hmac_key=_hex_key(
                source.get("V2_PUBLIC_AUTHORITY_HMAC_KEY_HEX", "")
            ),
            require_worker_heartbeat=_env_bool(
                source,
                "V2_REQUIRE_WORKER_HEARTBEAT",
                default=mode is not RuntimeMode.API_ONLY,
            ),
            worker_heartbeat_max_age_seconds=heartbeat_age,
            read_probe_check_in=source.get("V2_READ_PROBE_CHECK_IN", ""),
            read_probe_check_out=source.get("V2_READ_PROBE_CHECK_OUT", ""),
            read_probe_activity_date=source.get("V2_READ_PROBE_ACTIVITY_DATE", ""),
            read_probe_product_id=source.get("V2_READ_PROBE_PRODUCT_ID", ""),
            read_probe_interval_seconds=read_probe_interval,
        )


__all__ = ["RuntimeMode", "V2Settings"]
