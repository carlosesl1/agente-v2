"""Environment-owned settings for the standalone V2 host."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
import os
from pathlib import Path


@dataclass(frozen=True, slots=True)
class V2Settings:
    """Minimal fail-closed settings required by the ManyChat ingress."""

    webhook_secret: str
    sqlite_path: Path
    max_body_bytes: int = 65_536

    def __post_init__(self) -> None:
        if type(self.webhook_secret) is not str or not self.webhook_secret.strip():
            raise ValueError("webhook_secret is required")
        if "\x00" in self.webhook_secret:
            raise ValueError("webhook_secret may not contain NUL")
        if not isinstance(self.sqlite_path, Path) or not self.sqlite_path.is_absolute():
            raise ValueError("sqlite_path must be an absolute pathlib.Path")
        if type(self.max_body_bytes) is not int or self.max_body_bytes < 1:
            raise ValueError("max_body_bytes must be a positive exact integer")

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
            max_body_bytes=limit,
        )
