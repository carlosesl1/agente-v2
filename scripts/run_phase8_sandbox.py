#!/usr/bin/env python3
"""Interactive source runner for the Phase 8 effect-denied sandbox."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import sys

_PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from reservation_boundary.sandbox import (  # noqa: E402
    DEFAULT_SANDBOX_MODEL,
    HermesDockerModel,
    ModelCallFailed,
    ReadCallFailed,
    SandboxConversation,
    SandboxProtocolError,
    SQLiteSandboxStore,
    V2ProviderDockerRead,
)

DEFAULT_V2_READ_WORKER = "agente-v2-digest-canary-169a67c-worker"


def _default_db() -> Path:
    override = os.environ.get("PHASE8_SANDBOX_DB")
    if override:
        return Path(override).expanduser().resolve()
    host_home = Path("/home/ubuntu")
    base = host_home if host_home.is_dir() else Path.home()
    return base / ".local/share/agente-v2-phase8-evidence/sandbox/conversation.sqlite3"


def _knowledge(path: Path | None) -> dict[str, object]:
    if path is None:
        return {
            "business": "Chapada Backpackers",
            "environment": "sandbox",
            "external_effects": False,
            "known_services": ["hostel", "agency"],
            "tour_catalog": [
                {
                    "canonical_id": "product:buracao",
                    "public_name": "Cachoeira do Buracão",
                }
            ],
            "notice": (
                "Preço e disponibilidade de hospedagem e passeio só podem vir de "
                "READ_OBSERVATIONS; links e efeitos externos permanecem indisponíveis."
            ),
        }
    value = json.loads(path.read_text(encoding="utf-8"))
    if type(value) is not dict:
        raise ValueError("knowledge file must contain one JSON object")
    return value


def _submit(runner: SandboxConversation, session: str, message: str) -> bool:
    try:
        result = runner.submit(session_id=session, message=message)
    except (ModelCallFailed, ReadCallFailed, SandboxProtocolError, ValueError) as exc:
        print(f"[sandbox bloqueado] {exc}", file=sys.stderr)
        return False
    print(result.reply)
    if result.blocked_effects:
        kinds = ", ".join(item.kind for item in result.blocked_effects)
        print(f"[sandbox: {len(result.blocked_effects)} efeito(s) bloqueado(s): {kinds}]", file=sys.stderr)
    for observation in result.read_observations:
        print(
            f"[sandbox: leitura {observation.kind} "
            f"status={observation.status} "
            f"hash={observation.canonical_hash()[:12]}]",
            file=sys.stderr,
        )
    return True


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Conversa Maya real por source; nenhum efeito externo possui executor.",
    )
    parser.add_argument("--db", type=Path, default=_default_db())
    parser.add_argument("--session", default="carlos-sandbox")
    parser.add_argument("--message")
    parser.add_argument("--knowledge", type=Path)
    parser.add_argument("--container", default="hermes-webui")
    parser.add_argument("--read-worker-container", default=DEFAULT_V2_READ_WORKER)
    parser.add_argument("--provider", default="openai-codex")
    parser.add_argument("--model", default=DEFAULT_SANDBOX_MODEL)
    args = parser.parse_args()

    runner = SandboxConversation(
        store=SQLiteSandboxStore(args.db.expanduser().resolve()),
        model=HermesDockerModel(
            project_root=_PROJECT_ROOT,
            container=args.container,
            provider=args.provider,
            model=args.model,
        ),
        reads=V2ProviderDockerRead(
            project_root=_PROJECT_ROOT,
            container=args.read_worker_container,
        ),
        knowledge=_knowledge(args.knowledge),
    )
    if args.message is not None:
        return 0 if _submit(runner, args.session, args.message) else 2

    print("Phase 8 sandbox — efeitos externos bloqueados. Digite /sair para encerrar.")
    while True:
        try:
            message = input("Você> ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            return 0
        if message in {"/sair", "/exit", "/quit"}:
            return 0
        if not message:
            continue
        print("Maya> ", end="", flush=True)
        _submit(runner, args.session, message)


if __name__ == "__main__":
    raise SystemExit(main())
