"""Atomic ManyChat canary relay: one authenticated ingress, one selected runtime."""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Mapping
from datetime import datetime, timezone
import hashlib
import hmac
import json
import os
from pathlib import Path
import httpx
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, Response

from deploy.verify_runtime_identity import (
    RuntimeIdentityError,
    verify_runtime_identity_file,
)

Forward = Callable[[str, bytes, dict[str, str]], Awaitable[tuple[int, bytes, str]]]
ReadyProbe = Callable[[str], Awaitable[bool]]
IdentityProbe = Callable[[], Awaitable[bool]]


def _first_text(*values: object) -> str:
    for value in values:
        if isinstance(value, str) and value.strip():
            return value.strip()
        if type(value) is int and value >= 0:
            return str(value)
    return ""


def _mapping(value: object) -> Mapping[str, object]:
    return value if isinstance(value, Mapping) else {}


def _identity(payload: Mapping[str, object]) -> str:
    subscriber = _mapping(payload.get("subscriber"))
    contact = _mapping(payload.get("contact"))
    candidates = []
    for value in (
        payload.get("subscriber_id"),
        payload.get("subscriberId"),
        payload.get("id"),
        subscriber.get("id"),
        payload.get("contact_id"),
        payload.get("contactId"),
        contact.get("id"),
    ):
        candidate = _first_text(value)
        if candidate:
            if not candidate.isdecimal():
                raise ValueError("subscriber identity must be decimal")
            candidates.append(candidate)
    if not candidates or len(set(candidates)) != 1:
        raise ValueError("subscriber identity is missing or conflicting")
    return candidates[0]


def _strict_object(body: bytes) -> dict[str, object]:
    def unique(pairs: list[tuple[str, object]]) -> dict[str, object]:
        result: dict[str, object] = {}
        for key, value in pairs:
            if key in result:
                raise ValueError("duplicate JSON key")
            result[key] = value
        return result

    value = json.loads(
        body.decode("utf-8", errors="strict"),
        object_pairs_hook=unique,
        parse_constant=lambda token: (_ for _ in ()).throw(
            ValueError(f"non-finite number: {token}")
        ),
    )
    if type(value) is not dict:
        raise ValueError("request body must be a JSON object")
    return value


def _parse_time(value: str) -> datetime:
    normalized = value[:-1] + "+00:00" if value.endswith("Z") else value
    parsed = datetime.fromisoformat(normalized)
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError("message time must include timezone")
    return parsed.astimezone(timezone.utc)


def normalize_v2_payload(
    payload: Mapping[str, object],
    *,
    received_at: datetime | None = None,
) -> dict[str, object]:
    if not isinstance(payload, Mapping):
        raise ValueError("payload must be an object")
    subscriber_id = _identity(payload)
    message = _mapping(payload.get("message"))
    raw_message = payload.get("message")
    text = _first_text(
        payload.get("text"),
        raw_message if isinstance(raw_message, str) else None,
        payload.get("message_text"),
        payload.get("last_text_input"),
        payload.get("last_input_text"),
        message.get("text"),
        message.get("content"),
    )
    media = _mapping(payload.get("media"))
    media_url = _first_text(
        payload.get("media_url"),
        message.get("media_url"),
        media.get("url"),
    )
    media_type = _first_text(
        payload.get("media_type"),
        message.get("media_type"),
        media.get("type"),
    )
    if not text and not media_url:
        raise ValueError("message text or media is required")
    if media_type and not media_url:
        raise ValueError("media type requires media URL")

    source_event_id = _first_text(
        payload.get("event_id"),
        payload.get("message_id"),
        payload.get("messageId"),
        message.get("id"),
        message.get("message_id"),
    )
    source_time = _first_text(
        payload.get("time-message"),
        payload.get("time_message"),
        payload.get("message_time"),
        payload.get("message_timestamp"),
        payload.get("timestamp"),
        payload.get("created_at"),
        message.get("timestamp"),
        message.get("created_at"),
    )
    if source_time:
        occurred = _parse_time(source_time)
    else:
        occurred = received_at or datetime.now(timezone.utc)
        if occurred.tzinfo is None or occurred.utcoffset() is None:
            raise ValueError("received_at must include timezone")
        occurred = occurred.astimezone(timezone.utc)
    if not source_event_id and not source_time:
        raise ValueError("stable message identity or timestamp is required")

    material = json.dumps(
        {
            "source_event_id": source_event_id,
            "subscriber_id": subscriber_id,
            "source_time": source_time,
            "text": text,
            "media_url": media_url or None,
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    event_id = "manychat-event:" + hashlib.sha256(
        b"maya-v2-manychat-event-v1\0" + material
    ).hexdigest()[:32]
    conversation_id = _first_text(
        payload.get("conversation_id"),
        payload.get("conversationId"),
        message.get("conversation_id"),
        subscriber_id,
    )
    result: dict[str, object] = {
        "event_id": event_id,
        "subscriber_id": subscriber_id,
        "contact_id": subscriber_id,
        "conversation_id": conversation_id,
        "text": text,
        "occurred_at": occurred.isoformat(),
    }
    if media_url:
        result["media_url"] = media_url
    if media_type:
        result["media_type"] = media_type
    return result


async def _http_forward(
    target: str,
    body: bytes,
    headers: dict[str, str],
) -> tuple[int, bytes, str]:
    timeout = httpx.Timeout(15.0, connect=3.0)
    async with httpx.AsyncClient(timeout=timeout, follow_redirects=False) as client:
        response = await client.post(target, content=body, headers=headers)
    return (
        response.status_code,
        response.content,
        response.headers.get("content-type", "application/json"),
    )


async def _http_ready(target: str) -> bool:
    timeout = httpx.Timeout(3.0, connect=1.0)
    async with httpx.AsyncClient(timeout=timeout, follow_redirects=False) as client:
        response = await client.get(target)
    if response.status_code != 200:
        return False
    try:
        payload = response.json()
    except (ValueError, json.JSONDecodeError):
        return False
    return type(payload) is dict and payload.get("status") == "ready"


def build_app(
    *,
    shared_secret: str,
    allowed_subscriber_id: str,
    v2_url: str,
    v2_ready_url: str,
    legacy_url: str,
    cutover_deadline: datetime | None,
    forward: Forward = _http_forward,
    ready_probe: ReadyProbe = _http_ready,
    identity_probe: IdentityProbe,
    clock: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
    max_body_bytes: int = 65_536,
) -> FastAPI:
    if not shared_secret or "\x00" in shared_secret:
        raise ValueError("shared webhook secret is required")
    if not allowed_subscriber_id.isdecimal():
        raise ValueError("allowed subscriber must be decimal")
    if (
        not v2_url.startswith("http://")
        or not v2_ready_url.startswith("http://")
        or not legacy_url.startswith("http://")
    ):
        raise ValueError("relay upstreams must be private HTTP URLs")
    if cutover_deadline is not None and (
        type(cutover_deadline) is not datetime
        or cutover_deadline.tzinfo is None
        or cutover_deadline.utcoffset() != timezone.utc.utcoffset(cutover_deadline)
    ):
        raise ValueError("cutover deadline must be an exact UTC datetime")
    if not callable(ready_probe) or not callable(identity_probe) or not callable(clock):
        raise TypeError("relay probe and clock must be callable")
    if type(max_body_bytes) is not int or max_body_bytes < 1:
        raise ValueError("max body bytes must be positive")

    app = FastAPI(title="Maya V2 Canary Relay", docs_url=None, redoc_url=None)

    async def runtime_identity_ready() -> bool:
        try:
            return (await identity_probe()) is True
        except Exception:
            return False

    @app.get("/healthz")
    async def healthz() -> dict[str, str]:
        return {
            "status": "alive",
            "canary_route": (
                "eligible"
                if cutover_deadline is None or clock() < cutover_deadline
                else "legacy_only"
            ),
        }

    @app.get("/readyz")
    async def readyz() -> Response:
        if not await runtime_identity_ready():
            return JSONResponse(status_code=503, content={"status": "unready"})
        return JSONResponse(status_code=200, content={"status": "ready"})

    @app.post("/webhook/manychat")
    async def manychat(request: Request) -> Response:
        provided = request.headers.get("X-Hermes-Webhook-Secret", "") or request.headers.get(
            "X-V2-Webhook-Secret", ""
        )
        if not hmac.compare_digest(provided.encode(), shared_secret.encode()):
            return JSONResponse(status_code=401, content={"status": "unauthorized"})
        declared = request.headers.get("content-length")
        if declared is not None:
            try:
                if int(declared) < 0:
                    raise ValueError
            except ValueError:
                return JSONResponse(status_code=400, content={"status": "invalid"})
            if int(declared) > max_body_bytes:
                return JSONResponse(status_code=413, content={"status": "too_large"})
        body = await request.body()
        if len(body) > max_body_bytes:
            return JSONResponse(status_code=413, content={"status": "too_large"})
        try:
            payload = _strict_object(body)
            subscriber_id = _identity(payload)
        except (UnicodeError, json.JSONDecodeError, ValueError):
            return JSONResponse(status_code=400, content={"status": "invalid"})

        if not await runtime_identity_ready():
            return JSONResponse(
                status_code=503,
                content={"status": "runtime_identity_rejected"},
            )

        is_canary_target = subscriber_id == allowed_subscriber_id
        select_v2 = is_canary_target
        if (
            is_canary_target
            and cutover_deadline is not None
            and clock() >= cutover_deadline
        ):
            return JSONResponse(status_code=503, content={"status": "canary_closed"})
        if select_v2:
            try:
                select_v2 = await ready_probe(v2_ready_url)
            except (httpx.HTTPError, TimeoutError):
                select_v2 = False
            if not select_v2:
                return JSONResponse(
                    status_code=503,
                    content={"status": "canary_unavailable"},
                )

        if select_v2:
            try:
                canonical = normalize_v2_payload(payload)
            except ValueError:
                return JSONResponse(status_code=422, content={"status": "invalid"})
            target = v2_url
            outbound_body = json.dumps(
                canonical,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
                allow_nan=False,
            ).encode("utf-8")
            outbound_headers = {
                "X-V2-Webhook-Secret": shared_secret,
                "Content-Type": "application/json",
            }
        else:
            target = legacy_url
            outbound_body = body
            outbound_headers = {
                "X-Hermes-Webhook-Secret": shared_secret,
                "Content-Type": "application/json",
            }
        if not await runtime_identity_ready():
            return JSONResponse(
                status_code=503,
                content={"status": "runtime_identity_rejected"},
            )
        if (
            select_v2
            and cutover_deadline is not None
            and clock() >= cutover_deadline
        ):
            return JSONResponse(status_code=503, content={"status": "canary_closed"})
        try:
            status, response_body, content_type = await forward(
                target, outbound_body, outbound_headers
            )
        except (httpx.HTTPError, TimeoutError):
            return JSONResponse(status_code=502, content={"status": "upstream_unavailable"})
        if target == v2_url and 200 <= status < 300:
            status = 200
        return Response(content=response_body, status_code=status, media_type=content_type)

    return app


def create_app_from_env() -> FastAPI:
    max_body = int(os.environ.get("CANARY_MAX_BODY_BYTES", "65536"))
    deadline_text = os.environ.get("CANARY_CUTOVER_DEADLINE", "").strip()
    deadline = _parse_time(deadline_text) if deadline_text else None

    async def identity_probe() -> bool:
        try:
            verify_runtime_identity_file(
                expected_git_sha=os.environ["CANARY_EXPECTED_GIT_SHA"],
                expected_image_ref=os.environ["CANARY_EXPECTED_IMAGE_REF"],
                expected_image_digest=os.environ["CANARY_EXPECTED_IMAGE_DIGEST"],
                metadata_path=Path(
                    os.environ["CANARY_RUNTIME_IDENTITY_METADATA_PATH"]
                ),
            )
        except (OSError, RuntimeIdentityError):
            return False
        return True

    return build_app(
        shared_secret=os.environ["CANARY_WEBHOOK_SECRET"],
        allowed_subscriber_id=os.environ["CANARY_SUBSCRIBER_ID"],
        v2_url=os.environ["CANARY_V2_URL"],
        v2_ready_url=os.environ["CANARY_V2_READY_URL"],
        legacy_url=os.environ["CANARY_LEGACY_URL"],
        cutover_deadline=deadline,
        identity_probe=identity_probe,
        max_body_bytes=max_body,
    )


__all__ = ["build_app", "create_app_from_env", "normalize_v2_payload"]
