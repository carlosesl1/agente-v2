"""Fail-closed architectural boundary scanner for the V2 fast-track."""

from __future__ import annotations

import ast
from pathlib import Path
import sys
from typing import Final

NEW_PACKAGES: Final = (
    "v2_contracts",
    "v2_application",
    "v2_adapters",
    "v2_host",
)
KERNEL_PACKAGES: Final = frozenset(
    {
        "reservation_domain",
        "reservation_lookup",
        "reservation_confirmation",
        "reservation_execution",
        "reservation_followup",
        "reservation_boundary",
    }
)
ALLOWED_INTERNAL: Final = {
    "v2_contracts": frozenset(),
    "v2_application": frozenset({"v2_contracts", *KERNEL_PACKAGES}),
    "v2_adapters": frozenset({"v2_contracts"}),
    "v2_host": frozenset(
        {"v2_contracts", "v2_application", "v2_adapters", *KERNEL_PACKAGES}
    ),
}
LEGACY_PREFIXES: Final = frozenset(
    {"app", "cli", "chapada_leads", "config", "domain", "services", "tools"}
)
FORBIDDEN_LITERALS: Final = (
    "/home/ubuntu/chapada-leads-hermes",
    "chapada-leads-hermes",
    "PYTHONPATH",
)
_STDLIB_MODULES: Final = frozenset({*sys.stdlib_module_names, "__future__"})
_INTERNAL_PREFIXES: Final = frozenset({*NEW_PACKAGES, *KERNEL_PACKAGES})


def _module_prefix(module: str) -> str:
    return module.split(".", 1)[0]


def _location(path: Path, line: int) -> str:
    return f"{path.as_posix()}:{line}"


def _check_import(
    *,
    owner: str,
    module: str,
    path: Path,
    line: int,
    errors: list[str],
    dynamic: bool = False,
) -> None:
    prefix = _module_prefix(module)
    kind = "dynamic import of" if dynamic else "imports"
    if prefix in LEGACY_PREFIXES:
        errors.append(
            f"{_location(path, line)}: {owner} {kind} legacy prefix {prefix}"
        )
        return
    if prefix in _INTERNAL_PREFIXES:
        if prefix != owner and prefix not in ALLOWED_INTERNAL[owner]:
            errors.append(
                f"{_location(path, line)}: {owner} may not import {prefix}"
            )
        return
    if owner == "v2_contracts" and prefix not in _STDLIB_MODULES:
        errors.append(
            f"{_location(path, line)}: v2_contracts may import only the Python "
            f"stdlib, not {prefix}"
        )


def _dynamic_aliases(
    tree: ast.AST,
) -> tuple[frozenset[str], frozenset[str], frozenset[str]]:
    importlib_aliases = {"importlib"}
    function_aliases = {"__import__"}
    sys_aliases = {"sys"}
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name == "importlib":
                    importlib_aliases.add(alias.asname or alias.name)
                elif alias.name == "sys":
                    sys_aliases.add(alias.asname or alias.name)
        elif isinstance(node, ast.ImportFrom) and node.module == "importlib":
            for alias in node.names:
                if alias.name == "import_module":
                    function_aliases.add(alias.asname or alias.name)
    return (
        frozenset(importlib_aliases),
        frozenset(function_aliases),
        frozenset(sys_aliases),
    )


def _dynamic_target(
    node: ast.Call,
    *,
    importlib_aliases: frozenset[str],
    function_aliases: frozenset[str],
) -> tuple[bool, str | None]:
    function = node.func
    is_dynamic = False
    if isinstance(function, ast.Name) and function.id in function_aliases:
        is_dynamic = True
    elif (
        isinstance(function, ast.Attribute)
        and function.attr == "import_module"
        and isinstance(function.value, ast.Name)
        and function.value.id in importlib_aliases
    ):
        is_dynamic = True
    if not is_dynamic:
        return False, None
    if not node.args:
        return True, None
    target = node.args[0]
    if not isinstance(target, ast.Constant) or type(target.value) is not str:
        return True, None
    return True, target.value


def _is_sys_path(node: ast.AST, sys_aliases: frozenset[str]) -> bool:
    return (
        isinstance(node, ast.Attribute)
        and node.attr == "path"
        and isinstance(node.value, ast.Name)
        and node.value.id in sys_aliases
    )


def _mutates_sys_path(node: ast.AST, sys_aliases: frozenset[str]) -> bool:
    if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
        return _is_sys_path(node.func.value, sys_aliases) and node.func.attr in {
            "append",
            "clear",
            "extend",
            "insert",
            "pop",
            "remove",
            "reverse",
            "sort",
            "__delitem__",
            "__setitem__",
        }
    if isinstance(node, (ast.Assign, ast.Delete)):
        targets = node.targets
    elif isinstance(node, (ast.AnnAssign, ast.AugAssign)):
        targets = (node.target,)
    else:
        return False
    return any(
        _is_sys_path(target, sys_aliases)
        or (isinstance(target, ast.Subscript) and _is_sys_path(target.value, sys_aliases))
        for target in targets
    )


def _check_file(owner: str, path: Path, errors: list[str]) -> None:
    try:
        source = path.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as exc:
        errors.append(f"{path.as_posix()}: cannot be read as UTF-8: {type(exc).__name__}")
        return
    try:
        tree = ast.parse(source, filename=str(path))
    except SyntaxError as exc:
        errors.append(
            f"{_location(path, exc.lineno or 1)}: cannot be parsed: {exc.msg}"
        )
        return

    importlib_aliases, function_aliases, sys_aliases = _dynamic_aliases(tree)
    for node in ast.walk(tree):
        if _mutates_sys_path(node, sys_aliases):
            errors.append(
                f"{_location(path, node.lineno)}: {owner} may not mutate sys.path"
            )
        if isinstance(node, ast.Import):
            for alias in node.names:
                _check_import(
                    owner=owner,
                    module=alias.name,
                    path=path,
                    line=node.lineno,
                    errors=errors,
                )
        elif isinstance(node, ast.ImportFrom):
            if node.level > 1:
                errors.append(
                    f"{_location(path, node.lineno)}: relative import escapes {owner}"
                )
            elif node.level == 0 and node.module is not None:
                _check_import(
                    owner=owner,
                    module=node.module,
                    path=path,
                    line=node.lineno,
                    errors=errors,
                )
        elif isinstance(node, ast.Call):
            is_dynamic, target = _dynamic_target(
                node,
                importlib_aliases=importlib_aliases,
                function_aliases=function_aliases,
            )
            if is_dynamic and target is None:
                errors.append(
                    f"{_location(path, node.lineno)}: dynamic import target must be "
                    "a literal module name"
                )
            elif is_dynamic:
                _check_import(
                    owner=owner,
                    module=target,
                    path=path,
                    line=node.lineno,
                    errors=errors,
                    dynamic=True,
                )
        elif isinstance(node, ast.Constant) and type(node.value) is str:
            for literal in FORBIDDEN_LITERALS:
                if literal in node.value:
                    errors.append(
                        f"{_location(path, node.lineno)}: forbidden literal {literal}"
                    )


def check_tree(root: Path) -> tuple[str, ...]:
    """Return deterministic boundary violations for the four V2 packages."""

    if not isinstance(root, Path):
        raise TypeError("root must be a pathlib.Path")
    errors: list[str] = []
    for owner in NEW_PACKAGES:
        package = root / owner
        if not package.is_dir():
            errors.append(f"{package.as_posix()}: required package is missing")
            continue
        for path in sorted(package.rglob("*.py")):
            _check_file(owner, path, errors)
    return tuple(sorted(set(errors)))


def main() -> int:
    root = Path(__file__).resolve().parents[1]
    errors = check_tree(root)
    if errors:
        for error in errors:
            print(error)
        return 1
    print("fasttrack-boundaries: OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
