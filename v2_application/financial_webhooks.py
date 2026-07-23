"""Authenticated provider webhook verification and short-lived evidence acceptance."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import Enum
import hashlib
import hmac
import json
from pathlib import Path
import re
from typing import Protocol

from reservation_followup import (
    PaymentEvidenceRecorded,
    PaymentEvidenceTrust,
    PixVisualEvidence,
    VerifiedStripeEvent,
    VerifiedWiseCredit,
    from_wire_json,
)
from reservation_followup.sqlite_store import SQLiteFollowupUnitOfWork
from v2_application.payments import EvidenceAcceptance, V2PaymentEvidenceGateway

_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/-]{0,255}$")
_HASH_RE = re.compile(r"^[0-9a-f]{64}$")


class FinancialProvider(str, Enum):
    STRIPE = "stripe"
    WISE = "wise"
    PIX = "pix"


class FinancialWebhookUnauthorized(RuntimeError):
    pass


class FinancialWebhookInvalid(ValueError):
    pass


def _utc(value: object, name: str) -> datetime:
    if (
        type(value) is not datetime
        or value.tzinfo is None
        or value.utcoffset() != timedelta(0)
    ):
        raise ValueError(f"{name} must be an exact UTC datetime")
    return value


def _identifier(value: object, name: str) -> str:
    if type(value) is not str or _ID_RE.fullmatch(value) is None:
        raise ValueError(f"{name} must be a canonical identifier")
    return value


def _unique_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise FinancialWebhookInvalid(f"duplicate JSON key: {key}")
        result[key] = value
    return result


@dataclass(frozen=True, slots=True)
class VerifiedFinancialWebhook:
    provider: FinancialProvider
    external_event_id: str
    payment_id: str
    expected_revision: int
    event: PaymentEvidenceRecorded
    body_hash: str

    def __post_init__(self) -> None:
        if type(self.provider) is not FinancialProvider:
            raise TypeError("provider must be exact FinancialProvider")
        _identifier(self.external_event_id, "external_event_id")
        _identifier(self.payment_id, "payment_id")
        if type(self.expected_revision) is not int or self.expected_revision < 1:
            raise ValueError("expected_revision must be >= 1")
        if type(self.event) is not PaymentEvidenceRecorded:
            raise TypeError("event must be exact PaymentEvidenceRecorded")
        if self.event.payment_id != self.payment_id:
            raise ValueError("webhook payment_id diverged from evidence event")
        if (
            type(self.body_hash) is not str
            or _HASH_RE.fullmatch(self.body_hash) is None
        ):
            raise ValueError("body_hash must be a lowercase SHA-256")


class FinancialWebhookVerifier(Protocol):
    def verify(
        self,
        body: bytes,
        headers: Mapping[str, str],
        *,
        received_at: datetime,
    ) -> VerifiedFinancialWebhook: ...


class HmacFinancialWebhookVerifier:
    """Verify raw bytes before decoding a normalized provider evidence envelope."""

    def __init__(
        self,
        *,
        provider: FinancialProvider,
        secret: str,
        trust: PaymentEvidenceTrust,
    ) -> None:
        if type(provider) is not FinancialProvider:
            raise TypeError("provider must be exact FinancialProvider")
        if type(secret) is not str or not secret or "\x00" in secret:
            raise ValueError("secret must be non-empty NUL-free text")
        if type(trust) is not PaymentEvidenceTrust:
            raise TypeError("trust must be exact PaymentEvidenceTrust")
        self.provider = provider
        self._secret = secret.encode("utf-8")
        self._trust = trust
        self.header_name = f"X-V2-{provider.value.title()}-Signature"

    def verify(
        self,
        body: bytes,
        headers: Mapping[str, str],
        *,
        received_at: datetime,
    ) -> VerifiedFinancialWebhook:
        if type(body) is not bytes or not body:
            raise FinancialWebhookInvalid("financial webhook body must be bytes")
        _utc(received_at, "received_at")
        provided = headers.get(self.header_name, "")
        expected = "sha256=" + hmac.new(self._secret, body, hashlib.sha256).hexdigest()
        if type(provided) is not str or not hmac.compare_digest(provided, expected):
            raise FinancialWebhookUnauthorized("financial webhook signature mismatch")
        try:
            value = json.loads(
                body.decode("utf-8", errors="strict"),
                object_pairs_hook=_unique_object,
                parse_constant=lambda token: (_ for _ in ()).throw(
                    FinancialWebhookInvalid(f"non-finite JSON number: {token}")
                ),
            )
        except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
            raise FinancialWebhookInvalid(
                "financial webhook must be strict JSON"
            ) from exc
        expected_fields = {
            "provider",
            "external_event_id",
            "payment_id",
            "expected_revision",
            "event_wire",
        }
        if type(value) is not dict or set(value) != expected_fields:
            raise FinancialWebhookInvalid("financial webhook fields mismatch")
        if value["provider"] != self.provider.value:
            raise FinancialWebhookInvalid("financial webhook provider mismatch")
        if type(value["event_wire"]) is not str:
            raise FinancialWebhookInvalid("event_wire must be text")
        try:
            event = from_wire_json(value["event_wire"], PaymentEvidenceRecorded)
        except (TypeError, ValueError) as exc:
            raise FinancialWebhookInvalid("financial evidence wire is invalid") from exc
        if event.trust != self._trust:
            raise FinancialWebhookInvalid("financial evidence trust profiles diverged")
        if event.recorded_at > received_at:
            raise FinancialWebhookInvalid("financial evidence is future-dated")
        evidence = event.evidence
        expected_type = {
            FinancialProvider.STRIPE: VerifiedStripeEvent,
            FinancialProvider.WISE: VerifiedWiseCredit,
            FinancialProvider.PIX: PixVisualEvidence,
        }[self.provider]
        if type(evidence) is not expected_type:
            raise FinancialWebhookInvalid("financial evidence type/provider mismatch")
        if self.provider is FinancialProvider.STRIPE:
            if evidence.signature_verified is not True:
                raise FinancialWebhookInvalid("Stripe signature is not verified")
            intrinsic_id = evidence.event_id
        elif self.provider is FinancialProvider.WISE:
            if evidence.signature_verified is not True:
                raise FinancialWebhookInvalid("Wise signature is not verified")
            intrinsic_id = evidence.transaction_fingerprint
        else:
            intrinsic_id = evidence.normalized_e2e
        if value["external_event_id"] != intrinsic_id:
            raise FinancialWebhookInvalid("external event identity diverged")
        try:
            return VerifiedFinancialWebhook(
                provider=self.provider,
                external_event_id=value["external_event_id"],
                payment_id=value["payment_id"],
                expected_revision=value["expected_revision"],
                event=event,
                body_hash=hashlib.sha256(body).hexdigest(),
            )
        except (TypeError, ValueError) as exc:
            raise FinancialWebhookInvalid(
                "verified financial envelope is invalid"
            ) from exc


class FinancialEvidenceAcceptor:
    """Open the followup owner only for the duration of one API request."""

    def __init__(self, path: Path) -> None:
        if not isinstance(path, Path) or not path.is_absolute():
            raise ValueError("path must be an absolute pathlib.Path")
        self.path = path

    def accept(self, webhook: VerifiedFinancialWebhook) -> EvidenceAcceptance:
        if type(webhook) is not VerifiedFinancialWebhook:
            raise TypeError("webhook must be exact VerifiedFinancialWebhook")
        store = SQLiteFollowupUnitOfWork.open(self.path)
        try:
            return V2PaymentEvidenceGateway(store).accept(
                payment_id=webhook.payment_id,
                expected_revision=webhook.expected_revision,
                event=webhook.event,
            )
        finally:
            store.close()


__all__ = [
    "FinancialEvidenceAcceptor",
    "FinancialProvider",
    "FinancialWebhookInvalid",
    "FinancialWebhookUnauthorized",
    "HmacFinancialWebhookVerifier",
    "VerifiedFinancialWebhook",
]
