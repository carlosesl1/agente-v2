"""Executable API role for the standalone V2 image."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from contextlib import asynccontextmanager
from datetime import datetime
import os

from fastapi import FastAPI

from reservation_followup import PaymentEvidenceTrust
from v2_application.financial_webhooks import (
    FinancialEvidenceAcceptor,
    FinancialProvider,
    HmacFinancialWebhookVerifier,
)
from v2_host.app import create_app
from v2_host.composition import V2Container, V2Role
from v2_host.settings import V2Settings


def build_api_app(
    settings: V2Settings,
    *,
    clock: Callable[[], datetime] | None = None,
) -> FastAPI:
    if type(settings) is not V2Settings:
        raise TypeError("settings must be exact V2Settings")
    if not settings.financial_webhooks_configured:
        raise ValueError("API role requires complete financial webhook configuration")
    container = V2Container.open(settings=settings, role=V2Role.API)
    try:
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
        app = create_app(
            settings,
            container.inbox,
            clock=clock,
            financial_verifiers=verifiers,
            financial_evidence_acceptor=FinancialEvidenceAcceptor(
                settings.sqlite_paths["followup"]
            ),
            readiness=container.readiness,
            require_financial_webhooks=True,
        )
    except BaseException:
        container.close()
        raise
    app.state.v2_container = container

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        try:
            yield
        finally:
            container.close()

    app.router.lifespan_context = lifespan

    return app


def app_from_env(environ: Mapping[str, str] | None = None) -> FastAPI:
    return build_api_app(V2Settings.from_env(environ))


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
