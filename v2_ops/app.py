from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import secrets
from datetime import datetime, timedelta, timezone
from enum import Enum
from pathlib import Path
from typing import AsyncIterator, Literal
from urllib.parse import parse_qs, urlsplit

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse, Response, StreamingResponse

from v2_ops.auth import LoginLimiter, SessionClaims, SessionCodec, verify_password
from v2_ops.contracts import ExecutionStatus
from v2_ops.dashboard import DashboardRange, DashboardSnapshot, build_dashboard_snapshot
from v2_ops.settings import OpsWebSettings
from v2_ops.store import OpsTraceStoreError, SQLiteOpsTraceReader

_SESSION_COOKIE = "v2_ops_session"
_CSRF_COOKIE = "v2_ops_csrf"
_ROOT = Path(__file__).resolve().parent
_STATIC = _ROOT / "static"
_TEMPLATES = _ROOT / "templates"


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _json_value(value: object) -> object:
    if isinstance(value, datetime):
        return value.isoformat().replace("+00:00", "Z")
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, tuple):
        return [_json_value(item) for item in value]
    if isinstance(value, dict):
        return {key: _json_value(item) for key, item in value.items()}
    return value


def _execution(value: object) -> dict[str, object]:
    return {
        "execution_id": value.execution_id,
        "lead_id": value.lead_id,
        "received_at": _json_value(value.received_at),
        "completed_at": _json_value(value.completed_at),
        "status": value.status,
        "stored_status": value.stored_status.value,
        "trace_completeness": value.trace_completeness.value,
        "current_node_id": value.current_node_id,
        "terminal_reason": value.terminal_reason,
    }


def _node(value: object) -> dict[str, object]:
    return {
        "node_id": value.node_id,
        "execution_id": value.execution_id,
        "node_type": value.node_type.value,
        "ordinal": value.ordinal,
        "attempt": value.attempt,
        "parent_node_id": value.parent_node_id,
        "status": value.status,
        "stored_status": value.stored_status.value,
        "started_at": _json_value(value.started_at),
        "completed_at": _json_value(value.completed_at),
        "input_summary": _json_value(value.input_summary),
        "output_summary": _json_value(value.output_summary),
        "has_full_input": value.has_full_input,
        "has_full_output": value.has_full_output,
        "error": _json_value(value.error),
        "technical_metadata": _json_value(value.technical_metadata),
    }


def _dashboard(value: DashboardSnapshot) -> dict[str, object]:
    return {
        "generated_at": _json_value(value.generated_at),
        "range": value.range_key.value,
        "metrics": {
            "executions": _json_value(value.metrics["executions"]),
            "distinct_leads": _json_value(value.metrics["distinct_leads"]),
            "in_progress": _json_value(value.metrics["in_progress"]),
            "completed": _json_value(value.metrics["completed"]),
            "failed": _json_value(value.metrics["failed"]),
            "manual_review": _json_value(value.metrics["manual_review"]),
            "technical_completion_rate": _json_value(
                value.metrics["technical_completion_rate"]
            ),
            "average_terminal_duration_ms": _json_value(
                value.metrics["average_terminal_duration_ms"]
            ),
        },
        "execution_series": [
            {"start_at": _json_value(item["start_at"]), "count": item["count"]}
            for item in value.execution_series
        ],
        "status_distribution": [
            {"status": _json_value(item["status"]), "count": item["count"]}
            for item in value.status_distribution
        ],
        "trace_distribution": [
            {
                "trace_completeness": _json_value(item["trace_completeness"]),
                "count": item["count"],
            }
            for item in value.trace_distribution
        ],
        "milestones": [
            {"milestone": _json_value(item["milestone"]), "count": item["count"]}
            for item in value.milestones
        ],
        "top_node_types": [
            {"node_type": _json_value(item["node_type"]), "count": item["count"]}
            for item in value.top_node_types
        ],
        "executions": [
            {
                "lead_id": _json_value(item["lead_id"]),
                "execution_id": _json_value(item["execution_id"]),
                "received_at": _json_value(item["received_at"]),
                "duration_ms": _json_value(item["duration_ms"]),
                "status": _json_value(item["status"]),
                "trace_completeness": _json_value(item["trace_completeness"]),
                "current_node_type": _json_value(item["current_node_type"]),
                "node_count": _json_value(item["node_count"]),
                "has_reservation": _json_value(item["has_reservation"]),
                "has_payment": _json_value(item["has_payment"]),
                "has_public_delivery": _json_value(item["has_public_delivery"]),
                "has_handoff": _json_value(item["has_handoff"]),
                "terminal_reason": _json_value(item["terminal_reason"]),
            }
            for item in value.executions
        ],
    }


def _etag_response(payload: object, request: Request) -> Response:
    body = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    etag = '"' + hashlib.sha256(body).hexdigest() + '"'
    if request.headers.get("if-none-match") == etag:
        return Response(status_code=304, headers={"ETag": etag})
    return Response(body, media_type="application/json", headers={"ETag": etag})


def _same_origin(request: Request) -> bool:
    expected = f"{request.url.scheme}://{request.url.netloc}"
    origin = request.headers.get("origin")
    if origin is not None and origin != "null":
        return hmac.compare_digest(origin.rstrip("/"), expected)
    referer = request.headers.get("referer")
    if referer is None:
        # Browsers and embedded webviews may omit both headers or send a null
        # origin. The POST still requires the unpredictable double-submit CSRF
        # cookie and form token; explicit foreign origins remain rejected.
        return True
    parsed = urlsplit(referer)
    return hmac.compare_digest(f"{parsed.scheme}://{parsed.netloc}", expected)


def _cookie_values(request: Request, name: str) -> tuple[str, ...]:
    values: list[str] = []
    for part in request.headers.get("cookie", "").split(";"):
        key, separator, value = part.strip().partition("=")
        if separator and key == name:
            values.append(value)
    return tuple(values)


async def _form(request: Request) -> dict[str, str]:
    length = request.headers.get("content-length")
    if length is not None and (not length.isdigit() or int(length) > 4096):
        raise ValueError("form too large")
    body = await request.body()
    if len(body) > 4096:
        raise ValueError("form too large")
    try:
        values = parse_qs(body.decode("utf-8"), keep_blank_values=True, max_num_fields=8)
    except (UnicodeError, ValueError) as exc:
        raise ValueError("invalid form") from exc
    if any(len(items) != 1 for items in values.values()):
        raise ValueError("invalid form")
    return {key: items[0] for key, items in values.items()}


def create_ops_app(
    settings: OpsWebSettings,
    *,
    reader: SQLiteOpsTraceReader | None = None,
) -> FastAPI:
    if type(settings) is not OpsWebSettings:
        raise TypeError("settings must be exact OpsWebSettings")
    trace_reader = reader or SQLiteOpsTraceReader(settings.trace_path, settings.trace_key)
    if type(trace_reader) is not SQLiteOpsTraceReader:
        raise TypeError("reader must be exact SQLiteOpsTraceReader")
    sessions = SessionCodec(settings.session_key, ttl=settings.session_ttl)
    limiter = LoginLimiter()
    dashboard_cache: dict[DashboardRange, tuple[datetime, dict[str, object]]] = {}
    dashboard_cache_lock = asyncio.Lock()
    app = FastAPI(
        title="V2 Ops Read Only",
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
    )

    @app.middleware("http")
    async def security_headers(request: Request, call_next: object) -> Response:
        response = await call_next(request)
        response.headers["Cache-Control"] = "no-store"
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; script-src 'self'; style-src 'self'; "
            "img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'; "
            "base-uri 'none'; form-action 'self'"
        )
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["Permissions-Policy"] = "camera=(), microphone=(), geolocation=()"
        return response

    def claims(request: Request) -> SessionClaims | None:
        token = request.cookies.get(_SESSION_COOKIE, "")
        return sessions.verify(token, now=_now())

    def require_api(request: Request) -> SessionClaims | Response:
        authenticated = claims(request)
        if authenticated is None:
            return JSONResponse(status_code=401, content={"status": "authentication_required"})
        return authenticated

    @app.get("/ops/healthz")
    async def health() -> JSONResponse:
        return JSONResponse({"status": "alive", "mode": "read_only"})

    @app.get("/ops")
    async def ops_root() -> RedirectResponse:
        return RedirectResponse("/ops/", status_code=307)

    @app.get("/ops/login")
    async def login_page(request: Request) -> Response:
        if claims(request) is not None:
            return RedirectResponse("/ops/", status_code=303)
        csrf = secrets.token_hex(32)
        html = (_TEMPLATES / "login.html").read_text(encoding="utf-8").replace("{{CSRF}}", csrf)
        response = HTMLResponse(html)
        response.set_cookie(
            _CSRF_COOKIE,
            csrf,
            secure=settings.secure_cookie,
            httponly=False,
            samesite="strict",
            path="/ops",
            max_age=int(settings.session_ttl.total_seconds()),
        )
        return response

    @app.post("/ops/login")
    async def login(request: Request) -> Response:
        if not _same_origin(request):
            return JSONResponse(status_code=403, content={"status": "forbidden"})
        try:
            form = await _form(request)
        except ValueError:
            return JSONResponse(status_code=400, content={"status": "invalid_request"})
        csrf = form.get("csrf", "")
        cookie_csrf_values = _cookie_values(request, _CSRF_COOKIE)
        if not csrf or not any(hmac.compare_digest(csrf, value) for value in cookie_csrf_values):
            return JSONResponse(status_code=403, content={"status": "forbidden"})
        client_key = request.client.host if request.client is not None else "unknown"
        instant = _now()
        if not limiter.allowed(client_key, now=instant):
            return JSONResponse(status_code=429, content={"status": "invalid_credentials"})
        username = form.get("username", "")
        password = form.get("password", "")
        valid_password = verify_password(password, settings.password_hash)
        valid_user = hmac.compare_digest(username, settings.username)
        if not (valid_user and valid_password):
            limiter.failure(client_key, now=instant)
            return JSONResponse(status_code=401, content={"status": "invalid_credentials"})
        limiter.success(client_key)
        session_csrf = secrets.token_hex(32)
        token = sessions.issue(username=settings.username, csrf=session_csrf, now=instant)
        response = RedirectResponse("/ops/", status_code=303)
        response.set_cookie(
            _SESSION_COOKIE,
            token,
            secure=settings.secure_cookie,
            httponly=True,
            samesite="strict",
            path="/ops",
            max_age=int(settings.session_ttl.total_seconds()),
        )
        response.set_cookie(
            _CSRF_COOKIE,
            session_csrf,
            secure=settings.secure_cookie,
            httponly=False,
            samesite="strict",
            path="/ops",
            max_age=int(settings.session_ttl.total_seconds()),
        )
        return response

    @app.post("/ops/logout")
    async def logout(request: Request) -> Response:
        authenticated = claims(request)
        if authenticated is None:
            return RedirectResponse("/ops/login", status_code=303)
        if not _same_origin(request):
            return JSONResponse(status_code=403, content={"status": "forbidden"})
        try:
            form = await _form(request)
        except ValueError:
            return JSONResponse(status_code=400, content={"status": "invalid_request"})
        if not hmac.compare_digest(form.get("csrf", ""), authenticated.csrf):
            return JSONResponse(status_code=403, content={"status": "forbidden"})
        response = RedirectResponse("/ops/login", status_code=303)
        response.delete_cookie(_SESSION_COOKIE, path="/ops")
        response.delete_cookie(_CSRF_COOKIE, path="/ops")
        return response

    @app.get("/ops/")
    async def index(request: Request) -> Response:
        authenticated = claims(request)
        if authenticated is None:
            return RedirectResponse("/ops/login", status_code=303)
        html = (_STATIC / "index.html").read_text(encoding="utf-8").replace("{{CSRF}}", authenticated.csrf)
        return HTMLResponse(html)

    @app.get("/ops/static/ops.css")
    async def css() -> Response:
        return FileResponse(_STATIC / "ops.css", media_type="text/css")

    @app.get("/ops/static/ops.js")
    async def js(request: Request) -> Response:
        if claims(request) is None:
            return JSONResponse(status_code=401, content={"status": "authentication_required"})
        return FileResponse(_STATIC / "ops.js", media_type="text/javascript")

    @app.get("/ops/api/executions")
    async def executions(
        request: Request,
        lead_id: str | None = None,
        status: str | None = None,
        limit: int = 50,
        cursor: str | None = None,
    ) -> Response:
        if type(require_api(request)) is not SessionClaims:
            return require_api(request)
        try:
            parsed_status: ExecutionStatus | str | None
            if status is None:
                parsed_status = None
            elif status == "running_stale":
                parsed_status = status
            else:
                parsed_status = ExecutionStatus(status)
            page = trace_reader.list_executions(
                lead_id=lead_id,
                status=parsed_status,
                limit=limit,
                cursor=cursor,
                stale_after=settings.stale_after,
            )
        except ValueError:
            return JSONResponse(status_code=422, content={"status": "invalid_query"})
        except OpsTraceStoreError:
            return JSONResponse(status_code=503, content={"status": "source_unavailable"})
        return _etag_response(
            {"executions": [_execution(item) for item in page.executions], "next_cursor": page.next_cursor},
            request,
        )

    @app.get("/ops/api/dashboard")
    async def dashboard(request: Request, range: str = "7d") -> Response:
        authenticated = require_api(request)
        if type(authenticated) is not SessionClaims:
            return authenticated
        try:
            range_key = DashboardRange.parse(range)
        except ValueError:
            return JSONResponse(status_code=422, content={"status": "invalid_query"})
        async with dashboard_cache_lock:
            generated_at = _now()
            cached = dashboard_cache.get(range_key)
            if cached is not None and generated_at - cached[0] < timedelta(seconds=2):
                payload = cached[1]
            else:
                try:
                    records = trace_reader.list_dashboard_records(
                        start_at=generated_at - range_key.duration,
                        end_at=generated_at,
                        now=generated_at,
                        stale_after=settings.stale_after,
                    )
                    snapshot = build_dashboard_snapshot(
                        records,
                        range_key=range_key,
                        generated_at=generated_at,
                    )
                    payload = _dashboard(snapshot)
                except (OpsTraceStoreError, ValueError, TypeError):
                    return JSONResponse(
                        status_code=503, content={"status": "source_unavailable"}
                    )
                dashboard_cache[range_key] = (generated_at, payload)
        return _etag_response(payload, request)

    @app.get("/ops/api/harness")
    async def harness(request: Request) -> Response:
        if type(require_api(request)) is not SessionClaims:
            return require_api(request)
        try:
            page = trace_reader.list_executions(
                lead_id="synthetic-harness:lead-001",
                limit=1,
                stale_after=settings.stale_after,
            )
        except OpsTraceStoreError:
            return JSONResponse(status_code=503, content={"status": "source_unavailable"})
        execution_id = page.executions[0].execution_id if page.executions else None
        return _etag_response(
            {"available": execution_id is not None, "execution_id": execution_id},
            request,
        )

    @app.get("/ops/api/release")
    async def release(request: Request) -> Response:
        if type(require_api(request)) is not SessionClaims:
            return require_api(request)
        return _etag_response(
            {
                "release_sha": settings.release_sha,
                "image_digest": settings.image_digest,
                "config_fingerprint": settings.config_fingerprint,
            },
            request,
        )

    @app.get("/ops/api/executions/{execution_id}")
    async def execution_detail(execution_id: str, request: Request) -> Response:
        if type(require_api(request)) is not SessionClaims:
            return require_api(request)
        try:
            value = trace_reader.get_execution(execution_id, stale_after=settings.stale_after)
        except ValueError:
            return JSONResponse(status_code=422, content={"status": "invalid_execution_id"})
        except OpsTraceStoreError:
            return JSONResponse(status_code=503, content={"status": "source_unavailable"})
        if value is None:
            return JSONResponse(status_code=404, content={"status": "not_found"})
        return _etag_response({"execution": _execution(value)}, request)

    @app.get("/ops/api/executions/{execution_id}/nodes")
    async def nodes(execution_id: str, request: Request, limit: int = 256) -> Response:
        if type(require_api(request)) is not SessionClaims:
            return require_api(request)
        try:
            values = trace_reader.list_nodes(
                execution_id, limit=limit, stale_after=settings.stale_after
            )
        except ValueError:
            return JSONResponse(status_code=422, content={"status": "invalid_query"})
        except OpsTraceStoreError:
            return JSONResponse(status_code=503, content={"status": "source_unavailable"})
        return _etag_response({"nodes": [_node(item) for item in values]}, request)

    @app.get("/ops/api/executions/{execution_id}/nodes/{node_id}/full")
    async def full(
        execution_id: str,
        node_id: str,
        request: Request,
        side: Literal["input", "output"],
    ) -> Response:
        if type(require_api(request)) is not SessionClaims:
            return require_api(request)
        try:
            value = trace_reader.read_full(execution_id, node_id, side=side)
        except ValueError:
            return JSONResponse(status_code=422, content={"status": "invalid_query"})
        except OpsTraceStoreError:
            return JSONResponse(status_code=503, content={"status": "source_unavailable"})
        return JSONResponse(
            {"status": "not_recorded", "value": None}
            if value is None
            else {"status": "recorded", "value": _json_value(value.value)}
        )

    @app.get("/ops/api/events")
    async def events(request: Request) -> Response:
        if type(require_api(request)) is not SessionClaims:
            return require_api(request)

        async def stream() -> AsyncIterator[str]:
            event_id = int(request.headers.get("last-event-id", "0")) if request.headers.get("last-event-id", "0").isdigit() else 0
            yield f"id: {event_id}\nevent: ready\ndata: {{\"status\":\"connected\"}}\n\n"
            fingerprint = ""
            while not await request.is_disconnected():
                try:
                    page = trace_reader.list_executions(limit=100, stale_after=settings.stale_after)
                    current = hashlib.sha256(
                        json.dumps(
                            [_execution(item) for item in page.executions],
                            sort_keys=True,
                            separators=(",", ":"),
                        ).encode("utf-8")
                    ).hexdigest()
                    if fingerprint and current != fingerprint:
                        event_id += 1
                        yield f"id: {event_id}\nevent: change\ndata: {{\"status\":\"changed\"}}\n\n"
                    else:
                        yield ": keepalive\n\n"
                    fingerprint = current
                except OpsTraceStoreError:
                    yield "event: degraded\ndata: {\"status\":\"source_unavailable\"}\n\n"
                await asyncio.sleep(2)

        return StreamingResponse(stream(), media_type="text/event-stream")

    return app
