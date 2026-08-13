from __future__ import annotations

import ast
from pathlib import Path

from v2_ops.app import create_ops_app
from v2_ops.auth import hash_password
from v2_ops.settings import OpsWebSettings
from v2_ops.store import SQLiteOpsTraceReader, SQLiteOpsTraceWriter

ROOT = Path(__file__).resolve().parents[1]


def test_ops_web_package_has_no_business_effect_imports() -> None:
    forbidden = (
        "v2_adapters",
        "v2_application.reservations",
        "v2_application.payments",
        "v2_application.public_delivery",
        "v2_application.workers",
        "reservation_execution.worker",
    )
    for relative in ("v2_ops/app.py", "v2_ops/auth.py", "v2_ops/main.py", "v2_ops/settings.py"):
        tree = ast.parse((ROOT / relative).read_text(encoding="utf-8"))
        modules = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                modules.extend(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                modules.append(node.module)
        assert not any(module.startswith(forbidden) for module in modules), (relative, modules)


def test_ops_route_matrix_and_smoke_never_expose_business_mutation(tmp_path) -> None:
    settings = OpsWebSettings(
        username="ops-admin",
        password_hash=hash_password("synthetic-password", salt=b"0" * 16),
        session_key=b"s" * 32,
        trace_path=(tmp_path / "ops.sqlite3").resolve(),
        trace_key=b"t" * 32,
    )
    writer = SQLiteOpsTraceWriter(settings.trace_path, settings.trace_key)
    writer.close()
    reader = SQLiteOpsTraceReader(settings.trace_path, settings.trace_key)
    app = create_ops_app(settings, reader=reader)
    paths = {(route.path, tuple(sorted(route.methods or ()))) for route in app.routes}
    business_tokens = {"reserve", "book", "payment", "send", "replay", "retry", "handoff"}
    assert not any(
        token in path.casefold()
        for path, _methods in paths
        for token in business_tokens
    )
    approved_posts = {"/ops/login", "/ops/logout"}
    assert {path for path, methods in paths if "POST" in methods} == approved_posts
    assert not any(method in methods for _path, methods in paths for method in ("PUT", "PATCH", "DELETE"))
    reader.close()


def test_web_app_imports_only_the_dedicated_ops_reader() -> None:
    source = (ROOT / "v2_ops/app.py").read_text(encoding="utf-8")
    assert "SQLiteOpsTraceReader" in source
    assert "SQLiteInbox" not in source
    assert "SQLiteBoundaryStore" not in source
    assert "SQLiteUnitOfWork" not in source
