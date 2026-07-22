"""Phase 8 one-transaction boundary commit across every child row family."""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta, timezone
import hashlib
import json
import unittest

from reservation_domain import dumps_command
from reservation_boundary.conversation import (
    MayaIntentClosure,
    MayaTurnClosure,
    MayaTurnProposal,
    PublicReplyChunk,
    PublicReplyType,
    PublicRoute,
    SourceEventIdentity,
    TranscriptCommitment,
    TranscriptDirection,
    TranscriptKind,
)
from reservation_boundary.serialization import semantic_hash, to_wire_json
from reservation_boundary.sqlite_store import IdentityConflict, SQLiteBoundaryStore, TurnReceipt
from reservation_boundary.types import BoundaryCommit, ConversationIntentKind, KernelDecision
from tests.test_phase7_sqlite_store import queued_import


T0 = datetime(2026, 7, 22, 19, 30, tzinfo=timezone.utc)
TABLES = (
    "boundary_state", "boundary_events", "boundary_event_sources",
    "boundary_turn_artifacts", "boundary_commands", "boundary_command_relays",
    "boundary_outbox", "boundary_public_outbox", "boundary_dispatch_authority",
    "legacy_import_claims", "decision_comparisons",
)


def _canonical(schema: str, data: dict[str, object]) -> bytes:
    return json.dumps(
        {"schema": schema, "version": 1, "data": data},
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode()


def _domain_hash(domain: str, payload: bytes) -> str:
    return hashlib.sha256(domain.encode() + b"\x00" + payload).hexdigest()


class Phase8BoundaryAtomicCommitTests(unittest.TestCase):
    def setUp(self) -> None:
        self.store = SQLiteBoundaryStore.open_memory_v8()
        self.addCleanup(self.store.close)

    def _type(self, name: str):
        from reservation_boundary import sqlite_store

        value = getattr(sqlite_store, name, None)
        self.assertIsNotNone(value, f"{name} must be owned by the v8 store")
        return value

    def _install_conversation_allocation(self) -> None:
        instant = T0.isoformat()
        common = (
            "authorization-1", "contact-1", "whatsapp", 1,
            "conversation_test", None, None, "c" * 64, "d" * 64,
            "8" * 64, "e" * 64, "f" * 64,
        )
        self.store._connection.execute(
            "INSERT INTO boundary_dispatch_authority "
            "(authorization_id,scope_subject_id,channel_scope,generation,allocation_id,"
            "row_kind,authorization_kind,qualification_id,scenario_id,contract_digest,"
            "effect_authorization_binding_digest,capability_policy_digest,target_binding_hash,"
            "allowed_chunk_ordinal,allocation_manifest_hash,state,public_row_id,cas_revision,"
            "closure_receipt_hash,created_at,updated_at,fenced_at) "
            "VALUES (?,?,?,?,?,'generation_header',?,?,?,?,?,?,?,?,?,'open',NULL,0,NULL,?,?,NULL)",
            common[:4] + ("__header__",) + common[4:11] + (None, common[11], instant, instant),
        )
        self.store._connection.execute(
            "INSERT INTO boundary_dispatch_authority "
            "(authorization_id,scope_subject_id,channel_scope,generation,allocation_id,"
            "row_kind,authorization_kind,qualification_id,scenario_id,contract_digest,"
            "effect_authorization_binding_digest,capability_policy_digest,target_binding_hash,"
            "allowed_chunk_ordinal,allocation_manifest_hash,state,public_row_id,cas_revision,"
            "closure_receipt_hash,created_at,updated_at,fenced_at) "
            "VALUES (?,?,?,?,?,'allocation',?,?,?,?,?,?,?,?,?,'available',NULL,0,NULL,?,?,NULL)",
            common[:4] + ("allocation-1",) + common[4:11] + (0, common[11], instant, instant),
        )

    def _case(self):
        source, imported, command = queued_import()
        self.store.import_genesis(source, imported, claimed_at=T0)
        current, token = self.store.acquire_fence(source.raw_fields["lead_key"])
        next_state = replace(
            current.state,
            version=1,
            processed_event_ids=("event-source-1",),
        )
        commit = BoundaryCommit(next_state, (command,), (), ())
        decision = KernelDecision(next_state, (command,), (), (), ())

        intent = MayaIntentClosure(
            ConversationIntentKind.REQUEST_HANDOFF,
            None,
            None,
            True,
        )
        closure = MayaTurnClosure(
            "turn-atomic-1",
            intent,
            "Vou chamar uma pessoa.",
            PublicRoute.HANDOFF,
            PublicReplyType.HANDOFF,
            1,
            "5" * 64,
            "session-atomic-1",
            True,
        )
        chunk = PublicReplyChunk(
            "turn-atomic-1",
            0,
            closure.public_text,
            closure.canonical_hash(),
        )
        final_frame = TranscriptCommitment(
            TranscriptDirection.CHILD_TO_PARENT,
            TranscriptKind.FINAL,
            1,
            "final-request-1",
            "2" * 64,
            "3" * 64,
            "4" * 64,
        )
        maya_proposal = MayaTurnProposal.from_accepted_closure(
            accepted_closure=closure,
            read_observations=(),
            facts=(),
            normalized_tool_proposals=(),
            learning_proposals=(),
            public_reply_chunks=(chunk,),
            final_transcript_commitment_hash=final_frame.canonical_hash(),
            final_transcript_mac="6" * 64,
            runtime_graph_digest="7" * 64,
        )
        self.maya_proposal = maya_proposal
        maya_bytes = maya_proposal.to_canonical_bytes()
        maya_hash = maya_proposal.canonical_hash()
        decision_bytes = to_wire_json(decision).encode()
        decision_hash = semantic_hash(decision)
        artifact_type = self._type("TurnArtifactWrite")
        artifacts = (
            artifact_type(
                "frame-1", "frame_commitment", 1, final_frame.canonical_hash(),
                final_frame.to_canonical_bytes(), final_frame.canonical_hash(),
            ),
            artifact_type(
                "closure-1", "maya_closure", None, None,
                closure.to_canonical_bytes(), closure.canonical_hash(),
            ),
            artifact_type(
                "maya-proposal-1", "maya_proposal", None, None,
                maya_bytes, maya_hash,
            ),
            artifact_type(
                "kernel-decision-1", "kernel_decision", None, maya_hash,
                decision_bytes, decision_hash,
            ),
        )

        relay_bytes = _canonical("phase8-reservation-relay-bundle", {"command": command.command_id})
        relay_hash = _domain_hash("phase8-reservation-relay-bundle-v1", relay_bytes)
        relay_type = self._type("CommandRelayWrite")
        relays = (
            relay_type("relay-1", command.command_id, relay_bytes, relay_hash),
        )

        internal_bytes = _canonical("phase8-learning-proposal", {"claim": "sanitized"})
        internal_hash = _domain_hash("phase8-learning-proposal-v1", internal_bytes)
        internal_type = self._type("InternalOutboxWrite")
        internal = (
            internal_type(
                "job-1", "learning_proposal", internal_bytes, internal_hash,
                None, None, "operation-1",
            ),
        )

        public_type = self._type("PublicOutboxWrite")
        public = (
            public_type(
                public_row_id="public-row-1",
                chunk=chunk,
                idempotency_key="public-idempotency-1",
                target_binding_hash="e" * 64,
                channel_id="manychat-account-1",
                channel_scope="whatsapp",
                authorization_kind="conversation_test",
                authorization_id="authorization-1",
                scope_subject_id="contact-1",
                allocation_id="allocation-1",
                immutable_generation=1,
                qualification_id=None,
                scenario_id=None,
                capability_policy_digest="8" * 64,
                effect_authorization_binding_digest="d" * 64,
                effective_turn_binding_digest="9" * 64,
                deadline_at=T0 + timedelta(minutes=5),
            ),
        )

        command_json = dumps_command(command)
        command_hash = hashlib.sha256(command_json.encode()).hexdigest()
        receipt = TurnReceipt.create(
            aggregate_turn_id="turn-atomic-1",
            event_hash="0" * 64,
            source_events=(SourceEventIdentity("event-source-1", "2" * 64),),
            maya_proposal_hash=maya_hash,
            kernel_decision_hash=decision_hash,
            read_observations=(),
            committed_state_version=1,
            committed_state_hash=semantic_hash(next_state),
            public_chunks=((
                "public-row-1", 0, chunk.to_canonical_bytes(), chunk.canonical_hash(),
            ),),
            command_rows=((command.command_id, command_hash),),
            relay_rows=(("relay-1", relay_hash),),
            internal_outbox_rows=(("job-1", internal_hash),),
            uds_transcript_mac="6" * 64,
            uds_final_seq=1,
            structural_graph_digest="7" * 64,
            capability_policy_digest="8" * 64,
            effective_stage_binding_digest="9" * 64,
            behavior_state_snapshot_digest="a" * 64,
            qualification_id=None,
            admission_sequence=None,
            admission_revision=None,
            commit_fence_token=None,
            allocation_manifest_hash=None,
            immutable_generation=None,
            allocation_ids=None,
            committed_at=T0,
            previous_turn_receipt_hash=None,
        )
        self._install_conversation_allocation()
        return current, token, commit, receipt, artifacts, relays, internal, public

    def _commit(self):
        current, token, commit, receipt, artifacts, relays, internal, public = self._case()
        method = getattr(self.store, "commit_turn_v8", None)
        self.assertIsNotNone(method, "commit_turn_v8 must own the atomic write")
        return method(
            expected_version=current.version,
            fencing_token=token,
            commit=commit,
            receipt=receipt,
            artifacts=artifacts,
            command_relays=relays,
            internal_jobs=internal,
            public_rows=public,
            committed_at=T0,
        ), receipt, commit, artifacts

    def test_turn_commit_persists_all_row_families_and_allocation_cas_atomically(self) -> None:
        persisted, receipt, commit, _ = self._commit()
        self.assertEqual(persisted, receipt)
        counts = tuple(
            self.store._connection.execute(f"SELECT count(*) FROM {table}").fetchone()[0]
            for table in TABLES
        )
        self.assertEqual(counts, (1, 1, 1, 4, 1, 1, 1, 1, 2, 1, 0))
        self.assertEqual(self.store.load_state(commit.state.lead_key).state, commit.state)
        authority = self.store._connection.execute(
            "SELECT state,public_row_id,cas_revision FROM boundary_dispatch_authority "
            "WHERE allocation_id='allocation-1'"
        ).fetchone()
        self.assertEqual(authority, ("bound", "public-row-1", 1))
        event = self.store._connection.execute(
            "SELECT turn_receipt_json,turn_receipt_hash FROM boundary_events"
        ).fetchone()
        self.assertEqual(event, (receipt.to_canonical_bytes().decode(), receipt.artifact_hash))

    def test_all_child_rows_backlink_exact_final_receipt_hash(self) -> None:
        _, receipt, _, _ = self._commit()
        for table in (
            "boundary_event_sources", "boundary_turn_artifacts", "boundary_commands",
            "boundary_command_relays", "boundary_outbox", "boundary_public_outbox",
        ):
            with self.subTest(table=table):
                values = self.store._connection.execute(
                    f"SELECT DISTINCT source_turn_receipt_hash FROM {table}"
                ).fetchall()
                self.assertEqual(values, [(receipt.artifact_hash,)])

    def test_artifact_hash_excludes_receipt_backlink_but_receipt_binds_child_hashes(self) -> None:
        _, receipt, _, artifacts = self._commit()
        rows = self.store._connection.execute(
            "SELECT artifact_id,artifact_hash,source_turn_receipt_hash "
            "FROM boundary_turn_artifacts ORDER BY artifact_index"
        ).fetchall()
        self.assertEqual(
            rows,
            [
                (item.artifact_id, item.artifact_hash, receipt.artifact_hash)
                for item in artifacts
            ],
        )
        self.assertEqual(receipt.maya_proposal_hash, artifacts[2].artifact_hash)
        self.assertEqual(receipt.kernel_decision_hash, artifacts[3].artifact_hash)

    def test_commit_rejects_proposal_receipt_semantic_divergence(self) -> None:
        current, token, commit, receipt, artifacts, relays, internal, public = self._case()
        divergent = replace(self.maya_proposal, runtime_graph_digest="f" * 64)
        divergent_artifacts = tuple(
            replace(
                artifact,
                canonical_bytes=divergent.to_canonical_bytes(),
                artifact_hash=divergent.canonical_hash(),
            )
            if artifact.artifact_kind == "maya_proposal"
            else artifact
            for artifact in artifacts
        )
        divergent_receipt = replace(
            receipt,
            maya_proposal_hash=divergent.canonical_hash(),
            artifact_hash="",
        )
        with self.assertRaises(IdentityConflict):
            self.store.commit_turn_v8(
                expected_version=current.version,
                fencing_token=token,
                commit=commit,
                receipt=divergent_receipt,
                artifacts=divergent_artifacts,
                command_relays=relays,
                internal_jobs=internal,
                public_rows=public,
                committed_at=T0,
            )
        self.assertEqual(
            self.store.load_state(current.state.lead_key).state.version,
            0,
        )


if __name__ == "__main__":
    unittest.main()
