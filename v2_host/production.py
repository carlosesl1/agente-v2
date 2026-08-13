"""Production worker composition for the standalone V2 host.

The dark-read-only mode deliberately constructs provider reads but gives no
queue an effect capability.  Shadow/controlled modes are rejected until their
model/authority/write graph is fully configured; qualification-only factories
are never selected implicitly.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone

from reservation_boundary.worker_store import SQLiteBoundaryWorkerStore
from reservation_domain import ServiceKind
from reservation_execution.reconciliation import Reconciler
from reservation_followup.reconciliation import PaymentReconciler
from reservation_followup.workers import HandoffOutboxWorker
from v2_adapters.bokun import BokunReadAdapter, BokunReservationPort
from v2_adapters.cloudbeds import CloudbedsReadAdapter, CloudbedsReservationPort
from v2_adapters.hermes_model import HermesModelAdapter
from v2_adapters.knowledge import KnowledgeReadAdapter
from v2_adapters.manychat_profile import ManyChatProfileAdapter
from v2_adapters.manychat import ManyChatFlowDeliveryAdapter
from v2_adapters.payment_instructions import FilePaymentInstructionCatalog
from v2_adapters.pix import PixInstructionAdapter
from v2_adapters.provider_http import (
    BokunGETAuditTransport,
    BokunHTTPTransport,
    CloudbedsGETAuditTransport,
    CloudbedsHTTPTransport,
    FileKnowledgeTransport,
    ManyChatHTTPTransport,
)
from v2_adapters.stripe import (
    StripeLinkAdapter,
    StripeLinkReconciliationAdapter,
    StripeTestHTTPTransport,
    StripeTestReconciliationTransport,
)
from v2_adapters.wise import WiseInstructionAdapter
from v2_application.inbox_worker import InboxTurnWorker
from v2_application.lead_identity import DurableLeadResolver
from v2_application.bokun_audit import (
    BokunAuditProjector,
    BokunAuditStatus,
    BokunAuditWorker,
    SQLiteBokunAuditStore,
)
from v2_application.cloudbeds_audit import (
    CloudbedsAuditProjector,
    CloudbedsAuditStatus,
    CloudbedsAuditWorker,
    SQLiteCloudbedsAuditStore,
)
from v2_application.completion_projector import CompletionProjector
from v2_application.critical_actions import CriticalActionPolicy
from v2_application.outcome_projector import ReservationOutcomeProjector
from v2_application.payments import PaymentInitiationWorker, PaymentService
from v2_application.relay_worker import BoundaryRelayWorker
from v2_application.reads import PrivateOfferBindingResolver, V2ReadService
from v2_application.public_delivery import CombinedPublicDeliveryWorker
from v2_application.recovery import (
    HandoffCoordinator,
    ManualReviewHandoffProjector,
)
from v2_application.reservations import V2ReservationExecutionAdapter
from v2_application.workers import V2ReservationWorker
from v2_application.turn_executor import V2TurnExecutor
from v2_contracts.providers import (
    ProviderWriteAuthorization,
    ReadKind,
    ReadRequest,
)
from v2_contracts.payments import BusinessUnit, PaymentMethod
from v2_contracts.critical_actions import CriticalActionKind
from v2_application.conversation import V2ConversationReducer
from v2_host.composition import V2Container, V2Role
from v2_host.manychat_handoff import ManyChatHandoffDeliveryAdapter
from v2_host.public_authority import (
    GeneralAvailabilityPublicAuthorityResolver,
    ManifestPublicAuthorityResolver,
)
from v2_host.settings import RuntimeMode, V2Settings
from v2_host.worker_main import (
    WorkerFailureReason,
    WorkerHealthResult,
    WorkerQueue,
)


class UTCClock:
    def now(self) -> datetime:
        return datetime.now(timezone.utc)


def _critical_action_policy(settings: V2Settings) -> CriticalActionPolicy:
    """Derive conversational authority only from closed runtime capabilities."""
    if type(settings) is not V2Settings:
        raise TypeError("critical action policy requires exact V2Settings")
    enabled: set[CriticalActionKind] = set()
    if settings.cloudbeds_writes_enabled:
        enabled.add(CriticalActionKind.RESERVE_LODGING)
    if settings.bokun_writes_enabled:
        enabled.add(CriticalActionKind.BOOK_ACTIVITY)
    if settings.cloudbeds_writes_enabled and settings.bokun_writes_enabled:
        enabled.add(CriticalActionKind.BOOK_PACKAGE)
    if settings.enabled_payment_methods:
        enabled.add(CriticalActionKind.INITIATE_PAYMENT)
    return CriticalActionPolicy(
        frozenset(enabled),
        enabled_payment_methods=frozenset(settings.enabled_payment_methods),
        activity_participant_limit=6,
        valid_until=settings.write_window_end,
        kill_switch_engaged=settings.global_kill_switch_engaged,
    )


def _inbox_turn_budget(settings: V2Settings) -> timedelta:
    if type(settings) is not V2Settings:
        raise TypeError("inbox turn budget requires exact V2Settings")
    return timedelta(seconds=(settings.hermes_timeout_seconds * 6) + 30)


class ControlledEffectGuard:
    """Re-evaluate the immutable kill-switch/window contract for every claim."""

    def __init__(self, *, settings: V2Settings, clock: UTCClock) -> None:
        if type(settings) is not V2Settings or type(clock) is not UTCClock:
            raise TypeError("controlled effect guard requires exact settings and clock")
        self._settings = settings
        self._clock = clock

    def allows_workflow(self, workflow_id: str) -> bool:
        if type(workflow_id) is not str or not workflow_id:
            return False
        return (
            self._settings.runtime_mode
            in {RuntimeMode.CONTROLLED_WRITE, RuntimeMode.GENERAL_AVAILABILITY}
            and self._settings.write_window_is_open(now=self._clock.now())
        )


class _ClosedInstructionAdapter:
    def instruction(self, obligation: object) -> object:
        raise RuntimeError("non-Stripe payment initiation is closed")


class _ClosedStripeAdapter:
    def create_link(self, obligation: object) -> object:
        raise RuntimeError("Stripe payment initiation is closed")


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
        lead_resolver: DurableLeadResolver | None = None,
    ) -> None:
        if type(container) is not V2Container or container.role is not V2Role.WORKER:
            raise TypeError("reconciliation requires an exact worker container")
        if container.execution is None or container.followup is None:
            raise ValueError("reconciliation durable owners are unavailable")
        self._container = container
        self._reservation = Reconciler(container.execution)
        self._payment = PaymentReconciler(store=container.followup)
        if (reads is None) != (settings is None):
            raise ValueError("read probe requires both service and settings")
        self._reads = reads
        self._settings = settings
        self._cloudbeds_audit_transport = None
        if settings is not None and settings.cloudbeds_writes_enabled:
            self._cloudbeds_audit_transport = CloudbedsGETAuditTransport(
                api_key=settings.cloudbeds_api_key,
                property_id=settings.cloudbeds_property_id,
                base_url=settings.cloudbeds_base_url,
            )
        self._bokun_audit_transport = None
        if settings is not None and settings.bokun_writes_enabled:
            self._bokun_audit_transport = BokunGETAuditTransport(
                access_key=settings.bokun_access_key,
                secret_key=settings.bokun_secret_key,
                base_url=settings.bokun_base_url,
            )
        self._manual_handoff = None
        if settings is not None and settings.runtime_mode in {
            RuntimeMode.CONTROLLED_WRITE,
            RuntimeMode.GENERAL_AVAILABILITY,
        }:
            if (
                settings.runtime_mode is RuntimeMode.CONTROLLED_WRITE
                and len(settings.allowed_subscriber_ids) != 1
            ):
                raise ValueError(
                    "manual-review handoff requires one allowlisted subscriber"
                )
            if (
                settings.runtime_mode is RuntimeMode.GENERAL_AVAILABILITY
                and lead_resolver is None
            ):
                raise ValueError(
                    "general-availability handoff requires durable lead ownership"
                )
            self._manual_handoff = ManualReviewHandoffProjector(
                execution=container.execution,
                coordinator=HandoffCoordinator(store=container.followup),
                lead_id=(
                    f"manychat:{settings.allowed_subscriber_ids[0]}"
                    if settings.runtime_mode is RuntimeMode.CONTROLLED_WRITE
                    else ""
                ),
                lead_resolver=lead_resolver,
            )
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

    def _run_cloudbeds_audit(self, *, now: datetime) -> dict[str, object]:
        empty_projection = {"inserted": 0, "replayed": 0, "ignored": 0}
        if (
            self._cloudbeds_audit_transport is None
            or self._settings is None
            or self._container.execution is None
        ):
            return {
                "status": "closed",
                "projection": empty_projection,
                "observation": None,
            }
        audit_path = self._settings.sqlite_paths["cloudbeds_audit"]
        owner_paths = tuple(
            path
            for name, path in self._settings.sqlite_paths.items()
            if name != "cloudbeds_audit" and path.exists()
        )
        if audit_path.exists():
            try:
                audit_info = audit_path.stat()
                aliased = audit_info.st_nlink != 1 or any(
                    audit_path.samefile(owner_path) for owner_path in owner_paths
                )
            except OSError:
                return {
                    "status": "degraded",
                    "projection": empty_projection,
                    "observation": {"status": "failed"},
                }
            if aliased:
                return {
                    "status": "degraded",
                    "projection": empty_projection,
                    "observation": {"status": "failed"},
                }
        store: SQLiteCloudbedsAuditStore | None = None
        projection_payload = empty_projection
        try:
            store = SQLiteCloudbedsAuditStore(audit_path)
            projection = CloudbedsAuditProjector(
                execution=self._container.execution,
                audit_store=store,
                property_id=self._settings.cloudbeds_property_id,
                max_attempts=3,
            ).run_once()
            projection_payload = {
                "inserted": projection.inserted,
                "replayed": projection.replayed,
                "ignored": projection.ignored,
            }
            observation = CloudbedsAuditWorker(
                store=store,
                port=self._cloudbeds_audit_transport,
                worker_id="worker:cloudbeds-audit",
                lease_ttl=timedelta(seconds=30),
            ).run_once(now=now)
        except Exception:
            return {
                "status": "degraded",
                "projection": projection_payload,
                "observation": {"status": "failed"},
            }
        finally:
            if store is not None:
                store.close()
        terminal_degradation = observation is not None and observation.status in {
            CloudbedsAuditStatus.DIVERGENT,
            CloudbedsAuditStatus.ATTEMPTS_EXHAUSTED,
        }
        return {
            "status": "degraded" if terminal_degradation else "ok",
            "projection": projection_payload,
            "observation": (
                None
                if observation is None
                else {
                    "status": observation.status.value,
                    "attempts": observation.attempts,
                }
            ),
        }

    def _run_bokun_audit(self, *, now: datetime) -> dict[str, object]:
        empty_projection = {"inserted": 0, "replayed": 0, "ignored": 0}
        if (
            self._bokun_audit_transport is None
            or self._settings is None
            or self._container.execution is None
        ):
            return {
                "status": "closed",
                "projection": empty_projection,
                "observation": None,
            }
        audit_path = self._settings.sqlite_paths["bokun_audit"]
        owner_paths = tuple(
            path
            for name, path in self._settings.sqlite_paths.items()
            if name != "bokun_audit" and path.exists()
        )
        if audit_path.exists():
            try:
                info = audit_path.stat()
                aliased = info.st_nlink != 1 or any(
                    audit_path.samefile(owner_path) for owner_path in owner_paths
                )
            except OSError:
                aliased = True
            if aliased:
                return {
                    "status": "degraded",
                    "projection": empty_projection,
                    "observation": {"status": "failed"},
                }
        store: SQLiteBokunAuditStore | None = None
        projection_payload = empty_projection
        try:
            store = SQLiteBokunAuditStore(audit_path)
            projection = BokunAuditProjector(
                execution=self._container.execution,
                audit_store=store,
                max_attempts=3,
            ).run_once()
            projection_payload = {
                "inserted": projection.inserted,
                "replayed": projection.replayed,
                "ignored": projection.ignored,
            }
            observation = BokunAuditWorker(
                store=store,
                port=self._bokun_audit_transport,
                worker_id="worker:bokun-audit",
                lease_ttl=timedelta(seconds=30),
            ).run_once(now=now)
        except Exception:
            return {
                "status": "degraded",
                "projection": projection_payload,
                "observation": {"status": "failed"},
            }
        finally:
            if store is not None:
                store.close()
        terminal_degradation = observation is not None and observation.status in {
            BokunAuditStatus.DIVERGENT,
            BokunAuditStatus.ATTEMPTS_EXHAUSTED,
        }
        return {
            "status": "degraded" if terminal_degradation else "ok",
            "projection": projection_payload,
            "observation": (
                None
                if observation is None
                else {
                    "status": observation.status.value,
                    "attempts": observation.attempts,
                }
            ),
        }

    def run_once(self, *, now: datetime) -> dict[str, object] | WorkerHealthResult:
        reservation = self._reservation.run_once(now=now)
        manual_handoff = (
            None
            if self._manual_handoff is None
            else self._manual_handoff.run_once(now=now)
        )
        cloudbeds_audit = self._run_cloudbeds_audit(now=now)
        bokun_audit = self._run_bokun_audit(now=now)
        payload = {
            "status": "ok",
            "provider_reads": self._probe_reads(now=now),
            "reservation": reservation,
            "manual_handoff": manual_handoff,
            "payment": self._payment.run_once(now=now),
            "cloudbeds_audit": cloudbeds_audit,
            "bokun_audit": bokun_audit,
        }
        for audit, reasons in (
            (
                cloudbeds_audit,
                {
                    "divergent": WorkerFailureReason.CLOUDBEDS_AUDIT_DIVERGENT,
                    "attempts_exhausted": (
                        WorkerFailureReason.CLOUDBEDS_AUDIT_ATTEMPTS_EXHAUSTED
                    ),
                    "failed": WorkerFailureReason.CLOUDBEDS_AUDIT_UNAVAILABLE,
                },
            ),
            (
                bokun_audit,
                {
                    "divergent": WorkerFailureReason.BOKUN_AUDIT_DIVERGENT,
                    "attempts_exhausted": (
                        WorkerFailureReason.BOKUN_AUDIT_ATTEMPTS_EXHAUSTED
                    ),
                    "failed": WorkerFailureReason.BOKUN_AUDIT_UNAVAILABLE,
                },
            ),
        ):
            if audit["status"] != "degraded":
                continue
            observation = audit.get("observation")
            observed_status = (
                observation.get("status")
                if type(observation) is dict
                else "failed"
            )
            return WorkerHealthResult.degraded(
                reason=reasons.get(observed_status, reasons["failed"]),
                result=payload,
            )
        return payload


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
            quote_checkout_enabled=settings.bokun_writes_enabled,
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
    clock = UTCClock()
    if settings.runtime_mode is RuntimeMode.GENERAL_AVAILABILITY:
        authority: object = GeneralAvailabilityPublicAuthorityResolver(
            store=container.boundary,
            hmac_key=settings.public_authority_hmac_key,
        )
    else:
        if settings.public_authority_manifest_path is None:
            raise ValueError("shadow public authority manifest is unavailable")
        authority = ManifestPublicAuthorityResolver(
            store=container.boundary,
            manifest_path=settings.public_authority_manifest_path,
            hmac_key=settings.public_authority_hmac_key,
            now=clock.now(),
        )
    container.register_public_authority_resolver(authority)
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
    turn_budget = _inbox_turn_budget(settings)
    executor = V2TurnExecutor(
        store=container.boundary,
        model=model,
        reads=reads,
        profile=profile,
        private_customer_facts=container.private_customer,
        reducer=V2ConversationReducer(
            approval_ttl=timedelta(
                seconds=settings.critical_approval_ttl_seconds
            ),
            agency_payment_percentage=settings.agency_payment_percentage,
            hostel_payment_percentage=settings.hostel_payment_percentage,
            critical_action_policy=_critical_action_policy(settings),
        ),
        public_authority=authority,
        clock=clock,
        locale="pt-BR",
        turn_timeout=turn_budget,
        max_commit_attempts=2,
        ops_recorder=container.ops_recorder,
        ops_full_content=settings.ops_trace_full_content,
    )
    return InboxTurnWorker(
        inbox=container.inbox,
        executor=executor,
        quiet_window=timedelta(milliseconds=750),
        lease_ttl=turn_budget + timedelta(seconds=15),
        ops_recorder=container.ops_recorder,
        ops_full_content=settings.ops_trace_full_content,
    )


def _build_reservation_worker(
    *,
    container: V2Container,
    settings: V2Settings,
    effect_trace_resolver: DurableLeadResolver | None = None,
) -> V2ReservationWorker:
    if container.execution is None:
        raise ValueError("reservation execution owner is unavailable")
    if not (settings.cloudbeds_writes_enabled or settings.bokun_writes_enabled):
        raise ValueError("reservation worker requires an explicit provider gate")
    clock = UTCClock()
    adapters: list[V2ReservationExecutionAdapter] = []
    if settings.cloudbeds_writes_enabled:
        cloudbeds_transport = CloudbedsHTTPTransport(
            api_key=settings.cloudbeds_api_key,
            property_id=settings.cloudbeds_property_id,
            source_id=settings.cloudbeds_source_id,
            base_url=settings.cloudbeds_base_url,
        )
        cloudbeds_read_port = CloudbedsReadAdapter(
            transport=cloudbeds_transport,
            clock=clock,
            ttl=timedelta(minutes=5),
        )
        adapters.append(
            V2ReservationExecutionAdapter(
                provider="cloudbeds",
                port=CloudbedsReservationPort(cloudbeds_transport),
                authorization=ProviderWriteAuthorization(
                    provider="cloudbeds",
                    enabled=True,
                    authorization_id=(
                        "authorization-v2-lodging-" + settings.candidate_git_sha[:16]
                    ),
                ),
                binding_resolver=PrivateOfferBindingResolver(
                    {ServiceKind.LODGING: cloudbeds_read_port}
                ),
                clock=clock,
                ops_recorder=container.ops_recorder,
                effect_trace_resolver=effect_trace_resolver,
                ops_full_content=settings.ops_trace_full_content,
            )
        )
    if settings.bokun_writes_enabled:
        bokun_transport = BokunHTTPTransport(
            access_key=settings.bokun_access_key,
            secret_key=settings.bokun_secret_key,
            product_map=settings.bokun_product_map,
            base_url=settings.bokun_base_url,
            quote_checkout_enabled=True,
        )
        bokun_read_port = BokunReadAdapter(
            transport=bokun_transport,
            clock=clock,
            ttl=timedelta(minutes=5),
        )
        adapters.append(
            V2ReservationExecutionAdapter(
                provider="bokun",
                port=BokunReservationPort(bokun_transport),
                authorization=ProviderWriteAuthorization(
                    provider="bokun",
                    enabled=True,
                    authorization_id=(
                        "authorization-v2-activity-" + settings.candidate_git_sha[:16]
                    ),
                ),
                binding_resolver=PrivateOfferBindingResolver(
                    {ServiceKind.ACTIVITY: bokun_read_port}
                ),
                clock=clock,
                ops_recorder=container.ops_recorder,
                effect_trace_resolver=effect_trace_resolver,
                ops_full_content=settings.ops_trace_full_content,
            )
        )
    return V2ReservationWorker(
        store=container.execution,
        adapters=tuple(adapters),
        effect_guard=ControlledEffectGuard(settings=settings, clock=clock),
        worker_id="worker:reservation",
        lease_ttl=timedelta(seconds=30),
    )


def _build_payment_worker(
    *,
    container: V2Container,
    settings: V2Settings,
    lead_resolver: DurableLeadResolver | None = None,
    effect_trace_resolver: DurableLeadResolver | None = None,
) -> PaymentInitiationWorker:
    if container.payment_initiation is None:
        raise ValueError("payment initiation owner is unavailable")
    if not settings.enabled_payment_methods:
        raise ValueError("payment worker requires at least one explicit method gate")
    if (
        settings.runtime_mode is RuntimeMode.CONTROLLED_WRITE
        and len(settings.allowed_subscriber_ids) != 1
    ):
        raise ValueError("controlled payment worker requires one allowlisted subscriber")
    if (
        settings.runtime_mode is RuntimeMode.GENERAL_AVAILABILITY
        and lead_resolver is None
    ):
        raise ValueError("general-availability payment worker requires durable lead ownership")
    clock = UTCClock()
    profiles = {
        BusinessUnit.HOSTEL: settings.stripe_account_profiles["hostel"],
        BusinessUnit.AGENCY: settings.stripe_account_profiles["agency"],
    }
    percentages_by_unit = {
        BusinessUnit.HOSTEL: settings.hostel_payment_percentage,
        BusinessUnit.AGENCY: settings.agency_payment_percentage,
    }
    effect_guard = ControlledEffectGuard(settings=settings, clock=clock)
    stripe_reconciler: object | None = None
    if settings.stripe_links_enabled:
        stripe: object = StripeLinkAdapter(
            transport=StripeTestHTTPTransport(
                secret_keys=settings.stripe_test_secret_keys,
                base_url=settings.stripe_base_url,
                journal=container.payment_initiation,
                clock=clock.now,
                effect_guard=effect_guard,
            ),
            account_profiles=profiles,
            enabled=True,
            subscriber_id=(
                settings.allowed_subscriber_ids[0]
                if settings.runtime_mode is RuntimeMode.CONTROLLED_WRITE
                else ""
            ),
            payment_percentages=percentages_by_unit,
        )
        stripe_reconciler = StripeLinkReconciliationAdapter(
            transport=StripeTestReconciliationTransport(
                secret_keys=settings.stripe_test_secret_keys,
                base_url=settings.stripe_base_url,
            ),
            account_profiles=profiles,
            subscriber_id=(
                settings.allowed_subscriber_ids[0]
                if settings.runtime_mode is RuntimeMode.CONTROLLED_WRITE
                else ""
            ),
            payment_percentages=percentages_by_unit,
        )
    else:
        stripe = _ClosedStripeAdapter()

    wise: object = _ClosedInstructionAdapter()
    pix: object = _ClosedInstructionAdapter()
    if settings.wise_instructions_enabled or settings.pix_instructions_enabled:
        if settings.payment_instruction_path is None:
            raise ValueError("payment instruction catalog is unavailable")
        catalog = FilePaymentInstructionCatalog(
            path=settings.payment_instruction_path,
            receiver_profiles=profiles,
        )
        percentages_by_profile = {
            profiles[unit]: percentage
            for unit, percentage in percentages_by_unit.items()
        }
        if settings.wise_instructions_enabled:
            wise = WiseInstructionAdapter(
                instructions=catalog.wise_instructions(),
                payment_percentages=percentages_by_profile,
            )
        if settings.pix_instructions_enabled:
            pix = PixInstructionAdapter(
                knowledge=catalog,
                receiver_profiles=tuple(profiles.values()),
                payment_percentages=percentages_by_profile,
            )
    return PaymentInitiationWorker(
        store=container.payment_initiation,
        payments=PaymentService(stripe=stripe, wise=wise, pix=pix),
        worker_id="worker:payment-initiation",
        lease_ttl=timedelta(seconds=30),
        effect_guard=effect_guard,
        stripe_reconciler=stripe_reconciler,
        lead_resolver=lead_resolver,
        ops_recorder=container.ops_recorder,
        effect_trace_resolver=effect_trace_resolver,
        ops_full_content=settings.ops_trace_full_content,
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
    lead_resolver = (
        DurableLeadResolver(
            boundary=container.boundary,
            execution=container.execution,
            followup=container.followup,
        )
        if settings.runtime_mode is RuntimeMode.GENERAL_AVAILABILITY
        else None
    )
    effect_trace_resolver = (
        DurableLeadResolver(
            boundary=container.boundary,
            execution=container.execution,
            followup=container.followup,
        )
        if settings.ops_trace_path is not None
        else None
    )
    inbox_worker: object
    if settings.runtime_mode in {
        RuntimeMode.SHADOW,
        RuntimeMode.CONTROLLED_WRITE,
        RuntimeMode.GENERAL_AVAILABILITY,
    }:
        inbox_worker = _build_inbox_worker(
            container=container,
            settings=settings,
            reads=reads,
        )
    else:
        inbox_worker = ClosedCapabilityWorker("inbox_turns")
    if (
        container.boundary is None
        or container.execution is None
        or container.followup is None
    ):
        raise ValueError("boundary relay durable owners are unavailable")
    boundary_relay = BoundaryRelayWorker(
        boundary=SQLiteBoundaryWorkerStore(container.boundary),
        reservation_target=container.execution,
        handoff_target=container.followup,
        worker_id="worker:boundary-relay",
        lease_ttl=timedelta(seconds=30),
    )
    reservation_enabled = (
        settings.cloudbeds_writes_enabled or settings.bokun_writes_enabled
    )
    reservation_worker: object = (
        _build_reservation_worker(
            container=container,
            settings=settings,
            effect_trace_resolver=effect_trace_resolver,
        )
        if reservation_enabled
        else ClosedCapabilityWorker("reservation_writes")
    )
    payment_enabled = bool(settings.enabled_payment_methods)
    completion_enabled = bool(settings.allowed_subscriber_ids) or (
        settings.runtime_mode is RuntimeMode.GENERAL_AVAILABILITY
    )
    payment_worker: object = (
        _build_payment_worker(
            container=container,
            settings=settings,
            lead_resolver=lead_resolver,
            effect_trace_resolver=effect_trace_resolver,
        )
        if payment_enabled
        else ClosedCapabilityWorker("payment_initiation")
    )
    outcome_projector: object = (
        ReservationOutcomeProjector(
            execution=container.execution,
            payment_store=container.payment_initiation,
            receiver_profiles={
                BusinessUnit.HOSTEL: settings.stripe_account_profiles["hostel"],
                BusinessUnit.AGENCY: settings.stripe_account_profiles["agency"],
            },
            enabled_methods=tuple(
                PaymentMethod(value) for value in settings.enabled_payment_methods
            ),
        )
        if payment_enabled
        else ClosedCapabilityWorker("outcome_projector")
    )
    completion_projector: object = (
        CompletionProjector(
            execution=container.execution,
            payment_store=container.payment_initiation,
            public_store=container.public_outbox,
            account_profiles=(
                {
                    BusinessUnit.HOSTEL: settings.stripe_account_profiles["hostel"],
                    BusinessUnit.AGENCY: settings.stripe_account_profiles["agency"],
                }
                if payment_enabled
                else None
            ),
            subscriber_id=(
                settings.allowed_subscriber_ids[0]
                if settings.runtime_mode is RuntimeMode.CONTROLLED_WRITE
                else ""
            ),
            lead_resolver=lead_resolver,
            include_payment_offers=payment_enabled,
        )
        if completion_enabled
        else ClosedCapabilityWorker("completion_projector")
    )
    public_delivery: object
    if settings.manychat_delivery_enabled:
        manychat_transport = ManyChatHTTPTransport(
            api_key=settings.manychat_api_key,
            base_url=settings.manychat_base_url,
        )
        public_delivery = CombinedPublicDeliveryWorker(
            boundary=SQLiteBoundaryWorkerStore(container.boundary),
            completion=container.public_outbox,
            delivery=ManyChatFlowDeliveryAdapter(
                transport=manychat_transport,
                allowed_subscriber_id=(
                    settings.allowed_subscriber_ids[0]
                    if settings.runtime_mode is RuntimeMode.CONTROLLED_WRITE
                    else None
                ),
                reply_field_id=settings.manychat_reply_field_id,
                reply_flow_ns=settings.manychat_reply_flow_ns,
                payment_link_field_id=settings.manychat_payment_link_field_id,
                payment_description_field_id=(
                    settings.manychat_payment_description_field_id
                ),
                payment_flow_ns=settings.manychat_payment_flow_ns,
            ),
            effect_guard=ControlledEffectGuard(settings=settings, clock=UTCClock()),
            worker_id="worker:manychat-public",
            lease_ttl=timedelta(seconds=30),
            ops_recorder=container.ops_recorder,
            effect_trace_resolver=effect_trace_resolver,
            ops_full_content=settings.ops_trace_full_content,
        )
    else:
        public_delivery = ClosedCapabilityWorker("manychat_delivery")
    handoff_worker: object
    if settings.manychat_handoff_enabled:
        handoff_transport = ManyChatHTTPTransport(
            api_key=settings.manychat_api_key,
            base_url=settings.manychat_base_url,
        )
        handoff_worker = HandoffOutboxWorker(
            store=container.followup,
            delivery=ManyChatHandoffDeliveryAdapter(
                transport=handoff_transport,
                tag_id=settings.manychat_handoff_tag_id,
                flow_ns=settings.manychat_handoff_flow_ns,
                clock=UTCClock(),
                subscriber_id=(
                    settings.allowed_subscriber_ids[0]
                    if settings.runtime_mode is RuntimeMode.CONTROLLED_WRITE
                    else ""
                ),
                lead_resolver=lead_resolver,
            ),
            worker_id="worker:manychat-handoff",
            lease_ttl=timedelta(seconds=30),
            effect_guard=ControlledEffectGuard(settings=settings, clock=UTCClock()),
        )
    else:
        handoff_worker = ClosedCapabilityWorker("manychat_handoff")
    workers: dict[WorkerQueue, object] = {
        WorkerQueue.INBOX: inbox_worker,
        WorkerQueue.BOUNDARY_RELAY: boundary_relay,
        WorkerQueue.RESERVATION: reservation_worker,
        WorkerQueue.HANDOFF: handoff_worker,
        WorkerQueue.OUTCOME_PROJECTOR: outcome_projector,
        WorkerQueue.PAYMENT_INITIATION: payment_worker,
        WorkerQueue.SETTLEMENT: ClosedCapabilityWorker("settlement_writes"),
        WorkerQueue.POST_PAYMENT: completion_projector,
        WorkerQueue.PUBLIC_DELIVERY: public_delivery,
        WorkerQueue.RECONCILIATION: ReconciliationStage(
            container=container,
            reads=reads,
            settings=settings,
            lead_resolver=lead_resolver,
        ),
    }
    controlled_ingress_status = "closed"
    if settings.runtime_mode in {
        RuntimeMode.CONTROLLED_WRITE,
        RuntimeMode.GENERAL_AVAILABILITY,
    }:
        controlled_ingress_status = (
            "ready"
            if container.controlled_public_ingress_reason(now=UTCClock().now()) is None
            else "degraded"
        )
    container.register_runtime_capabilities(
        {
            "bokun_reads": "ready",
            "cloudbeds_reads": "ready",
            "knowledge_reads": (
                "ready" if settings.knowledge_base_path is not None else "closed"
            ),
            "hermes_model": (
                "ready"
                if settings.runtime_mode
                in {
                    RuntimeMode.SHADOW,
                    RuntimeMode.CONTROLLED_WRITE,
                    RuntimeMode.GENERAL_AVAILABILITY,
                }
                else "closed"
            ),
            "inbox_turns": (
                "ready"
                if settings.runtime_mode
                in {
                    RuntimeMode.SHADOW,
                    RuntimeMode.CONTROLLED_WRITE,
                    RuntimeMode.GENERAL_AVAILABILITY,
                }
                else "closed"
            ),
            "manychat_profile": (
                "ready"
                if settings.runtime_mode
                in {
                    RuntimeMode.SHADOW,
                    RuntimeMode.CONTROLLED_WRITE,
                    RuntimeMode.GENERAL_AVAILABILITY,
                }
                else "closed"
            ),
            "boundary_relay": "ready",
            "controlled_public_ingress": controlled_ingress_status,
            "manychat_delivery": (
                "ready" if settings.manychat_delivery_enabled else "closed"
            ),
            "manychat_handoff": (
                "ready" if settings.manychat_handoff_enabled else "closed"
            ),
            "payment_initiation": (
                "ready" if payment_enabled else "closed"
            ),
            "outcome_projector": (
                "ready" if payment_enabled else "closed"
            ),
            "completion_projector": (
                "ready" if completion_enabled else "closed"
            ),
            "stripe_test_links": (
                "ready" if settings.stripe_links_enabled else "closed"
            ),
            "wise_instructions": (
                "ready" if settings.wise_instructions_enabled else "closed"
            ),
            "pix_instructions": (
                "ready" if settings.pix_instructions_enabled else "closed"
            ),
            "reservation_writes": (
                "ready"
                if settings.cloudbeds_writes_enabled or settings.bokun_writes_enabled
                else "closed"
            ),
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
