"""Executable API role for the standalone V2 image."""

from __future__ import annotations

import os
from collections.abc import Callable, Mapping
from contextlib import asynccontextmanager
from datetime import datetime, timezone

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from starlette.concurrency import run_in_threadpool

from reservation_followup import PaymentEvidenceTrust
from v2_application.financial_webhooks import (
    FinancialEvidenceAcceptor,
    FinancialProvider,
    FinancialWebhookInvalid,
    FinancialWebhookUnauthorized,
    HmacFinancialWebhookVerifier,
)
from v2_application.native_stripe import (
    NativeStripeIgnored,
    NativeStripeIngress,
    NativeStripeUnresolved,
)
from v2_application.payments import EvidenceConflict
from v2_host.app import _bounded_body, create_app
from v2_host.composition import V2Container, V2Role
from v2_host.settings import V2ProcessRole, V2Settings


def build_api_app(
    settings: V2Settings,
    *,
    clock: Callable[[], datetime] | None = None,
) -> FastAPI:
    if type(settings) is not V2Settings:
        raise TypeError("settings must be exact V2Settings")
    container = V2Container.open(settings=settings, role=V2Role.API)
    try:
        verifiers = None
        acceptor = None
        if settings.financial_webhooks_configured:
            trust = PaymentEvidenceTrust(
                pix_receiver_profile_id=settings.pix_receiver_profile_id,
                wise_signer_profile_id=settings.wise_signer_profile_id,
                wise_account_profile_id=settings.wise_account_profile_id,
                stripe_account_profile_id=settings.stripe_account_profile_id,
            )
            secrets = {
                FinancialProvider.STRIPE: settings.stripe_webhook_secret,
                FinancialProvider.WISE: settings.wise_webhook_secret,
                FinancialProvider.PIX: settings.pix_webhook_secret,
            }
            verifiers = {
                provider: HmacFinancialWebhookVerifier(
                    provider=provider,
                    secret=secret,
                    trust=trust,
                )
                for provider, secret in secrets.items()
            }
            acceptor = FinancialEvidenceAcceptor(settings.sqlite_paths["followup"])
        app = create_app(
            settings,
            container.inbox,
            clock=clock,
            financial_verifiers=verifiers,
            financial_evidence_acceptor=acceptor,
            readiness=container.readiness,
            require_financial_webhooks=False,
            ops_recorder=container.ops_recorder,
            ops_full_content=settings.ops_trace_full_content,
        )
    except BaseException:
        container.close()
        raise
    app.state.v2_container = container
    native = NativeStripeIngress(
        paths=settings.sqlite_paths,
        accounts=settings.stripe_native_accounts,
        result_key=settings.stripe_native_result_key,
        allowed_subscribers=settings.allowed_subscriber_ids,
    ) if settings.stripe_native_accounts else None
    app.state.native_stripe = native

    @app.post("/webhook/payments/stripe/{business_unit}")
    async def native_stripe_webhook(business_unit: str, request: Request):
        if native is None or business_unit not in native.accounts:
            return JSONResponse(status_code=503, content={"status": "unavailable"})
        body = await _bounded_body(request, settings.max_body_bytes)
        if isinstance(body, JSONResponse):
            return body
        try:
            accepted = await run_in_threadpool(
                native.accept, business_unit, body, request.headers,
                received_at=(clock or (lambda: datetime.now(timezone.utc)))(),
            )
        except FinancialWebhookUnauthorized:
            return JSONResponse(status_code=401, content={"status": "unauthorized"})
        except FinancialWebhookInvalid:
            return JSONResponse(status_code=422, content={"status": "invalid"})
        except NativeStripeIgnored:
            return JSONResponse(status_code=200, content={"status": "ignored"})
        except EvidenceConflict:
            return JSONResponse(status_code=409, content={"status": "conflict"})
        except NativeStripeUnresolved:
            return JSONResponse(status_code=503, content={"status": "unresolved"})
        return JSONResponse(
            status_code=202 if accepted.disposition.value == "accepted" else 200,
            content={"status": accepted.disposition.value},
        )

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        try:
            yield
        finally:
            container.close()

    app.router.lifespan_context = lifespan

    return app


def app_from_env(environ: Mapping[str, str] | None = None) -> FastAPI:
    return build_api_app(
        V2Settings.from_env(environ, process_role=V2ProcessRole.API)
    )


def main() -> None:
    import uvicorn

    host = os.environ.get("V2_API_HOST", "0.0.0.0")
    raw_port = os.environ.get("V2_API_PORT", "8080")
    try:
        port = int(raw_port)
    except ValueError as exc:
        raise SystemExit("V2_API_PORT must be an integer") from exc
    if port < 1 or port > 65_535:
        raise SystemExit("V2_API_PORT must be between 1 and 65535")
    uvicorn.run(app_from_env(), host=host, port=port)


if __name__ == "__main__":
    main()


__all__ = ["app_from_env", "build_api_app", "main"]
