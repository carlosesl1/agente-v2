"""Production worker composition for the standalone V2 host.

The dark-read-only mode deliberately constructs provider reads but gives no
queue an effect capability.  Shadow/controlled modes are rejected until their
model/authority/write graph is fully configured; qualification-only factories
are never selected implicitly.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone

from reservation_execution.reconciliation import Reconciler
from reservation_followup.reconciliation import PaymentReconciler
from v2_adapters.bokun import BokunReadAdapter
from v2_adapters.cloudbeds import CloudbedsReadAdapter
from v2_adapters.hermes_model import HermesModelAdapter
from v2_adapters.knowledge import KnowledgeReadAdapter
from v2_adapters.manychat_profile import ManyChatProfileAdapter
from v2_adapters.provider_http import (
    BokunHTTPTransport,
    CloudbedsHTTPTransport,
    FileKnowledgeTransport,
    ManyChatHTTPTransport,
)
from v2_application.inbox_worker import InboxTurnWorker
from v2_application.reads import V2ReadService
from v2_application.turn_executor import V2TurnExecutor
from v2_contracts.providers import ReadKind, ReadRequest
from v2_application.conversation import V2ConversationReducer
from v2_host.composition import V2Container, V2Role
from v2_host.public_authority import ManifestPublicAuthorityResolver
from v2_host.settings import RuntimeMode, V2Settings
from v2_host.worker_main import WorkerQueue


class UTCClock:
    def now(self) -> datetime:
        return datetime.now(timezone.utc)


@dataclass(frozen=True, slots=True)
class ClosedCapabilityWorker:
    capability: str

    def __post_init__(self) -> None:
        if type(self.capability) is not str or not self.capability:
            raise ValueError("closed capability name must be non-empty exact text")

    def run_once(self, *, now: datetime) -> dict[str, str]:
        if type(now) is not datetime or now.tzinfo is None or now.utcoffset() != timedelta(0):
            raise ValueError("now must be an exact UTC datetime")
        return {"status": "closed", "capability": self.capability}


class ReconciliationStage:
    """Probe mandatory reads and recover leases without any provider writes."""

    def __init__(
        self,
        *,
        container: V2Container,
        reads: V2ReadService | None = None,
        settings: V2Settings | None = None,
    ) -> None:
        if type(container) is not V2Container or container.role is not V2Role.WORKER:
            raise TypeError("reconciliation requires an exact worker container")
        if container.execution is None or container.followup is None:
            raise ValueError("reconciliation durable owners are unavailable")
        self._reservation = Reconciler(container.execution)
        self._payment = PaymentReconciler(store=container.followup)
        if (reads is None) != (settings is None):
            raise ValueError("read probe requires both service and settings")
        self._reads = reads
        self._settings = settings
        self._next_probe_at: datetime | None = None
        self._probe_healthy = False

    def _probe_reads(self, *, now: datetime) -> dict[str, str]:
        if self._reads is None or self._settings is None:
            return {"status": "closed"}
        if self._next_probe_at is not None and now < self._next_probe_at:
            if not self._probe_healthy:
                raise RuntimeError("mandatory provider read probe is degraded")
            return {"status": "cached_healthy"}
        self._next_probe_at = now + timedelta(
            seconds=self._settings.read_probe_interval_seconds
        )
        try:
            lodging = ReadRequest(
                request_id=f"probe:cloudbeds:{self._settings.read_probe_check_in}",
                kind=ReadKind.LODGING,
                check_in=date.fromisoformat(self._settings.read_probe_check_in),
                check_out=date.fromisoformat(self._settings.read_probe_check_out),
                adults=2,
                children=0,
            )
            activity = ReadRequest(
                request_id=f"probe:bokun:{self._settings.read_probe_activity_date}",
                kind=ReadKind.ACTIVITY,
                product_id=self._settings.read_probe_product_id,
                activity_date=date.fromisoformat(
                    self._settings.read_probe_activity_date
                ),
                participants=2,
            )
            lodging_observation = self._reads.read(lodging)
            self._reads.accept(
                lodging_observation,
                now=datetime.now(timezone.utc),
            )
            activity_observation = self._reads.read(activity)
            self._reads.accept(
                activity_observation,
                now=datetime.now(timezone.utc),
            )
        except Exception:
            self._probe_healthy = False
            raise
        self._probe_healthy = True
        return {"status": "fresh_healthy"}

    def run_once(self, *, now: datetime) -> dict[str, object]:
        return {
            "status": "ok",
            "provider_reads": self._probe_reads(now=now),
            "reservation": self._reservation.run_once(now=now),
            "payment": self._payment.run_once(now=now),
        }


def build_read_service(settings: V2Settings) -> V2ReadService:
    if type(settings) is not V2Settings:
        raise TypeError("settings must be exact V2Settings")
    if not settings.read_providers_configured:
        raise ValueError("productive reads require complete Cloudbeds/Bókun configuration")
    clock = UTCClock()
    cloudbeds = CloudbedsReadAdapter(
        transport=CloudbedsHTTPTransport(
            api_key=settings.cloudbeds_api_key,
            property_id=settings.cloudbeds_property_id,
            base_url=settings.cloudbeds_base_url,
        ),
        clock=clock,
        ttl=timedelta(minutes=5),
    )
    bokun = BokunReadAdapter(
        transport=BokunHTTPTransport(
            access_key=settings.bokun_access_key,
            secret_key=settings.bokun_secret_key,
            product_map=settings.bokun_product_map,
            base_url=settings.bokun_base_url,
        ),
        clock=clock,
        ttl=timedelta(minutes=5),
    )
    ports = {
        ReadKind.LODGING: cloudbeds,
        ReadKind.ROOM_DESCRIPTION: cloudbeds,
        ReadKind.ACTIVITY: bokun,
        ReadKind.ACTIVITY_DESCRIPTION: bokun,
    }
    if settings.knowledge_base_path is not None:
        ports[ReadKind.KNOWLEDGE] = KnowledgeReadAdapter(
            transport=FileKnowledgeTransport(settings.knowledge_base_path),
            clock=clock,
            ttl=timedelta(minutes=5),
        )
    return V2ReadService(ports)


def _build_inbox_worker(
    *,
    container: V2Container,
    settings: V2Settings,
    reads: V2ReadService,
) -> InboxTurnWorker:
    if container.boundary is None or container.inbox is None:
        raise ValueError("shadow inbox durable owners are unavailable")
    if settings.public_authority_manifest_path is None:
        raise ValueError("shadow public authority manifest is unavailable")
    clock = UTCClock()
    authority = ManifestPublicAuthorityResolver(
        store=container.boundary,
        manifest_path=settings.public_authority_manifest_path,
        hmac_key=settings.public_authority_hmac_key,
        now=clock.now(),
    )
    profile = ManyChatProfileAdapter(
        transport=ManyChatHTTPTransport(
            api_key=settings.manychat_api_key,
            base_url=settings.manychat_base_url,
        ),
        ttl=timedelta(minutes=5),
    )
    model = HermesModelAdapter(
        command=settings.hermes_command,
        system_prompt=settings.hermes_system_prompt,
        timeout=settings.hermes_timeout_seconds,
        transcript_key=settings.hermes_transcript_key,
    )
    executor = V2TurnExecutor(
        store=container.boundary,
        model=model,
        reads=reads,
        profile=profile,
        reducer=V2ConversationReducer(),
        public_authority=authority,
        clock=clock,
        locale="pt-BR",
        turn_timeout=timedelta(seconds=settings.hermes_timeout_seconds + 5),
        max_commit_attempts=2,
    )
    return InboxTurnWorker(
        inbox=container.inbox,
        executor=executor,
        quiet_window=timedelta(milliseconds=750),
        lease_ttl=timedelta(seconds=settings.hermes_timeout_seconds + 15),
    )


def build_worker_set(
    *, container: V2Container, settings: V2Settings
) -> dict[WorkerQueue, object]:
    if type(container) is not V2Container or container.role is not V2Role.WORKER:
        raise TypeError("productive worker factory requires an exact worker container")
    if type(settings) is not V2Settings or container.settings is not settings:
        raise TypeError("productive worker factory requires the container settings identity")
    if settings.runtime_mode is RuntimeMode.API_ONLY:
        raise ValueError("api_only runtime cannot start a worker process")
    reads = build_read_service(settings)
    if settings.runtime_mode is RuntimeMode.CONTROLLED_WRITE:
        raise RuntimeError(
            "controlled-write graph is closed until every enabled effect transport is installed"
        )
    inbox_worker: object
    if settings.runtime_mode is RuntimeMode.SHADOW:
        inbox_worker = _build_inbox_worker(
            container=container,
            settings=settings,
            reads=reads,
        )
    else:
        inbox_worker = ClosedCapabilityWorker("inbox_turns")
    workers: dict[WorkerQueue, object] = {
        WorkerQueue.INBOX: inbox_worker,
        WorkerQueue.RESERVATION: ClosedCapabilityWorker("reservation_writes"),
        WorkerQueue.PAYMENT_INITIATION: ClosedCapabilityWorker("payment_initiation"),
        WorkerQueue.SETTLEMENT: ClosedCapabilityWorker("settlement_writes"),
        WorkerQueue.POST_PAYMENT: ClosedCapabilityWorker("post_payment_delivery"),
        WorkerQueue.PUBLIC_DELIVERY: ClosedCapabilityWorker("manychat_delivery"),
        WorkerQueue.RECONCILIATION: ReconciliationStage(
            container=container,
            reads=reads,
            settings=settings,
        ),
    }
    container.register_runtime_capabilities(
        {
            "bokun_reads": "ready",
            "cloudbeds_reads": "ready",
            "knowledge_reads": (
                "ready" if settings.knowledge_base_path is not None else "closed"
            ),
            "hermes_model": (
                "ready" if settings.runtime_mode is RuntimeMode.SHADOW else "closed"
            ),
            "inbox_turns": (
                "ready" if settings.runtime_mode is RuntimeMode.SHADOW else "closed"
            ),
            "manychat_profile": (
                "ready" if settings.runtime_mode is RuntimeMode.SHADOW else "closed"
            ),
            "manychat_delivery": "closed",
            "payment_initiation": "closed",
            "reservation_writes": "closed",
            "settlement_writes": "closed",
            "reconciliation": "ready",
        }
    )
    # Keep the service reachable for the read-only qualification probe without
    # exposing it through the public API or a provider-write worker.
    container.read_service = reads
    return workers


__all__ = [
    "ClosedCapabilityWorker",
    "ReconciliationStage",
    "UTCClock",
    "build_read_service",
    "build_worker_set",
]
