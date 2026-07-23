"""Single construction and lifecycle owner for the standalone V2 runtime."""

from __future__ import annotations

from enum import Enum

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
                paths["payment_initiation"]
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


__all__ = ["V2Container", "V2Role"]
