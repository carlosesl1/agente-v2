"""Standalone FastAPI ingress host for the Agente V2."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from datetime import datetime, timezone
import hmac
import json

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from v2_adapters.manychat import (
    ManyChatPayloadError,
    SubscriberAllowlist,
    parse_manychat_payload,
)
from v2_application.financial_webhooks import (
    FinancialProvider,
    FinancialWebhookInvalid,
    FinancialWebhookUnauthorized,
)
from v2_application.inbox import SQLiteInbox
from v2_application.payments import EvidenceConflict, EvidenceDisposition
from v2_contracts.channel import AcceptDisposition
from v2_host.settings import V2Settings


def _unique_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate JSON key: {key}")
        result[key] = value
    return result


def _load_json_object(body: bytes) -> dict[str, object]:
    try:
        value = json.loads(
            body.decode("utf-8", errors="strict"),
            object_pairs_hook=_unique_object,
            parse_constant=lambda token: (_ for _ in ()).throw(
                ValueError(f"non-finite JSON number: {token}")
            ),
        )
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
        raise ManyChatPayloadError("request body must be strict JSON") from exc
    if type(value) is not dict:
        raise ManyChatPayloadError("request body must be a JSON object")
    return value


def _secret_matches(provided: str, expected: str) -> bool:
    return hmac.compare_digest(provided.encode("utf-8"), expected.encode("utf-8"))


async def _bounded_body(request: Request, limit: int) -> bytes | JSONResponse:
    content_length = request.headers.get("content-length")
    if content_length is not None:
        try:
            declared_length = int(content_length)
        except ValueError:
            return JSONResponse(status_code=400, content={"status": "invalid"})
        if declared_length < 0:
            return JSONResponse(status_code=400, content={"status": "invalid"})
        if declared_length > limit:
            return JSONResponse(status_code=413, content={"status": "too_large"})
    body = await request.body()
    if len(body) > limit:
        return JSONResponse(status_code=413, content={"status": "too_large"})
    return body


def create_app(
    settings: V2Settings,
    inbox: SQLiteInbox,
    *,
    clock: Callable[[], datetime] | None = None,
    financial_verifiers: Mapping[FinancialProvider, object] | None = None,
    financial_evidence_acceptor: object | None = None,
    readiness: Callable[[], object] | None = None,
    require_financial_webhooks: bool = False,
) -> FastAPI:
    """Compose authenticated ingress with durable, role-owned acceptance."""

    if type(settings) is not V2Settings:
        raise TypeError("settings must be exact V2Settings")
    if type(inbox) is not SQLiteInbox:
        raise TypeError("inbox must be an exact SQLiteInbox")
    if inbox.path != settings.sqlite_path:
        raise ValueError("inbox path must match settings.sqlite_path")
    if type(require_financial_webhooks) is not bool:
        raise TypeError("require_financial_webhooks must be exact bool")
    if financial_verifiers is None:
        normalized_verifiers = None
    else:
        if not isinstance(financial_verifiers, Mapping) or set(
            financial_verifiers
        ) != set(FinancialProvider):
            raise ValueError("financial verifiers must cover every provider exactly")
        normalized_verifiers = dict(financial_verifiers)
        if any(
            not callable(getattr(verifier, "verify", None))
            for verifier in normalized_verifiers.values()
        ):
            raise TypeError("every financial verifier must expose verify")
    if financial_evidence_acceptor is not None and not callable(
        getattr(financial_evidence_acceptor, "accept", None)
    ):
        raise TypeError("financial_evidence_acceptor must expose accept")
    if require_financial_webhooks and (
        normalized_verifiers is None or financial_evidence_acceptor is None
    ):
        raise ValueError("complete API role requires every financial webhook port")
    now = clock or (lambda: datetime.now(timezone.utc))
    subscriber_allowlist = SubscriberAllowlist(settings.allowed_subscriber_ids)
    app = FastAPI(title="Agente V2", version="0.8.0")

    @app.get("/healthz")
    async def healthz() -> dict[str, str]:
        return {"status": "alive", "role": "api"}

    @app.get("/readyz")
    async def readyz() -> JSONResponse:
        if readiness is None:
            content = {
                "status": "ready",
                "real_effect_gates": settings.real_effect_gates,
            }
        else:
            snapshot = readiness()
            content = {
                "status": snapshot.status,
                "role": snapshot.role.value,
                "owner_counts": snapshot.owner_counts,
                "real_effect_gates": snapshot.real_effect_gates,
                "capabilities": snapshot.capabilities,
                "reasons": list(snapshot.reasons),
            }
        return JSONResponse(
            status_code=200 if content["status"] == "ready" else 503,
            content=content,
        )

    @app.post("/webhook/manychat")
    async def manychat_webhook(request: Request) -> JSONResponse:
        provided = request.headers.get("X-V2-Webhook-Secret", "")
        if not _secret_matches(provided, settings.webhook_secret):
            return JSONResponse(status_code=401, content={"status": "unauthorized"})
        bounded = await _bounded_body(request, settings.max_body_bytes)
        if type(bounded) is JSONResponse:
            return bounded
        try:
            payload = _load_json_object(bounded)
            if not subscriber_allowlist.allows(payload):
                return JSONResponse(status_code=403, content={"status": "forbidden"})
            event = parse_manychat_payload(payload, now())
        except ManyChatPayloadError:
            return JSONResponse(status_code=422, content={"status": "invalid"})
        disposition = inbox.accept(event)
        status_codes: dict[AcceptDisposition, int] = {
            AcceptDisposition.ACCEPTED: 202,
            AcceptDisposition.DUPLICATE: 200,
            AcceptDisposition.CONFLICT: 409,
        }
        return JSONResponse(
            status_code=status_codes[disposition],
            content={"status": disposition.value},
        )

    async def financial_webhook(
        provider: FinancialProvider,
        request: Request,
    ) -> JSONResponse:
        if normalized_verifiers is None or financial_evidence_acceptor is None:
            return JSONResponse(status_code=503, content={"status": "unavailable"})
        bounded = await _bounded_body(request, settings.max_body_bytes)
        if type(bounded) is JSONResponse:
            return bounded
        try:
            verified = normalized_verifiers[provider].verify(
                bounded,
                request.headers,
                received_at=now(),
            )
        except FinancialWebhookUnauthorized:
            return JSONResponse(status_code=401, content={"status": "unauthorized"})
        except FinancialWebhookInvalid:
            return JSONResponse(status_code=422, content={"status": "invalid"})
        try:
            accepted = financial_evidence_acceptor.accept(verified)
        except EvidenceConflict:
            return JSONResponse(status_code=409, content={"status": "conflict"})
        disposition = accepted.disposition
        return JSONResponse(
            status_code=(202 if disposition is EvidenceDisposition.ACCEPTED else 200),
            content={
                "status": disposition.value,
                "payment_id": accepted.payment_id,
                "claim_key": accepted.claim_key,
            },
        )

    @app.post("/webhook/payments/stripe")
    async def stripe_webhook(request: Request) -> JSONResponse:
        return await financial_webhook(FinancialProvider.STRIPE, request)

    @app.post("/webhook/payments/wise")
    async def wise_webhook(request: Request) -> JSONResponse:
        return await financial_webhook(FinancialProvider.WISE, request)

    @app.post("/webhook/payments/pix")
    async def pix_webhook(request: Request) -> JSONResponse:
        return await financial_webhook(FinancialProvider.PIX, request)

    return app
