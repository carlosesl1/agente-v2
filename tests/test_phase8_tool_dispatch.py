"""Phase 8 Task 2: proposal normalization is separate from authorization."""

from __future__ import annotations

import ast
from dataclasses import replace
import hashlib
import json
from pathlib import Path
import unittest

from reservation_boundary import conversation
from reservation_boundary import dispatch as dispatch_module
from reservation_boundary.dispatch import DispatchRejected, ToolDispatch
from reservation_boundary.serialization import (
    to_tool_arguments_canonical_json,
    to_wire_json,
)
from reservation_boundary.types import (
    ActivityReservationArguments,
    DecimalSlot,
    KernelDecision,
    LodgingPaymentArguments,
    LodgingReservationArguments,
    NormalizedMessage,
    StringSlot,
    ToolDispatchRequest,
    TypedFact,
    WiseVerificationArguments,
)
from tests.phase7_helpers import DEADLINE
from tests.test_phase7_dispatch import activity_queued_state, payment_boundary_state


ROOT = Path(__file__).resolve().parents[1]


def _lead_hash(lead_key: str) -> str:
    return hashlib.sha256(b"phase8-lead-key-v1\x00" + lead_key.encode("utf-8")).hexdigest()


def _turn_request(state, *, lead_key: str = "lead-synthetic-001"):
    state_bytes = to_wire_json(state).encode("utf-8")
    return conversation.MayaTurnRequest(
        boundary_state_bytes=state_bytes,
        state_version=state.version,
        state_hash=hashlib.sha256(state_bytes).hexdigest(),
        normalized_message=NormalizedMessage("confirm", "en"),
        aggregate_turn_id="turn-001",
        source_events=(conversation.SourceEventIdentity("event-001", "1" * 64),),
        lead_key_hash=_lead_hash(lead_key),
        private_delivery_binding_hash="2" * 64,
        deadline_at=DEADLINE,
        behavior_profile_fingerprint="3" * 64,
    )


def _activity_request(state, command) -> ToolDispatchRequest:
    arguments = ActivityReservationArguments(
        command.payload.components[0].offer_id,
        command.draft_version,
        command.subject_signature,
    )
    return ToolDispatchRequest(
        "bokun_agendar_passeio_v2",
        arguments,
        state.lead_key,
        "turn-001",
        DEADLINE,
    )


def _request_hash(turn_request, request, *, request_id: str, sequence: int) -> str:
    payload = json.dumps(
        {
            "schema": "phase8-command-request-binding",
            "version": 1,
            "data": {
                "maya_turn_request_hash": turn_request.canonical_hash(),
                "request_id": request_id,
                "sequence": sequence,
                "tool_dispatch_request": json.loads(to_wire_json(request)),
            },
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    return hashlib.sha256(b"phase8-command-request-binding-v1\x00" + payload).hexdigest()


def _commitment(
    turn_request,
    request,
    *,
    request_id: str = "request-001",
    sequence: int = 1,
    direction=conversation.TranscriptDirection.CHILD_TO_PARENT,
    kind=conversation.TranscriptKind.COMMAND,
):
    return conversation.TranscriptCommitment(
        direction=direction,
        kind=kind,
        sequence=sequence,
        request_id=request_id,
        request_hash=_request_hash(
            turn_request,
            request,
            request_id=request_id,
            sequence=sequence,
        ),
        response_hash="4" * 64,
        previous_frame_commitment="5" * 64,
    )


def _normalize(dispatch: ToolDispatch, turn_request, request, commitment):
    method = getattr(dispatch, "normalize_proposal", None)
    if not callable(method):
        raise AssertionError("ToolDispatch.normalize_proposal must exist")
    return method(
        turn_request=turn_request,
        request=request,
        transcript_commitment=commitment,
    )


class Phase8ToolDispatchNormalizationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.dispatch = ToolDispatch()
        self.state, self.command = activity_queued_state()
        self.turn = _turn_request(self.state)
        self.request = _activity_request(self.state, self.command)
        self.commitment = _commitment(self.turn, self.request)

    def test_sequence_zero_is_rejected_by_both_proposal_types(self) -> None:
        frame_hash = "a" * 64
        claim = TypedFact("language", StringSlot("pt-BR"), frame_hash)
        cases = (
            lambda: conversation.LearningProposal(
                "turn-001",
                "request-001",
                0,
                claim,
                0,
                "b" * 64,
                "c" * 64,
                frame_hash,
            ),
            lambda: conversation.NormalizedToolProposal(
                "turn-001",
                "request-001",
                0,
                conversation.NormalizedCommandTool.ACTIVITY_RESERVATION,
                conversation.NormalizedCommandArgumentsType.ACTIVITY_RESERVATION,
                to_tool_arguments_canonical_json(self.request.arguments),
                "c" * 64,
                frame_hash,
            ),
        )
        for constructor in cases:
            with self.subTest(constructor=constructor), self.assertRaises((TypeError, ValueError)):
                constructor()

    def test_normalize_proposal_derives_exact_capability_free_proposal(self) -> None:
        proposal = _normalize(self.dispatch, self.turn, self.request, self.commitment)
        self.assertIs(type(proposal), conversation.NormalizedToolProposal)
        self.assertEqual(proposal.aggregate_turn_id, self.turn.aggregate_turn_id)
        self.assertEqual(proposal.request_id, self.commitment.request_id)
        self.assertEqual(proposal.sequence, self.commitment.sequence)
        self.assertIs(
            proposal.tool_name,
            conversation.NormalizedCommandTool.ACTIVITY_RESERVATION,
        )
        self.assertIs(
            proposal.arguments_type,
            conversation.NormalizedCommandArgumentsType.ACTIVITY_RESERVATION,
        )
        self.assertEqual(
            proposal.typed_arguments_json,
            to_tool_arguments_canonical_json(self.request.arguments),
        )
        self.assertEqual(proposal.request_hash, self.commitment.request_hash)
        self.assertEqual(
            proposal.frame_commitment_hash,
            self.commitment.canonical_hash(),
        )
        self.assertFalse(hasattr(proposal, "command"))
        self.assertFalse(hasattr(proposal, "capability"))

    def test_normalize_recomposes_complete_binding_and_rejects_a_plus_b(self) -> None:
        replacement_bytes = b'{"state":"other"}'
        other_tool_arguments = LodgingReservationArguments(
            self.request.arguments.offer_id,
            self.request.arguments.summary_version,
            self.request.arguments.confirmation_signature,
        )
        cases = (
            (
                replace(self.turn, aggregate_turn_id="turn-002"),
                replace(self.request, event_id="turn-002"),
                self.commitment,
            ),
            (
                replace(
                    self.turn,
                    boundary_state_bytes=replacement_bytes,
                    state_hash=hashlib.sha256(replacement_bytes).hexdigest(),
                ),
                self.request,
                self.commitment,
            ),
            (replace(self.turn, state_version=self.turn.state_version + 1), self.request, self.commitment),
            (
                replace(self.turn, lead_key_hash=_lead_hash("lead-synthetic-002")),
                replace(self.request, lead_key="lead-synthetic-002"),
                self.commitment,
            ),
            (self.turn, replace(self.request, event_id="turn-002"), self.commitment),
            (
                replace(self.turn, deadline_at=DEADLINE.replace(minute=DEADLINE.minute + 1)),
                replace(self.request, deadline=DEADLINE.replace(minute=DEADLINE.minute + 1)),
                self.commitment,
            ),
            (
                self.turn,
                replace(
                    self.request,
                    tool_name="cloudbeds_criar_reserva_v2",
                    arguments=other_tool_arguments,
                ),
                self.commitment,
            ),
            (
                self.turn,
                replace(
                    self.request,
                    arguments=replace(
                        self.request.arguments,
                        confirmation_signature="f" * 64,
                    ),
                ),
                self.commitment,
            ),
            (self.turn, self.request, replace(self.commitment, request_id="request-002")),
            (self.turn, self.request, replace(self.commitment, sequence=2)),
        )
        for turn, request, commitment in cases:
            with self.subTest(turn=turn, request=request, commitment=commitment):
                with self.assertRaises(DispatchRejected):
                    _normalize(self.dispatch, turn, request, commitment)

    def test_normalize_rejects_wrong_frame_alias_blocked_command_and_handmade_dto(self) -> None:
        wrong_frames = (
            _commitment(
                self.turn,
                self.request,
                direction=conversation.TranscriptDirection.PARENT_TO_CHILD,
            ),
            _commitment(
                self.turn,
                self.request,
                kind=conversation.TranscriptKind.READ,
            ),
        )
        for commitment in wrong_frames:
            with self.subTest(commitment=commitment), self.assertRaises(DispatchRejected):
                _normalize(self.dispatch, self.turn, self.request, commitment)

        invalid_requests = (
            replace(self.request, alias_depth=1),
            replace(self.request, tool_name="availability"),
            ToolDispatchRequest(
                "wise_verificar_pagamento",
                WiseVerificationArguments("anchor-001", "evidence-001"),
                self.state.lead_key,
                "turn-001",
                DEADLINE,
            ),
        )
        for request in invalid_requests:
            with self.subTest(request=request), self.assertRaises(DispatchRejected):
                _normalize(self.dispatch, self.turn, request, _commitment(self.turn, request))

        method = getattr(self.dispatch, "normalize_proposal", None)
        self.assertTrue(callable(method), "ToolDispatch.normalize_proposal must exist")
        for name, value in (
            ("turn_request", object()),
            ("request", object()),
            ("transcript_commitment", object()),
        ):
            kwargs = {
                "turn_request": self.turn,
                "request": self.request,
                "transcript_commitment": self.commitment,
            }
            kwargs[name] = value
            with self.subTest(name=name), self.assertRaises(TypeError):
                method(**kwargs)


class Phase8ToolDispatchAuthorizationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.dispatch = ToolDispatch()
        self.state, self.command = activity_queued_state()
        self.turn = _turn_request(self.state)
        self.request = _activity_request(self.state, self.command)
        self.proposal = _normalize(
            self.dispatch,
            self.turn,
            self.request,
            _commitment(self.turn, self.request),
        )

    def _verify(self, proposal, state, decision):
        method = getattr(self.dispatch, "verify_authorized", None)
        if not callable(method):
            raise AssertionError("ToolDispatch.verify_authorized must exist")
        return method(proposal=proposal, state=state, decision=decision)

    def test_verify_returns_exact_proposal_and_kernel_command(self) -> None:
        decision = KernelDecision(self.state, (self.command,), (), (), ())
        authorized = self._verify(self.proposal, self.state, decision)
        authorized_type = getattr(dispatch_module, "AuthorizedDispatch", None)
        self.assertIsNotNone(authorized_type, "AuthorizedDispatch must have an owner")
        self.assertIs(type(authorized), authorized_type)
        self.assertIs(authorized.proposal, self.proposal)
        self.assertIs(authorized.command, decision.commands[0])
        self.assertFalse(hasattr(authorized, "capability"))
        self.assertFalse(hasattr(authorized, "provider"))

    def test_verify_rejects_missing_duplicate_divergent_and_handmade_values(self) -> None:
        _, divergent_command = payment_boundary_state()
        decisions = (
            KernelDecision(self.state, (), (), (), ()),
            KernelDecision(self.state, (self.command, self.command), (), (), ()),
            KernelDecision(self.state, (divergent_command,), (), (), ()),
        )
        for decision in decisions:
            with self.subTest(decision=decision), self.assertRaises(DispatchRejected):
                self._verify(self.proposal, self.state, decision)

        valid_decision = KernelDecision(self.state, (self.command,), (), (), ())
        for name, value in (
            ("proposal", object()),
            ("state", object()),
            ("decision", object()),
        ):
            kwargs = {
                "proposal": self.proposal,
                "state": self.state,
                "decision": valid_decision,
            }
            kwargs[name] = value
            with self.subTest(name=name), self.assertRaises(TypeError):
                self._verify(**kwargs)

    def test_verify_rejects_stale_reservation_binding_and_payment_evidence(self) -> None:
        stale_reservation = replace(
            self.proposal,
            typed_arguments_json=to_tool_arguments_canonical_json(
                replace(self.request.arguments, confirmation_signature="f" * 64)
            ),
        )
        with self.assertRaises(DispatchRejected):
            self._verify(
                stale_reservation,
                self.state,
                KernelDecision(self.state, (self.command,), (), (), ()),
            )

        payment_state, payment_command = payment_boundary_state()
        payment = payment_state.payments[0]
        anchor = payment.subject.confirmed_reservation_anchor
        payment_arguments = LodgingPaymentArguments(
            anchor.payment_target_id,
            payment_command.evidence_claim_key,
            DecimalSlot("125.00"),
            anchor.currency,
            payment.verified_evidence.evidence.proof_receiver_profile_id,
            payment.verified_evidence.evidence.proof_status.value,
        )
        payment_request = ToolDispatchRequest(
            "cloudbeds_lancar_pagamento_confirmar_reserva",
            payment_arguments,
            payment_state.lead_key,
            "turn-001",
            DEADLINE,
        )
        payment_turn = _turn_request(payment_state)
        payment_proposal = _normalize(
            self.dispatch,
            payment_turn,
            payment_request,
            _commitment(payment_turn, payment_request),
        )
        stale_payment = replace(
            payment_proposal,
            typed_arguments_json=to_tool_arguments_canonical_json(
                replace(payment_arguments, receiver_profile_id="receiver:profile:forged")
            ),
        )
        with self.assertRaises(DispatchRejected):
            self._verify(
                stale_payment,
                payment_state,
                KernelDecision(payment_state, (payment_command,), (), (), ()),
            )

    def test_dispatch_module_has_no_provider_network_send_or_executor_capability(self) -> None:
        tree = ast.parse((ROOT / "reservation_boundary" / "dispatch.py").read_text())
        imported = {
            alias.name.split(".")[0].lower()
            for node in ast.walk(tree)
            if isinstance(node, (ast.Import, ast.ImportFrom))
            for alias in node.names
        }
        self.assertTrue(
            imported.isdisjoint(
                {
                    "requests",
                    "httpx",
                    "socket",
                    "subprocess",
                    "manychat",
                    "provider",
                }
            )
        )
        forbidden_calls = {
            "send",
            "send_message",
            "deliver",
            "execute_provider",
            "provider_write",
        }
        called_attributes = {
            node.func.attr
            for node in ast.walk(tree)
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
        }
        self.assertTrue(called_attributes.isdisjoint(forbidden_calls))


if __name__ == "__main__":
    unittest.main()
