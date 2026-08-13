"""Single construction and lifecycle owner for the standalone V2 runtime."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from enum import Enum
import json
import os
from pathlib import Path
import stat

from reservation_boundary.sqlite_store import SQLiteBoundaryStore
from reservation_execution.sqlite_store import SQLiteUnitOfWork
from reservation_followup.sqlite_store import SQLiteFollowupUnitOfWork
from v2_application.completion import PublicOutboxStore
from v2_application.inbox import SQLiteInbox
from v2_application.payments import SQLitePaymentInitiationStore
from v2_application.private_customer_facts import SQLitePrivateCustomerFactStore
from v2_host.public_authority import active_authority_reason
from v2_host.settings import RuntimeMode, V2Settings
from v2_ops.recording import NullOpsRecorder, SQLiteOpsRecorder
from v2_ops.store import SQLiteOpsTraceWriter


class V2Role(str, Enum):
    API = "api"
    WORKER = "worker"


def _validate_worker_heartbeat_payload(value: object) -> None:
    from v2_host.worker_main import WorkerFailureReason, WorkerQueue

    if type(value) is not dict or set(value) != {
        "schema",
        "observed_at",
        "status",
        "failed_queues",
        "public_ingress_ready",
        "public_ingress_reason",
        "public_turn_capacity",
        "queues",
    }:
        raise ValueError("worker heartbeat envelope is not exact")
    if value["schema"] != "v2-worker-heartbeat-v2":
        raise ValueError("worker heartbeat schema is not supported")
    try:
        observed = datetime.fromisoformat(value["observed_at"])
    except (TypeError, ValueError) as exc:
        raise ValueError("worker heartbeat timestamp is invalid") from exc
    if observed.tzinfo is None or observed.utcoffset() is None:
        raise ValueError("worker heartbeat timestamp must be timezone-aware")
    if type(value["public_ingress_ready"]) is not bool:
        raise ValueError("worker public ingress readiness must be exact bool")
    reason = value["public_ingress_reason"]
    if reason is not None and (type(reason) is not str or not reason):
        raise ValueError("worker public ingress reason must be closed text")
    if (
        type(value["public_turn_capacity"]) is not int
        or value["public_turn_capacity"] < 0
    ):
        raise ValueError("worker public turn capacity is invalid")
    queue_names = tuple(queue.value for queue in WorkerQueue)
    queues = value["queues"]
    if type(queues) is not dict or set(queues) != set(queue_names):
        raise ValueError("worker heartbeat queue universe is not exact")
    failed: list[str] = []
    expected_queue_fields = {
        "status",
        "reason_code",
        "failure_fingerprint",
        "consecutive_failures",
        "last_success_at",
        "last_failure_at",
        "backoff_seconds",
        "next_attempt_at",
    }
    for queue_name in queue_names:
        health = queues[queue_name]
        if type(health) is not dict or set(health) != expected_queue_fields:
            raise ValueError("worker queue health shape is not exact")
        status = health["status"]
        if status not in {"healthy", "failed", "backoff"}:
            raise ValueError("worker queue health status is invalid")
        count = health["consecutive_failures"]
        if type(count) is not int or count < 0:
            raise ValueError("worker queue failure count is invalid")
        backoff = health["backoff_seconds"]
        if type(backoff) is not float or backoff < 0:
            raise ValueError("worker queue backoff is invalid")
        for field in ("last_success_at", "last_failure_at", "next_attempt_at"):
            stamp = health[field]
            if stamp is None:
                continue
            try:
                parsed = datetime.fromisoformat(stamp)
            except (TypeError, ValueError) as exc:
                raise ValueError("worker queue timestamp is invalid") from exc
            if parsed.tzinfo is None or parsed.utcoffset() is None:
                raise ValueError("worker queue timestamp must be timezone-aware")
        if status == "healthy":
            if (
                health["reason_code"] is not None
                or health["failure_fingerprint"] is not None
                or count != 0
                or backoff != 0.0
                or health["next_attempt_at"] is not None
            ):
                raise ValueError("healthy queue carries failure evidence")
            continue
        failed.append(queue_name)
        fingerprint = health["failure_fingerprint"]
        if (
            health["reason_code"]
            not in {reason.value for reason in WorkerFailureReason}
            or type(fingerprint) is not str
            or len(fingerprint) != 64
            or any(char not in "0123456789abcdef" for char in fingerprint)
            or count < 1
            or health["last_failure_at"] is None
            or health["next_attempt_at"] is None
            or backoff <= 0
        ):
            raise ValueError("failed queue evidence is not canonical")
    if value["failed_queues"] != failed:
        raise ValueError("worker heartbeat failed queue projection diverged")
    expected_status = "degraded" if failed else "healthy"
    if value["status"] != expected_status:
        raise ValueError("worker heartbeat aggregate status diverged")


def _authenticate_sqlite_owner_files(paths: dict[str, Path]) -> None:
    identities: dict[tuple[int, int], str] = {}
    descriptors: list[tuple[Path, int, tuple[int, int]]] = []
    flags = os.O_RDWR | os.O_CREAT | getattr(os, "O_CLOEXEC", 0)
    flags |= getattr(os, "O_NOFOLLOW", 0)
    try:
        for name, path in paths.items():
            path.parent.mkdir(parents=True, exist_ok=True)
            descriptor = os.open(path, flags, 0o600)
            info = os.fstat(descriptor)
            identity = (info.st_dev, info.st_ino)
            descriptors.append((path, descriptor, identity))
            if (
                not stat.S_ISREG(info.st_mode)
                or info.st_nlink != 1
                or identity in identities
            ):
                raise RuntimeError("sqlite owner paths must be physically distinct")
            identities[identity] = name
        for path, _descriptor, identity in descriptors:
            current = path.stat()
            if (current.st_dev, current.st_ino) != identity or current.st_nlink != 1:
                raise RuntimeError("sqlite owner paths must be physically distinct")
    except OSError:
        raise RuntimeError("sqlite owner paths cannot be authenticated") from None
    finally:
        for _path, descriptor, _identity in descriptors:
            os.close(descriptor)


@dataclass(frozen=True, slots=True)
class V2Readiness:
    status: str
    role: V2Role
    owner_counts: dict[str, int]
    real_effect_gates: dict[str, bool]
    capabilities: dict[str, str]
    reasons: tuple[str, ...]

    def __post_init__(self) -> None:
        if self.status not in {"ready", "not_ready"}:
            raise ValueError("readiness status is outside the closed grammar")
        if type(self.role) is not V2Role:
            raise TypeError("readiness role must be exact V2Role")
        if type(self.capabilities) is not dict or any(
            type(key) is not str
            or not key
            or value not in {"ready", "closed", "missing", "degraded"}
            for key, value in self.capabilities.items()
        ):
            raise ValueError("readiness capabilities use a closed status grammar")
        if type(self.reasons) is not tuple or any(
            type(item) is not str or not item for item in self.reasons
        ):
            raise TypeError("readiness reasons must be exact non-empty strings")


class V2Container:
    """Own exactly one durable component instance for one V2 process."""

    def __init__(
        self,
        *,
        settings: V2Settings,
        role: V2Role,
        inbox: SQLiteInbox,
        boundary: SQLiteBoundaryStore | None,
        execution: SQLiteUnitOfWork | None,
        followup: SQLiteFollowupUnitOfWork | None,
        payment_initiation: SQLitePaymentInitiationStore | None,
        public_outbox: PublicOutboxStore | None,
        private_customer: SQLitePrivateCustomerFactStore | None,
        ops_trace_writer: SQLiteOpsTraceWriter | None,
    ) -> None:
        self.settings = settings
        self.role = role
        self.inbox = inbox
        self.boundary = boundary
        self.execution = execution
        self.followup = followup
        self.payment_initiation = payment_initiation
        self.public_outbox = public_outbox
        self.private_customer = private_customer
        self._ops_trace_writer = ops_trace_writer
        self.ops_recorder = (
            NullOpsRecorder()
            if ops_trace_writer is None
            else SQLiteOpsRecorder(ops_trace_writer)
        )
        self._runtime_capabilities: dict[str, str] | None = None
        self._public_authority_resolver: object | None = None
        self._closed = False

    @classmethod
    def open(cls, *, settings: V2Settings, role: V2Role) -> V2Container:
        if type(settings) is not V2Settings:
            raise TypeError("settings must be exact V2Settings")
        if type(role) is not V2Role:
            raise TypeError("role must be exact V2Role")
        paths = settings.sqlite_paths
        if role is V2Role.API:
            owned_names = (
                ("inbox", "ops_trace")
                if settings.ops_trace_path is not None
                else ("inbox",)
            )
        else:
            mutable_worker_owners = [
                "inbox",
                "boundary",
                "execution",
                "followup",
                "public_outbox",
                "private_customer",
            ]
            if settings.enabled_payment_methods:
                mutable_worker_owners.append("payment_initiation")
            if settings.ops_trace_path is not None:
                mutable_worker_owners.append("ops_trace")
            owned_names = tuple(mutable_worker_owners)
        owner_paths = {name: paths[name] for name in owned_names if name != "ops_trace"}
        if "ops_trace" in owned_names:
            owner_paths["ops_trace"] = settings.ops_trace_path
        _authenticate_sqlite_owner_files(owner_paths)
        opened: list[object] = []
        try:
            inbox = SQLiteInbox(paths["inbox"])
            opened.append(inbox)
            if role is V2Role.API:
                ops_trace_writer = None
                if settings.ops_trace_path is not None:
                    ops_trace_writer = SQLiteOpsTraceWriter(
                        settings.ops_trace_path,
                        settings.ops_trace_key,
                    )
                    opened.append(ops_trace_writer)
                return cls(
                    settings=settings,
                    role=role,
                    inbox=inbox,
                    boundary=None,
                    execution=None,
                    followup=None,
                    payment_initiation=None,
                    public_outbox=None,
                    private_customer=None,
                    ops_trace_writer=ops_trace_writer,
                )
            ops_trace_writer = None
            if settings.ops_trace_path is not None:
                ops_trace_writer = SQLiteOpsTraceWriter(
                    settings.ops_trace_path,
                    settings.ops_trace_key,
                )
                opened.append(ops_trace_writer)
            boundary = SQLiteBoundaryStore.open_path_v8(paths["boundary"])
            opened.append(boundary)
            private_customer = SQLitePrivateCustomerFactStore(
                paths["private_customer"]
            )
            opened.append(private_customer)
            execution = SQLiteUnitOfWork.open_v6(paths["execution"])
            opened.append(execution)
            followup = SQLiteFollowupUnitOfWork.open(paths["followup"])
            opened.append(followup)
            payment_initiation = None
            if settings.enabled_payment_methods:
                payment_initiation = SQLitePaymentInitiationStore(
                    paths["payment_initiation"],
                    result_encryption_key=settings.payment_result_store_key,
                )
                opened.append(payment_initiation)
            public_outbox = PublicOutboxStore(paths["public_outbox"])
            opened.append(public_outbox)
            return cls(
                settings=settings,
                role=role,
                inbox=inbox,
                boundary=boundary,
                execution=execution,
                followup=followup,
                payment_initiation=payment_initiation,
                public_outbox=public_outbox,
                private_customer=private_customer,
                ops_trace_writer=ops_trace_writer,
            )
        except BaseException:
            for owner in reversed(opened):
                close = getattr(owner, "close", None)
                if callable(close):
                    close()
            raise

    def owner_counts(self) -> dict[str, int]:
        if self._closed:
            raise RuntimeError("V2 container is closed")
        return {
            "boundary": int(self.boundary is not None),
            "execution": int(self.execution is not None),
            "followup": int(self.followup is not None),
            "inbox": 1,
            "payment_initiation": int(self.payment_initiation is not None),
            "public_outbox": int(self.public_outbox is not None),
            "private_customer": int(self.private_customer is not None),
        }

    def readiness(self) -> V2Readiness:
        counts = self.owner_counts()
        expected = {
            V2Role.API: {
                "boundary": 0,
                "execution": 0,
                "followup": 0,
                "inbox": 1,
                "payment_initiation": 0,
                "public_outbox": 0,
                "private_customer": 0,
            },
            V2Role.WORKER: {
                "boundary": 1,
                "execution": 1,
                "followup": 1,
                "inbox": 1,
                "payment_initiation": int(bool(self.settings.enabled_payment_methods)),
                "public_outbox": 1,
                "private_customer": 1,
            },
        }[self.role]
        capabilities: dict[str, str]
        reasons: list[str] = []
        if self.role is V2Role.API:
            capabilities = {
                "financial_webhooks": (
                    "ready" if self.settings.financial_webhooks_configured else "closed"
                ),
                "manychat_ingress": "ready",
            }
            if self.settings.require_worker_heartbeat:
                heartbeat_reason = self._worker_heartbeat_reason()
                capabilities["worker_heartbeat"] = (
                    "ready" if heartbeat_reason is None else "missing"
                )
                if heartbeat_reason is not None:
                    reasons.append(heartbeat_reason)
            if self.settings.runtime_mode in {
                RuntimeMode.CONTROLLED_WRITE,
                RuntimeMode.GENERAL_AVAILABILITY,
            }:
                ingress_reason = self.controlled_public_ingress_reason(
                    now=datetime.now(timezone.utc),
                )
                capabilities["controlled_public_ingress"] = (
                    "ready" if ingress_reason is None else "missing"
                )
                if ingress_reason is not None:
                    reasons.append(ingress_reason)
        elif self._runtime_capabilities is None:
            capabilities = {"productive_graph": "missing"}
            reasons.append("productive_graph_not_built")
        else:
            capabilities = dict(self._runtime_capabilities)
            reasons.extend(
                f"capability_{name}_{status}"
                for name, status in capabilities.items()
                if status in {"missing", "degraded"}
            )
        if counts != expected:
            reasons.append("durable_owner_count_mismatch")
        ready = counts == expected and not reasons
        return V2Readiness(
            status="ready" if ready else "not_ready",
            role=self.role,
            owner_counts=counts,
            real_effect_gates=self.settings.real_effect_gates,
            capabilities=capabilities,
            reasons=tuple(dict.fromkeys(reasons)),
        )

    def register_runtime_capabilities(self, capabilities: dict[str, str]) -> None:
        if self.role is not V2Role.WORKER:
            raise ValueError("runtime capabilities belong to the worker role")
        if self._runtime_capabilities is not None:
            raise RuntimeError("runtime capabilities are immutable after registration")
        if type(capabilities) is not dict or not capabilities or any(
            type(key) is not str
            or not key
            or value not in {"ready", "closed", "missing", "degraded"}
            for key, value in capabilities.items()
        ):
            raise ValueError("runtime capabilities use a closed status grammar")
        self._runtime_capabilities = dict(capabilities)

    def register_public_authority_resolver(self, resolver: object) -> None:
        if self.role is not V2Role.WORKER:
            raise ValueError("public authority resolver belongs to the worker role")
        if self._public_authority_resolver is not None:
            raise RuntimeError("public authority resolver is immutable after registration")
        if not callable(getattr(resolver, "available_turn_capacity", None)):
            raise TypeError("public authority resolver must expose capacity")
        self._public_authority_resolver = resolver

    def public_turn_capacity(self, *, now: datetime) -> int:
        if type(now) is not datetime or now.tzinfo is None or now.utcoffset() != timedelta(0):
            raise ValueError("public capacity now must be exact UTC")
        if self._public_authority_resolver is None:
            return 0
        if self.settings.runtime_mode is RuntimeMode.GENERAL_AVAILABILITY:
            # GA provisions one finite authority generation per authenticated turn.
            # Capacity is therefore on demand, not a pre-installed allowlist total.
            return self._public_authority_resolver.available_turn_capacity(
                "0",
                now=now,
            )
        capacities = tuple(
            self._public_authority_resolver.available_turn_capacity(
                subscriber,
                now=now,
            )
            for subscriber in self.settings.allowed_subscriber_ids
        )
        return min(capacities) if capacities else 0

    def controlled_public_ingress_reason(self, *, now: datetime) -> str | None:
        if type(now) is not datetime or now.tzinfo is None or now.utcoffset() != timedelta(0):
            raise ValueError("controlled ingress now must be exact UTC")
        if self.settings.runtime_mode not in {
            RuntimeMode.CONTROLLED_WRITE,
            RuntimeMode.GENERAL_AVAILABILITY,
        }:
            return None
        if self.settings.runtime_mode is RuntimeMode.GENERAL_AVAILABILITY:
            if len(self.settings.public_authority_hmac_key) < 32:
                return "public_authority_invalid"
            return None
        if self.settings.public_authority_manifest_path is None:
            return "public_authority_missing"
        reason = active_authority_reason(
            manifest_path=self.settings.public_authority_manifest_path,
            hmac_key=self.settings.public_authority_hmac_key,
            subscriber_ids=self.settings.allowed_subscriber_ids,
            now=now,
        )
        if reason is not None:
            return reason
        if self.role is V2Role.WORKER and self.public_turn_capacity(now=now) < 1:
            return "public_authority_allocations_exhausted"
        return None

    def _worker_heartbeat_reason(self) -> str | None:
        path = self.settings.worker_heartbeat_path
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except OSError:
            return "worker_heartbeat_missing"
        except (TypeError, ValueError, json.JSONDecodeError):
            return "worker_heartbeat_invalid"
        try:
            _validate_worker_heartbeat_payload(value)
            observed = datetime.fromisoformat(value["observed_at"])
            status = value["status"]
        except (KeyError, TypeError, ValueError):
            return "worker_heartbeat_invalid"
        if observed.tzinfo is None or observed.utcoffset() is None:
            return "worker_heartbeat_invalid"
        age = (datetime.now(timezone.utc) - observed.astimezone(timezone.utc)).total_seconds()
        if age < 0 or age > self.settings.worker_heartbeat_max_age_seconds:
            return "worker_heartbeat_stale"
        if status != "healthy":
            return "worker_heartbeat_degraded"
        if self.settings.runtime_mode in {
            RuntimeMode.CONTROLLED_WRITE,
            RuntimeMode.GENERAL_AVAILABILITY,
        }:
            if value.get("public_ingress_ready") is not True:
                reason = value.get("public_ingress_reason")
                return (
                    reason
                    if type(reason) is str and reason
                    else "worker_public_ingress_not_ready"
                )
            if self.settings.runtime_mode is RuntimeMode.CONTROLLED_WRITE:
                capacity = value.get("public_turn_capacity")
                if type(capacity) is not int or capacity < 1:
                    return "public_authority_allocations_exhausted"
        return None

    def close(self) -> None:
        if self._closed:
            return
        for owner in (
            self._ops_trace_writer,
            self.private_customer,
            self.public_outbox,
            self.payment_initiation,
            self.followup,
            self.execution,
            self.boundary,
        ):
            if owner is not None:
                owner.close()
        self._closed = True


__all__ = ["V2Container", "V2Readiness", "V2Role"]
