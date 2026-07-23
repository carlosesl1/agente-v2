"""Executable API role for the standalone V2 image."""

from __future__ import annotations

from collections.abc import Mapping
from contextlib import asynccontextmanager
import os

from fastapi import FastAPI

from v2_host.app import create_app
from v2_host.composition import V2Container, V2Role
from v2_host.settings import V2Settings


def build_api_app(settings: V2Settings) -> FastAPI:
    if type(settings) is not V2Settings:
        raise TypeError("settings must be exact V2Settings")
    container = V2Container.open(settings=settings, role=V2Role.API)
    try:
        app = create_app(settings, container.inbox)
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
