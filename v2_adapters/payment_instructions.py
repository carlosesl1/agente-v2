"""Immutable, versioned public payment instructions bound to opaque profiles."""

from __future__ import annotations

import json
from pathlib import Path

from v2_contracts.payments import BusinessUnit


class FilePaymentInstructionCatalog:
    def __init__(
        self,
        *,
        path: Path,
        receiver_profiles: dict[BusinessUnit, str],
    ) -> None:
        if not isinstance(path, Path) or not path.is_absolute():
            raise ValueError("payment instruction path must be absolute")
        if type(receiver_profiles) is not dict or set(receiver_profiles) != set(
            BusinessUnit
        ):
            raise ValueError("receiver profiles must cover both business units")
        if any(type(value) is not str or not value for value in receiver_profiles.values()):
            raise ValueError("receiver profiles must be exact non-empty text")
        if len(set(receiver_profiles.values())) != 2:
            raise ValueError("receiver profiles must be distinct")
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            raise ValueError("payment instruction catalog is unreadable") from exc
        if type(raw) is not dict or set(raw) != {
            "schema",
            "version",
            "hostel",
            "agency",
        }:
            raise ValueError("payment instruction catalog fields mismatch")
        if raw["schema"] != "v2-payment-instructions-v1":
            raise ValueError("payment instruction catalog schema mismatch")
        if type(raw["version"]) is not str or not raw["version"]:
            raise ValueError("payment instruction catalog version is invalid")
        by_profile: dict[str, dict[str, str]] = {}
        for unit in BusinessUnit:
            value = raw[unit.value]
            if type(value) is not dict or set(value) != {"pix", "wise"}:
                raise ValueError("payment instruction unit fields mismatch")
            if any(
                type(text) is not str or not text.strip() or "\x00" in text
                for text in value.values()
            ):
                raise ValueError("payment instructions must be non-empty NUL-free text")
            by_profile[receiver_profiles[unit]] = dict(value)
        self._by_profile = by_profile

    def wise_instructions(self) -> dict[str, str]:
        return {
            profile: methods["wise"]
            for profile, methods in self._by_profile.items()
        }

    def pix_instruction(self, profile: str) -> str:
        try:
            return self._by_profile[profile]["pix"]
        except KeyError as exc:
            raise ValueError("Pix receiver profile is not configured") from exc


__all__ = ["FilePaymentInstructionCatalog"]
