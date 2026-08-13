from __future__ import annotations

import argparse
import os
from pathlib import Path
import sqlite3

from v2_ops.projection import LedgerOnlyProjector
from v2_ops.sources import (
    SQLiteBoundaryProjectionSource,
    SQLiteExecutionProjectionSource,
    SQLitePaymentProjectionSource,
)
from v2_ops.store import SQLiteOpsTraceWriter


def _absolute(value: str) -> Path:
    path = Path(value)
    if not path.is_absolute():
        raise argparse.ArgumentTypeError("path must be absolute")
    return path


def _materialize_target_sidecars(path: Path) -> None:
    connection = sqlite3.connect(path)
    try:
        if connection.execute("PRAGMA journal_mode").fetchone()[0] != "wal":
            raise RuntimeError("ops projection target is not in WAL mode")
        connection.execute("BEGIN IMMEDIATE")
        connection.rollback()
        wal = Path(f"{path}-wal")
        shm = Path(f"{path}-shm")
        wal_bytes = wal.read_bytes()
        shm_bytes = shm.read_bytes()
    finally:
        connection.close()
    wal.write_bytes(wal_bytes)
    shm.write_bytes(shm_bytes)


def _reject_source_aliases(
    output: Path,
    sources: tuple[Path | None, ...],
) -> None:
    identities = {
        (source.stat().st_dev, source.stat().st_ino)
        for source in sources
        if source is not None
    }
    if output.exists() and (output.stat().st_dev, output.stat().st_ino) in identities:
        raise SystemExit("output must be physically distinct from every source")
    if not output.exists() and output in {source for source in sources if source is not None}:
        raise SystemExit("output must be physically distinct from every source")


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Project causally proven V2 ledger rows into an offline ops store."
    )
    parser.add_argument("--boundary", required=True, type=_absolute)
    parser.add_argument("--execution", type=_absolute)
    parser.add_argument("--payments", type=_absolute)
    parser.add_argument("--output", required=True, type=_absolute)
    parser.add_argument("--limit", type=int, default=10_000)
    args = parser.parse_args()
    key_hex = os.environ.get("V2_OPS_TRACE_KEY_HEX", "")
    try:
        key = bytes.fromhex(key_hex)
    except ValueError as exc:
        raise SystemExit("V2_OPS_TRACE_KEY_HEX must be valid hex") from exc
    if len(key) != 32:
        raise SystemExit("V2_OPS_TRACE_KEY_HEX must encode exactly 32 bytes")
    _reject_source_aliases(
        args.output,
        (args.boundary, args.execution, args.payments),
    )

    with (
        SQLiteBoundaryProjectionSource(args.boundary) as boundary,
        SQLiteOpsTraceWriter(args.output, key) as writer,
    ):
        execution = (
            None
            if args.execution is None
            else SQLiteExecutionProjectionSource(args.execution)
        )
        payments = (
            None
            if args.payments is None
            else SQLitePaymentProjectionSource(args.payments)
        )
        try:
            result = LedgerOnlyProjector(
                source=boundary,
                execution_source=execution,
                payment_source=payments,
                writer=writer,
            ).run(limit=args.limit)
        finally:
            if execution is not None:
                execution.close()
            if payments is not None:
                payments.close()
    _materialize_target_sidecars(args.output)
    print(
        "ledger-projection: OK "
        f"projected={result.projected} "
        f"skipped_existing={result.skipped_existing} degraded={result.degraded}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
