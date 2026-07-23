"""Standalone FastAPI ingress host for the Agente V2."""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime, timezone
import hmac
import json

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from v2_adapters.manychat import ManyChatPayloadError, parse_manychat_payload
from v2_application.inbox import SQLiteInbox
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


def create_app(
    settings: V2Settings,
    inbox: SQLiteInbox,
    *,
    clock: Callable[[], datetime] | None = None,
) -> FastAPI:
    """Compose only authenticated ingress and durable acceptance."""

    if type(settings) is not V2Settings:
        raise TypeError("settings must be an exact V2Settings")
    if type(inbox) is not SQLiteInbox:
        raise TypeError("inbox must be an exact SQLiteInbox")
    if inbox.path != settings.sqlite_path:
        raise ValueError("inbox path must match settings.sqlite_path")
    now = clock or (lambda: datetime.now(timezone.utc))
    app = FastAPI(title="Agente V2", version="0.8.0")

    @app.get("/healthz")
    async def healthz() -> dict[str, str]:
        return {"status": "alive", "role": "api"}

    @app.get("/readyz")
    async def readyz() -> dict[str, object]:
        return {
            "status": "ready",
            "real_effect_gates": settings.real_effect_gates,
        }

    @app.post("/webhook/manychat")
    async def manychat_webhook(request: Request) -> JSONResponse:
        provided = request.headers.get("X-V2-Webhook-Secret", "")
        if not _secret_matches(provided, settings.webhook_secret):
            return JSONResponse(status_code=401, content={"status": "unauthorized"})

        content_length = request.headers.get("content-length")
        if content_length is not None:
            try:
                declared_length = int(content_length)
            except ValueError:
                return JSONResponse(status_code=400, content={"status": "invalid"})
            if declared_length < 0:
                return JSONResponse(status_code=400, content={"status": "invalid"})
            if declared_length > settings.max_body_bytes:
                return JSONResponse(status_code=413, content={"status": "too_large"})
        body = await request.body()
        if len(body) > settings.max_body_bytes:
            return JSONResponse(status_code=413, content={"status": "too_large"})

        try:
            payload = _load_json_object(body)
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

    return app
