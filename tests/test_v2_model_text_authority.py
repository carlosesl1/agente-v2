"""Global AST gate for model-owned Maya text and explicit public authorship."""

from __future__ import annotations

import ast
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PRODUCTION_ROOTS = (
    ROOT / "v2_application",
    ROOT / "v2_adapters",
    ROOT / "v2_contracts",
    ROOT / "reservation_boundary",
)
LEGACY_PUBLIC_COPY_HELPERS = {
    "_render_positive_payload",
    "execution_in_progress_reply",
    "grounded_positive_reply",
}


@dataclass(frozen=True, slots=True)
class _Violation:
    code: str
    path: str
    line: int
    detail: str


def _call_name(node: ast.Call) -> str:
    if isinstance(node.func, ast.Name):
        return node.func.id
    if isinstance(node.func, ast.Attribute):
        return node.func.attr
    return ""


def _attribute(node: ast.AST, owner: str, attribute: str) -> bool:
    return (
        isinstance(node, ast.Attribute)
        and isinstance(node.value, ast.Name)
        and node.value.id == owner
        and node.attr == attribute
    )


def _literal_text_container(node: ast.AST) -> bool:
    return isinstance(node, (ast.Tuple, ast.List, ast.Set)) and any(
        isinstance(item, ast.Constant) and isinstance(item.value, str)
        for item in node.elts
    )


class _AuthorityScanner(ast.NodeVisitor):
    def __init__(self, path: str) -> None:
        self.path = path
        self.owners: list[str] = []
        self.violations: list[_Violation] = []

    def _record(self, code: str, node: ast.AST, detail: str) -> None:
        self.violations.append(
            _Violation(code, self.path, getattr(node, "lineno", 0), detail)
        )

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        self.owners.append(node.name)
        self.generic_visit(node)
        self.owners.pop()

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        if node.name in LEGACY_PUBLIC_COPY_HELPERS:
            self._record(
                "legacy-public-copy-helper",
                node,
                "legacy controller-authored public copy helper must not exist",
            )
        self.owners.append(node.name)
        self.generic_visit(node)
        self.owners.pop()

    visit_AsyncFunctionDef = visit_FunctionDef

    def visit_Call(self, node: ast.Call) -> None:
        name = _call_name(node)
        keywords = {item.arg: item.value for item in node.keywords if item.arg}
        owner = self.owners[-1] if self.owners else "<module>"

        if name == "replace" and "reply_chunks" in keywords:
            self._record(
                "replace-reply-chunks",
                node,
                "controller cannot replace model reply_chunks",
            )

        if name == "ModelProposal" and "reply_chunks" in keywords:
            value = keywords["reply_chunks"]
            if any(
                isinstance(item, ast.Constant) and isinstance(item.value, str)
                for item in ast.walk(value)
            ):
                self._record(
                    "literal-model-proposal",
                    node,
                    "production ModelProposal cannot contain parent-authored prose",
                )

        if name == "ConversationReply":
            allowed = False
            if (
                self.path == "v2_application/conversation.py"
                and owner == "_model_owned_reply"
                and len(node.args) == 3
            ):
                allowed = _attribute(node.args[1], "proposal", "reply_chunks") and _attribute(
                    node.args[2], "PublicMessageAuthor", "MAYA"
                )
            elif (
                self.path == "v2_application/conversation.py"
                and owner == "_authenticated_system_reply"
                and len(node.args) == 3
            ):
                allowed = (
                    isinstance(node.args[1], ast.Name)
                    and node.args[1].id == "chunks"
                    and _attribute(
                        node.args[2],
                        "PublicMessageAuthor",
                        "AUTHENTICATED_SYSTEM",
                    )
                )
            if not allowed:
                self._record(
                    "conversation-reply-owner",
                    node,
                    "ConversationReply must use one named ownership helper",
                )

        if name == "PublicReply":
            author = keywords.get("author")
            allowed = (
                self.path == "v2_application/completion_projector.py"
                and author is not None
                and _attribute(
                    author,
                    "PublicMessageAuthor",
                    "AUTHENTICATED_SYSTEM",
                )
            )
            if not allowed:
                self._record(
                    "public-reply-author",
                    node,
                    "production PublicReply requires an allowlisted explicit author",
                )

        if name == "PublicReplyChunk":
            author = node.args[4] if len(node.args) >= 5 else keywords.get("author")
            allowed_author = author is not None and (
                _attribute(author, "PublicMessageAuthor", "MAYA")
                or _attribute(
                    author,
                    "PublicMessageAuthor",
                    "AUTHENTICATED_SYSTEM",
                )
            )
            if self.path != "v2_application/turn_executor.py" or not allowed_author:
                self._record(
                    "public-chunk-author",
                    node,
                    "PublicReplyChunk requires explicit author in the turn executor",
                )

        self.generic_visit(node)

    def visit_BoolOp(self, node: ast.BoolOp) -> None:
        if isinstance(node.op, ast.Or) and any(
            _literal_text_container(item) for item in node.values
        ):
            self._record(
                "literal-text-fallback",
                node,
                "controller cannot fall back to a literal public text container",
            )
        self.generic_visit(node)

    def visit_Assign(self, node: ast.Assign) -> None:
        mutates_reply_attribute = any(
            isinstance(target, ast.Attribute) and target.attr == "reply_chunks"
            for target in node.targets
        )
        canonicalizes_local_reply = any(
            isinstance(target, ast.Name) and target.id == "reply_chunks"
            for target in node.targets
        ) and any(
            isinstance(item, ast.Call)
            and _call_name(item) in {"casefold", "normalize", "replace", "strip"}
            for item in ast.walk(node.value)
        )
        if self.owners and (mutates_reply_attribute or canonicalizes_local_reply):
            self._record(
                "reply-chunks-assignment",
                node,
                "decoded reply_chunks cannot be reassigned or canonicalized",
            )
        self.generic_visit(node)


def _scan(path: str, source: str) -> tuple[_Violation, ...]:
    scanner = _AuthorityScanner(path)
    scanner.visit(ast.parse(source, filename=path))
    return tuple(scanner.violations)


def _production_violations() -> tuple[_Violation, ...]:
    violations: list[_Violation] = []
    for directory in PRODUCTION_ROOTS:
        for path in sorted(directory.rglob("*.py")):
            violations.extend(_scan(str(path.relative_to(ROOT)), path.read_text()))
    return tuple(violations)


def test_authority_scanner_rejects_representative_controller_copy() -> None:
    source = """
def bad(value):
    replace(value, reply_chunks=("parent rewrite",))
    ModelProposal(reply_chunks=("deterministic fallback",))
    ConversationReply("inform", ("controller prose",), PublicMessageAuthor.MAYA)
    PublicReply(chunks=("unowned",))
    PublicReplyChunk("turn", 0, "unowned", "a" * 64)
    value.reply_chunks = ("canonicalized",)
    return value.reply_chunks or ("fallback",)

def execution_in_progress_reply(locale):
    return ("controller status copy",)
"""
    codes = {item.code for item in _scan("v2_application/bad.py", source)}

    assert codes == {
        "conversation-reply-owner",
        "legacy-public-copy-helper",
        "literal-model-proposal",
        "literal-text-fallback",
        "public-chunk-author",
        "public-reply-author",
        "replace-reply-chunks",
        "reply-chunks-assignment",
    }


def test_production_public_text_authority_has_no_violations() -> None:
    violations = _production_violations()

    assert violations == (), "\n".join(
        f"{item.code}: {item.path}:{item.line}: {item.detail}"
        for item in violations
    )
