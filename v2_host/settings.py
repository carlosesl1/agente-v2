"""Environment-owned settings for the standalone V2 host."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
import os
from pathlib import Path


_REAL_EFFECTS_ACK = "ENABLE_V2_REAL_EFFECTS_FOR_CONTROLLED_TEST"


def _env_bool(source: Mapping[str, str], name: str) -> bool:
    raw = source.get(name, "false").strip().casefold()
    if raw in {"false", "0"}:
        return False
    if raw in {"true", "1"}:
        return True
    raise ValueError(f"{name} must be true/false or 1/0")


@dataclass(frozen=True, slots=True)
class V2Settings:
    """Minimal fail-closed settings required by the ManyChat ingress."""

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
            raise ValueError(
                "financial webhook secrets and trust profiles are all-or-none"
            )
        if not isinstance(self.sqlite_path, Path) or not self.sqlite_path.is_absolute():
            raise ValueError("sqlite_path must be an absolute pathlib.Path")
        if type(self.max_body_bytes) is not int or self.max_body_bytes < 1:
            raise ValueError("max_body_bytes must be a positive exact integer")
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

    @classmethod
    def from_env(cls, environ: Mapping[str, str] | None = None) -> V2Settings:
        source = os.environ if environ is None else environ
        secret = source.get("V2_MANYCHAT_WEBHOOK_SECRET", "")
        raw_path = source.get("V2_SQLITE_PATH", "")
        if not raw_path:
            raise ValueError("V2_SQLITE_PATH is required and must be absolute")
        raw_limit = source.get("V2_MAX_WEBHOOK_BODY_BYTES", "65536")
        try:
            limit = int(raw_limit)
        except ValueError as exc:
            raise ValueError("V2_MAX_WEBHOOK_BODY_BYTES must be an integer") from exc
        return cls(
            webhook_secret=secret,
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
        )
