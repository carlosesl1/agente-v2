from pathlib import Path
import tempfile
import tomllib

from scripts.check_fasttrack_boundaries import check_tree


PHASE7_PACKAGES = (
    "reservation_domain",
    "reservation_lookup",
    "reservation_confirmation",
    "reservation_execution",
    "reservation_followup",
    "reservation_boundary",
)
FASTTRACK_PACKAGES = (
    "v2_contracts",
    "v2_application",
    "v2_adapters",
    "v2_host",
    "v2_ops",
)


def test_current_tree_obeys_fasttrack_boundaries() -> None:
    assert check_tree(Path(__file__).parents[1]) == ()


def test_project_registers_fasttrack_without_expanding_phase7_wheel() -> None:
    root = Path(__file__).parents[1]
    payload = tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8"))

    assert tuple(payload["tool"]["phase7-wheel"]["packages"]) == PHASE7_PACKAGES
    assert tuple(payload["tool"]["v2-fasttrack"]["packages"]) == FASTTRACK_PACKAGES
    assert set(payload["project"]["optional-dependencies"]) == {"runtime", "dev"}


def test_guard_rejects_legacy_import_and_cross_layer_import() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        for package in ("v2_contracts", "v2_application", "v2_adapters", "v2_host"):
            (root / package).mkdir()
            (root / package / "__init__.py").write_text("", encoding="utf-8")
        (root / "v2_adapters" / "bad.py").write_text(
            "from services.manychat import ManyChatClient\n"
            "from v2_application.turns import V2TurnService\n",
            encoding="utf-8",
        )
        errors = check_tree(root)
        assert any("legacy prefix services" in item for item in errors)
        assert any(
            "v2_adapters may not import v2_application" in item for item in errors
        )


def test_guard_rejects_dynamic_import_forbidden_literal_and_invalid_syntax() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        for package in ("v2_contracts", "v2_application", "v2_adapters", "v2_host"):
            (root / package).mkdir()
            (root / package / "__init__.py").write_text("", encoding="utf-8")
        (root / "v2_host" / "dynamic.py").write_text(
            "import importlib\n"
            "legacy = importlib.import_module('services.manychat')\n",
            encoding="utf-8",
        )
        (root / "v2_adapters" / "literal.py").write_text(
            "LEGACY = '/home/ubuntu/chapada-leads-hermes'\n",
            encoding="utf-8",
        )
        (root / "v2_application" / "broken.py").write_text(
            "def broken(:\n",
            encoding="utf-8",
        )

        errors = check_tree(root)

        assert any("dynamic import of legacy prefix services" in item for item in errors)
        assert any("forbidden literal chapada-leads-hermes" in item for item in errors)
        assert any("cannot be parsed" in item for item in errors)


def test_guard_rejects_sys_path_mutation() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        for package in ("v2_contracts", "v2_application", "v2_adapters", "v2_host"):
            (root / package).mkdir()
            (root / package / "__init__.py").write_text("", encoding="utf-8")
        (root / "v2_host" / "path_bypass.py").write_text(
            "import sys\n"
            "sys.path.append('/tmp/legacy')\n",
            encoding="utf-8",
        )

        errors = check_tree(root)

        assert any("may not mutate sys.path" in item for item in errors)


def test_contracts_reject_external_and_escaping_relative_imports() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        for package in ("v2_contracts", "v2_application", "v2_adapters", "v2_host"):
            (root / package).mkdir()
            (root / package / "__init__.py").write_text("", encoding="utf-8")
        (root / "v2_contracts" / "bad.py").write_text(
            "import httpx\n"
            "from ..v2_application import turns\n",
            encoding="utf-8",
        )

        errors = check_tree(root)

        assert any("v2_contracts may import only the Python stdlib" in item for item in errors)
        assert any("relative import escapes v2_contracts" in item for item in errors)


def test_ops_may_use_contracts_and_kernel_but_not_runtime_layers() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        for package in FASTTRACK_PACKAGES:
            (root / package).mkdir()
            (root / package / "__init__.py").write_text("", encoding="utf-8")
        (root / "v2_ops" / "allowed.py").write_text(
            "from v2_contracts.channel import InboundEvent\n"
            "from reservation_boundary.public_dispatch import PublicDispatchClaim\n",
            encoding="utf-8",
        )
        (root / "v2_ops" / "forbidden.py").write_text(
            "from v2_application.turn_executor import V2TurnExecutor\n"
            "from v2_adapters.provider_http import ManyChatHTTPTransport\n"
            "from v2_host.production import build_worker_set\n",
            encoding="utf-8",
        )

        errors = check_tree(root)

        assert not any("allowed.py" in item for item in errors)
        assert any("v2_ops may not import v2_application" in item for item in errors)
        assert any("v2_ops may not import v2_adapters" in item for item in errors)
        assert any("v2_ops may not import v2_host" in item for item in errors)


def test_runtime_layers_may_depend_on_ops_but_adapters_may_not() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        for package in FASTTRACK_PACKAGES:
            (root / package).mkdir()
            (root / package / "__init__.py").write_text("", encoding="utf-8")
        (root / "v2_application" / "trace.py").write_text(
            "from v2_ops.recording import NullOpsRecorder\n",
            encoding="utf-8",
        )
        (root / "v2_host" / "trace.py").write_text(
            "from v2_ops.store import SQLiteOpsTraceWriter\n",
            encoding="utf-8",
        )
        (root / "v2_adapters" / "bad_trace.py").write_text(
            "from v2_ops.recording import SQLiteOpsRecorder\n",
            encoding="utf-8",
        )

        errors = check_tree(root)

        assert not any("v2_application/trace.py" in item for item in errors)
        assert not any("v2_host/trace.py" in item for item in errors)
        assert any("v2_adapters may not import v2_ops" in item for item in errors)
