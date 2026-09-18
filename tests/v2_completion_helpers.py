"""Offline completion harness; fake model/identity, real ledger and boundary commits."""

from dataclasses import replace
from types import SimpleNamespace

from reservation_boundary.conversation import PublicReplyChunk
from reservation_boundary.sqlite_store import SQLiteBoundaryStore
from tests.test_v2_turn_executor import (
    FakeAuditedModel,
    FakeProfile,
    _executor,
    _proposal,
)
from v2_application.active_execution import ReservationExecutionStatusResolver
from v2_application.completion_projector import CompletionProjector
from v2_host.public_authority import GeneralAvailabilityPublicAuthorityResolver


class CompletionMaya(FakeAuditedModel):
    text = "Resultado redigido exclusivamente pela Maya neste teste."

    def __init__(self, store):
        super().__init__(store, [])

    def complete_audited(self, request):
        self.proposals = [
            replace(_proposal(self.text), source_event_id=request.source_event_id)
        ]
        return super().complete_audited(request)


def make_completion(
    tmp_path,
    execution,
    payments,
    public,
    *,
    lead_id="manychat:1873018537",
    include_payment_offers=True,
):
    boundary = SQLiteBoundaryStore.open_path_v8(
        tmp_path / "communication-boundary.sqlite3"
    )
    owner = SimpleNamespace(
        lead_id_for_command=lambda _: lead_id, lead_id_for_payment=lambda _: lead_id
    )
    projector = CompletionProjector(
        execution=execution,
        payment_store=payments,
        public_store=public,
        boundary=boundary,
        lead_resolver=owner,
        include_payment_offers=include_payment_offers,
    )
    executor = _executor(
        store=boundary,
        model=CompletionMaya(boundary),
        profile=FakeProfile(boundary),
        public_authority=GeneralAvailabilityPublicAuthorityResolver(
            store=boundary, hmac_key=b"c" * 32
        ),
    )
    executor._completion_projector = projector
    executor._execution_status_resolver = ReservationExecutionStatusResolver(
        execution, payment_store=payments, public_store=public, lead_resolver=owner
    )
    projector.executor = executor
    return projector


def authored_chunks(projector):
    return tuple(
        PublicReplyChunk.from_canonical_bytes(row[0].encode())
        for row in projector._boundary._connection.execute(
            "SELECT chunk_json FROM boundary_public_outbox ORDER BY created_at,aggregate_turn_id,chunk_index"
        )
    )
