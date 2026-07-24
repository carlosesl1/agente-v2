"""Single construction and lifecycle owner for the standalone V2 runtime."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
import hashlib
import json

from reservation_boundary.sqlite_store import SQLiteBoundaryStore
from reservation_execution.sqlite_store import SQLiteUnitOfWork
from reservation_followup.sqlite_store import SQLiteFollowupUnitOfWork
from v2_application.completion import PublicOutboxStore
from v2_application.inbox import SQLiteInbox
from v2_application.payments import SQLitePaymentInitiationStore
from v2_host.settings import V2Settings


class V2Role(str, Enum):
    API = "api"
    WORKER = "worker"


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
    ) -> None:
        self.settings = settings
        self.role = role
        self.inbox = inbox
        self.boundary = boundary
        self.execution = execution
        self.followup = followup
        self.payment_initiation = payment_initiation
        self.public_outbox = public_outbox
        self._runtime_capabilities: dict[str, str] | None = None
        self._closed = False

    @classmethod
    def open(cls, *, settings: V2Settings, role: V2Role) -> V2Container:
        if type(settings) is not V2Settings:
            raise TypeError("settings must be exact V2Settings")
        if type(role) is not V2Role:
            raise TypeError("role must be exact V2Role")
        paths = settings.sqlite_paths
        opened: list[object] = []
        try:
            inbox = SQLiteInbox(paths["inbox"])
            if role is V2Role.API:
                return cls(
                    settings=settings,
                    role=role,
                    inbox=inbox,
                    boundary=None,
                    execution=None,
                    followup=None,
                    payment_initiation=None,
                    public_outbox=None,
                )
            boundary = SQLiteBoundaryStore.open_path_v8(paths["boundary"])
            opened.append(boundary)
            execution = SQLiteUnitOfWork.open(paths["execution"])
            opened.append(execution)
            followup = SQLiteFollowupUnitOfWork.open(paths["followup"])
            opened.append(followup)
            payment_initiation = SQLitePaymentInitiationStore(
                paths["payment_initiation"],
                result_encryption_key=hashlib.sha256(
                    b"v2-payment-result-store-v1\0"
                    + settings.webhook_secret.encode()
                ).digest(),
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
            },
            V2Role.WORKER: {
                "boundary": 1,
                "execution": 1,
                "followup": 1,
                "inbox": 1,
                "payment_initiation": 1,
                "public_outbox": 1,
            },
        }[self.role]
        capabilities: dict[str, str]
        reasons: list[str] = []
        if self.role is V2Role.API:
            capabilities = {
                "financial_webhooks": (
                    "ready" if self.settings.financial_webhooks_configured else "missing"
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
        if not self.settings.financial_webhooks_configured:
            reasons.append("financial_webhooks_not_configured")
        ready = (
            counts == expected
            and self.settings.financial_webhooks_configured
            and not reasons
        )
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

    def _worker_heartbeat_reason(self) -> str | None:
        path = self.settings.worker_heartbeat_path
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
            observed = datetime.fromisoformat(value["observed_at"])
            status = value["status"]
        except (OSError, KeyError, TypeError, ValueError, json.JSONDecodeError):
            return "worker_heartbeat_missing"
        if observed.tzinfo is None or observed.utcoffset() is None:
            return "worker_heartbeat_invalid"
        age = (datetime.now(timezone.utc) - observed.astimezone(timezone.utc)).total_seconds()
        if age < 0 or age > self.settings.worker_heartbeat_max_age_seconds:
            return "worker_heartbeat_stale"
        if status != "healthy":
            return "worker_heartbeat_degraded"
        return None

    def close(self) -> None:
        if self._closed:
            return
        for owner in (
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
