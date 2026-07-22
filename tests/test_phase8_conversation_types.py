"""Focused contracts for Phase 8 conversation wire types."""

from __future__ import annotations

from dataclasses import FrozenInstanceError, fields
from datetime import datetime, timedelta, timezone
import hashlib
import json
import unittest

import reservation_boundary.conversation as conversation
from reservation_boundary.conversation import SourceEventIdentity
from reservation_boundary.types import ConversationIntentKind, NormalizedMessage


DEADLINE = datetime(2026, 7, 22, 12, 0, tzinfo=timezone.utc)


def _request_kwargs() -> dict[str, object]:
    boundary_state_bytes = b'{"state":"canonical"}'
    return {
        "boundary_state_bytes": boundary_state_bytes,
        "state_version": 7,
        "state_hash": hashlib.sha256(boundary_state_bytes).hexdigest(),
        "normalized_message": NormalizedMessage("hello", "en"),
        "aggregate_turn_id": "turn-001",
        "source_events": (
            SourceEventIdentity("manychat:event-001", "a" * 64),
            SourceEventIdentity("manychat:event-002", "b" * 64),
        ),
        "lead_key_hash": "c" * 64,
        "private_delivery_binding_hash": "d" * 64,
        "deadline_at": DEADLINE,
        "behavior_profile_fingerprint": "e" * 64,
    }


class Phase8ConversationTypeTests(unittest.TestCase):
    def test_source_event_identity_fields_and_canonical_hash_are_closed(self) -> None:
        identity = SourceEventIdentity(
            source_event_id="manychat:event-001",
            source_event_hash="a" * 64,
        )

        self.assertEqual(
            tuple(field.name for field in fields(SourceEventIdentity)),
            ("source_event_id", "source_event_hash"),
        )
        self.assertEqual(SourceEventIdentity.SCHEMA, "phase8-source-event-identity")
        self.assertEqual(SourceEventIdentity.VERSION, 1)
        self.assertEqual(SourceEventIdentity.DOMAIN, "phase8-source-event-identity-v1")
        self.assertEqual(
            identity.to_canonical_bytes(),
            b'{"data":{"source_event_hash":"aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa","source_event_id":"manychat:event-001"},"schema":"phase8-source-event-identity","version":1}',
        )
        self.assertEqual(
            identity.canonical_hash(),
            hashlib.sha256(
                b"phase8-source-event-identity-v1\x00" + identity.to_canonical_bytes()
            ).hexdigest(),
        )

    def test_source_event_identity_rejects_noncanonical_identity_and_hash(self) -> None:
        invalid_values = (
            ("", "a" * 64),
            (" event-001", "a" * 64),
            ("event 001", "a" * 64),
            ("event-001", "A" * 64),
            ("event-001", "a" * 63),
            (1, "a" * 64),
            ("event-001", b"a" * 64),
        )
        for source_event_id, source_event_hash in invalid_values:
            with self.subTest(
                source_event_id=source_event_id,
                source_event_hash=source_event_hash,
            ):
                with self.assertRaises((TypeError, ValueError)):
                    SourceEventIdentity(
                        source_event_id=source_event_id,  # type: ignore[arg-type]
                        source_event_hash=source_event_hash,  # type: ignore[arg-type]
                    )

    def test_source_event_identity_is_frozen(self) -> None:
        identity = SourceEventIdentity("event-001", "b" * 64)

        with self.assertRaises(FrozenInstanceError):
            identity.source_event_id = "event-002"  # type: ignore[misc]

    def test_maya_turn_request_fields_and_canonical_hash_are_closed(self) -> None:
        request_type = getattr(conversation, "MayaTurnRequest", None)
        self.assertIsNotNone(request_type, "MayaTurnRequest must have an owner")
        assert request_type is not None
        request = request_type(**_request_kwargs())

        self.assertEqual(
            tuple(field.name for field in fields(request_type)),
            (
                "boundary_state_bytes",
                "state_version",
                "state_hash",
                "normalized_message",
                "aggregate_turn_id",
                "source_events",
                "lead_key_hash",
                "private_delivery_binding_hash",
                "deadline_at",
                "behavior_profile_fingerprint",
            ),
        )
        self.assertEqual(request_type.SCHEMA, "phase8-maya-turn-request")
        self.assertEqual(request_type.VERSION, 1)
        self.assertEqual(request_type.DOMAIN, "phase8-maya-turn-request-v1")
        expected = {
            "schema": "phase8-maya-turn-request",
            "version": 1,
            "data": {
                "aggregate_turn_id": "turn-001",
                "behavior_profile_fingerprint": "e" * 64,
                "boundary_state_bytes": "eyJzdGF0ZSI6ImNhbm9uaWNhbCJ9",
                "deadline_at": "2026-07-22T12:00:00+00:00",
                "lead_key_hash": "c" * 64,
                "normalized_message": {"locale": "en", "text": "hello"},
                "private_delivery_binding_hash": "d" * 64,
                "source_events": [
                    {
                        "schema": "phase8-source-event-identity",
                        "version": 1,
                        "data": {
                            "source_event_hash": "a" * 64,
                            "source_event_id": "manychat:event-001",
                        },
                    },
                    {
                        "schema": "phase8-source-event-identity",
                        "version": 1,
                        "data": {
                            "source_event_hash": "b" * 64,
                            "source_event_id": "manychat:event-002",
                        },
                    },
                ],
                "state_hash": hashlib.sha256(b'{"state":"canonical"}').hexdigest(),
                "state_version": 7,
            },
        }
        expected_bytes = json.dumps(
            expected,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
        self.assertEqual(request.to_canonical_bytes(), expected_bytes)
        self.assertEqual(
            request.canonical_hash(),
            hashlib.sha256(
                b"phase8-maya-turn-request-v1\x00" + expected_bytes
            ).hexdigest(),
        )
        with self.assertRaises(FrozenInstanceError):
            request.state_version = 8

    def test_maya_turn_request_rejects_open_or_inconsistent_values(self) -> None:
        request_type = getattr(conversation, "MayaTurnRequest", None)
        self.assertIsNotNone(request_type, "MayaTurnRequest must have an owner")
        assert request_type is not None
        valid = _request_kwargs()
        invalid_overrides = (
            {"boundary_state_bytes": bytearray(b"mutable")},
            {"boundary_state_bytes": b""},
            {"state_version": True},
            {"state_hash": "f" * 64},
            {"normalized_message": object()},
            {"aggregate_turn_id": "turn 001"},
            {"source_events": list(valid["source_events"])},
            {"source_events": ()},
            {"source_events": (valid["source_events"][0], valid["source_events"][0])},
            {"lead_key_hash": "C" * 64},
            {"private_delivery_binding_hash": "short"},
            {"deadline_at": DEADLINE.replace(tzinfo=None)},
            {"deadline_at": DEADLINE.astimezone(timezone(timedelta(hours=-3)))},
            {"behavior_profile_fingerprint": ""},
        )
        for override in invalid_overrides:
            with self.subTest(override=override):
                with self.assertRaises((TypeError, ValueError)):
                    request_type(**(valid | override))

    def test_maya_intent_closure_is_closed_and_excludes_child_capabilities(self) -> None:
        intent_type = getattr(conversation, "MayaIntentClosure", None)
        self.assertIsNotNone(intent_type, "MayaIntentClosure must have an owner")
        assert intent_type is not None
        self.assertEqual(
            tuple(field.name for field in fields(intent_type)),
            ("kind", "selection", "confirmation", "handoff"),
        )
        self.assertEqual(intent_type.SCHEMA, "phase8-maya-intent-closure")
        self.assertEqual(intent_type.VERSION, 1)
        self.assertEqual(intent_type.DOMAIN, "phase8-maya-intent-closure-v1")

        inform = intent_type(ConversationIntentKind.INFORM, None, None, False)
        selection = intent_type(ConversationIntentKind.SELECT, "offer-001", None, False)
        confirmation = intent_type(ConversationIntentKind.CONFIRM, None, 7, False)
        handoff = intent_type(
            ConversationIntentKind.REQUEST_HANDOFF,
            None,
            None,
            True,
        )
        selection_bytes = (
            b'{"data":{"confirmation":null,"handoff":false,"kind":"select","selection":"offer-001"},"schema":"phase8-maya-intent-closure","version":1}'
        )
        self.assertEqual(selection.to_canonical_bytes(), selection_bytes)
        self.assertEqual(
            selection.canonical_hash(),
            hashlib.sha256(
                b"phase8-maya-intent-closure-v1\x00" + selection_bytes
            ).hexdigest(),
        )
        self.assertEqual(
            tuple(item.kind for item in (inform, selection, confirmation, handoff)),
            (
                ConversationIntentKind.INFORM,
                ConversationIntentKind.SELECT,
                ConversationIntentKind.CONFIRM,
                ConversationIntentKind.REQUEST_HANDOFF,
            ),
        )
        for invalid in (
            (ConversationIntentKind.TOOL_REQUEST, None, None, False),
            (ConversationIntentKind.SELECT, None, None, False),
            (ConversationIntentKind.INFORM, "offer-001", None, False),
            (ConversationIntentKind.CONFIRM, None, True, False),
            (ConversationIntentKind.CONFIRM, None, None, False),
            (ConversationIntentKind.REQUEST_HANDOFF, None, None, False),
            (ConversationIntentKind.INFORM, None, None, True),
        ):
            with self.subTest(invalid=invalid):
                with self.assertRaises((TypeError, ValueError)):
                    intent_type(*invalid)
        with self.assertRaises(TypeError):
            intent_type(
                kind=ConversationIntentKind.INFORM,
                selection=None,
                confirmation=None,
                handoff=False,
                tool_name="forbidden",
            )

    def test_maya_turn_closure_fields_and_canonical_hash_are_closed(self) -> None:
        intent_type = getattr(conversation, "MayaIntentClosure", None)
        closure_type = getattr(conversation, "MayaTurnClosure", None)
        route_type = getattr(conversation, "PublicRoute", None)
        reply_type = getattr(conversation, "PublicReplyType", None)
        self.assertIsNotNone(intent_type, "MayaIntentClosure must have an owner")
        self.assertIsNotNone(closure_type, "MayaTurnClosure must have an owner")
        self.assertIsNotNone(route_type, "PublicRoute must have an owner")
        self.assertIsNotNone(reply_type, "PublicReplyType must have an owner")
        assert intent_type is not None
        assert closure_type is not None
        assert route_type is not None
        assert reply_type is not None

        self.assertEqual(
            tuple(item.value for item in route_type),
            ("recepcionista", "hostel", "agencia", "fechamento", "handoff", "no_reply"),
        )
        self.assertEqual(
            tuple(item.value for item in reply_type),
            ("ask_more", "qualify", "answer", "handoff", "no_reply"),
        )
        self.assertEqual(
            tuple(field.name for field in fields(closure_type)),
            (
                "aggregate_turn_id",
                "intent_closure",
                "public_text",
                "route",
                "reply_type",
                "final_seq",
                "expected_prefix_mac",
                "ephemeral_session_id",
                "zero_requests_in_flight",
            ),
        )
        closure = closure_type(
            aggregate_turn_id="turn-001",
            intent_closure=intent_type(
                ConversationIntentKind.SELECT,
                "offer-001",
                None,
                False,
            ),
            public_text="Escolha registrada.",
            route=route_type.HOSTEL,
            reply_type=reply_type.ANSWER,
            final_seq=3,
            expected_prefix_mac="a" * 64,
            ephemeral_session_id="session-001",
            zero_requests_in_flight=True,
        )
        self.assertEqual(closure_type.SCHEMA, "phase8-maya-turn-closure")
        self.assertEqual(closure_type.VERSION, 1)
        self.assertEqual(closure_type.DOMAIN, "phase8-maya-turn-closure-v1")
        expected = {
            "schema": "phase8-maya-turn-closure",
            "version": 1,
            "data": {
                "aggregate_turn_id": "turn-001",
                "ephemeral_session_id": "session-001",
                "expected_prefix_mac": "a" * 64,
                "final_seq": 3,
                "intent_closure": {
                    "schema": "phase8-maya-intent-closure",
                    "version": 1,
                    "data": {
                        "confirmation": None,
                        "handoff": False,
                        "kind": "select",
                        "selection": "offer-001",
                    },
                },
                "public_text": "Escolha registrada.",
                "reply_type": "answer",
                "route": "hostel",
                "zero_requests_in_flight": True,
            },
        }
        expected_bytes = json.dumps(
            expected,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
        self.assertEqual(closure.to_canonical_bytes(), expected_bytes)
        self.assertEqual(
            closure.canonical_hash(),
            hashlib.sha256(
                b"phase8-maya-turn-closure-v1\x00" + expected_bytes
            ).hexdigest(),
        )
        with self.assertRaises(FrozenInstanceError):
            closure.final_seq = 4

    def test_maya_turn_closure_rejects_nonterminal_or_inconsistent_output(self) -> None:
        intent_type = getattr(conversation, "MayaIntentClosure", None)
        closure_type = getattr(conversation, "MayaTurnClosure", None)
        route_type = getattr(conversation, "PublicRoute", None)
        reply_type = getattr(conversation, "PublicReplyType", None)
        self.assertIsNotNone(intent_type, "MayaIntentClosure must have an owner")
        self.assertIsNotNone(closure_type, "MayaTurnClosure must have an owner")
        self.assertIsNotNone(route_type, "PublicRoute must have an owner")
        self.assertIsNotNone(reply_type, "PublicReplyType must have an owner")
        assert intent_type is not None
        assert closure_type is not None
        assert route_type is not None
        assert reply_type is not None
        valid = {
            "aggregate_turn_id": "turn-001",
            "intent_closure": intent_type(
                ConversationIntentKind.INFORM,
                None,
                None,
                False,
            ),
            "public_text": "Resposta pública.",
            "route": route_type.RECEPTIONIST,
            "reply_type": reply_type.ANSWER,
            "final_seq": 1,
            "expected_prefix_mac": "a" * 64,
            "ephemeral_session_id": "session-001",
            "zero_requests_in_flight": True,
        }
        invalid_overrides = (
            {"route": "hostel"},
            {"reply_type": "answer"},
            {"final_seq": True},
            {"final_seq": 0},
            {"expected_prefix_mac": "A" * 64},
            {"ephemeral_session_id": "session 001"},
            {"zero_requests_in_flight": False},
            {"zero_requests_in_flight": 1},
            {"public_text": ""},
            {"public_text": "bad\u0000text"},
            {
                "route": route_type.HANDOFF,
                "reply_type": reply_type.ANSWER,
            },
            {
                "route": route_type.NO_REPLY,
                "reply_type": reply_type.NO_REPLY,
                "public_text": "must be empty",
            },
        )
        for override in invalid_overrides:
            with self.subTest(override=override):
                with self.assertRaises((TypeError, ValueError)):
                    closure_type(**(valid | override))

        handoff_intent = intent_type(
            ConversationIntentKind.REQUEST_HANDOFF,
            None,
            None,
            True,
        )
        with self.assertRaises(ValueError):
            closure_type(**(valid | {"intent_closure": handoff_intent}))
        no_reply = closure_type(
            **(
                valid
                | {
                    "public_text": "",
                    "route": route_type.NO_REPLY,
                    "reply_type": reply_type.NO_REPLY,
                }
            )
        )
        self.assertEqual(no_reply.public_text, "")

    def test_transcript_commitment_fields_enums_and_hash_are_closed(self) -> None:
        commitment_type = getattr(conversation, "TranscriptCommitment", None)
        direction_type = getattr(conversation, "TranscriptDirection", None)
        kind_type = getattr(conversation, "TranscriptKind", None)
        self.assertIsNotNone(
            commitment_type,
            "TranscriptCommitment must have an owner",
        )
        self.assertIsNotNone(direction_type, "TranscriptDirection must have an owner")
        self.assertIsNotNone(kind_type, "TranscriptKind must have an owner")
        assert commitment_type is not None
        assert direction_type is not None
        assert kind_type is not None
        self.assertEqual(
            tuple(item.value for item in direction_type),
            ("child_to_parent", "parent_to_child"),
        )
        self.assertEqual(
            tuple(item.value for item in kind_type),
            ("read", "state_commit", "learning", "command", "final"),
        )
        self.assertEqual(
            tuple(field.name for field in fields(commitment_type)),
            (
                "direction",
                "kind",
                "sequence",
                "request_id",
                "request_hash",
                "response_hash",
                "previous_frame_commitment",
            ),
        )
        commitment = commitment_type(
            direction=direction_type.CHILD_TO_PARENT,
            kind=kind_type.READ,
            sequence=1,
            request_id="request-001",
            request_hash="a" * 64,
            response_hash="b" * 64,
            previous_frame_commitment="c" * 64,
        )
        self.assertEqual(commitment_type.SCHEMA, "phase8-transcript-commitment")
        self.assertEqual(commitment_type.VERSION, 1)
        self.assertEqual(commitment_type.DOMAIN, "phase8-transcript-commitment-v1")
        expected_bytes = (
            b'{"data":{"direction":"child_to_parent","kind":"read","previous_frame_commitment":"cccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccc","request_hash":"aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa","request_id":"request-001","response_hash":"bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb","sequence":1},"schema":"phase8-transcript-commitment","version":1}'
        )
        self.assertEqual(commitment.to_canonical_bytes(), expected_bytes)
        self.assertEqual(
            commitment.canonical_hash(),
            hashlib.sha256(
                b"phase8-transcript-commitment-v1\x00" + expected_bytes
            ).hexdigest(),
        )

    def test_transcript_commitment_rejects_open_or_inconsistent_frames(self) -> None:
        commitment_type = getattr(conversation, "TranscriptCommitment", None)
        direction_type = getattr(conversation, "TranscriptDirection", None)
        kind_type = getattr(conversation, "TranscriptKind", None)
        self.assertIsNotNone(
            commitment_type,
            "TranscriptCommitment must have an owner",
        )
        self.assertIsNotNone(direction_type, "TranscriptDirection must have an owner")
        self.assertIsNotNone(kind_type, "TranscriptKind must have an owner")
        assert commitment_type is not None
        assert direction_type is not None
        assert kind_type is not None
        valid = {
            "direction": direction_type.CHILD_TO_PARENT,
            "kind": kind_type.READ,
            "sequence": 1,
            "request_id": "request-001",
            "request_hash": "a" * 64,
            "response_hash": "b" * 64,
            "previous_frame_commitment": "c" * 64,
        }
        invalid_overrides = (
            {"direction": "child_to_parent"},
            {"kind": "read"},
            {"sequence": True},
            {"sequence": 0},
            {"request_id": "request 001"},
            {"request_hash": "A" * 64},
            {"response_hash": "short"},
            {"previous_frame_commitment": ""},
            {
                "direction": direction_type.PARENT_TO_CHILD,
                "kind": kind_type.FINAL,
            },
        )
        for override in invalid_overrides:
            with self.subTest(override=override):
                with self.assertRaises((TypeError, ValueError)):
                    commitment_type(**(valid | override))

    def test_public_reply_chunk_matches_accepted_known_answer_and_policy(self) -> None:
        chunk_type = getattr(conversation, "PublicReplyChunk", None)
        self.assertIsNotNone(chunk_type, "PublicReplyChunk must have an owner")
        assert chunk_type is not None
        chunk = chunk_type(
            aggregate_turn_id="turn-1",
            ordinal=0,
            text="Vou chamar uma pessoa.",
            source_closure_hash="a" * 64,
        )

        self.assertEqual(
            tuple(field.name for field in fields(chunk_type)),
            ("aggregate_turn_id", "ordinal", "text", "source_closure_hash"),
        )
        self.assertEqual(chunk_type.SCHEMA, "phase8-public-reply-chunk")
        self.assertEqual(chunk_type.VERSION, 1)
        self.assertEqual(chunk_type.DOMAIN, "phase8-public-reply-chunk-v1")
        expected = (
            b'{"data":{"aggregate_turn_id":"turn-1","ordinal":0,'
            b'"source_closure_hash":"aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",'
            b'"text":"Vou chamar uma pessoa."},"schema":"phase8-public-reply-chunk","version":1}'
        )
        self.assertEqual(chunk.to_canonical_bytes(), expected)
        self.assertEqual(
            chunk.canonical_hash(),
            "151df95a0d5ac9322f1263e9f35766e3e1db3aa91c6f42332d946dfcbf3641e1",
        )
        for override in (
            {"aggregate_turn_id": "Turn-1"},
            {"ordinal": True},
            {"ordinal": -1},
            {"text": ""},
            {"text": "Telefone pessoal: (75) 99999-9999"},
            {"text": "  espaço"},
            {"source_closure_hash": "A" * 64},
        ):
            with self.subTest(override=override):
                with self.assertRaises((TypeError, ValueError)):
                    chunk_type(
                        **(
                            {
                                "aggregate_turn_id": "turn-1",
                                "ordinal": 0,
                                "text": "Resposta segura.",
                                "source_closure_hash": "a" * 64,
                            }
                            | override
                        )
                    )

    def test_capability_policy_is_complete_ordered_and_matches_known_answer(self) -> None:
        policy_type = getattr(conversation, "CapabilityPolicy", None)
        capability_type = getattr(conversation, "Capability", None)
        disposition_type = getattr(conversation, "CapabilityDisposition", None)
        worker_type = getattr(conversation, "Worker", None)
        mode_type = getattr(conversation, "WorkerMode", None)
        guard_type = getattr(conversation, "GuardSemantic", None)
        for value in (
            policy_type,
            capability_type,
            disposition_type,
            worker_type,
            mode_type,
            guard_type,
        ):
            self.assertIsNotNone(value)
        assert policy_type and capability_type and disposition_type
        assert worker_type and mode_type and guard_type

        capability_rows = (
            (capability_type.LEGACY_READ, disposition_type.READ_ONLY),
            (capability_type.MAYA_INFERENCE, disposition_type.EXECUTE),
            (capability_type.PROVIDER_READ, disposition_type.READ_ONLY),
            (capability_type.TURN_COMMIT, disposition_type.EXECUTE),
            (capability_type.RELAY_ENQUEUE, disposition_type.EXECUTE),
            (capability_type.PROVIDER_WRITE, disposition_type.DENIED),
            (capability_type.FOLLOWUP_DELIVERY, disposition_type.DENIED),
            (capability_type.PUBLIC_DELIVERY, disposition_type.DENIED),
            (capability_type.LEARNING_WRITE, disposition_type.DENIED),
        )
        worker_rows = (
            (worker_type.TURN_COORDINATOR, mode_type.ACTIVE),
            (worker_type.COMMAND_RELAY_WORKER, mode_type.ACTIVE),
            (worker_type.INTERNAL_JOB_WORKER, mode_type.ACTIVE),
            (worker_type.PROVIDER_EFFECT_WORKER, mode_type.DISABLED),
            (worker_type.FOLLOWUP_DELIVERY_WORKER, mode_type.DISABLED),
            (worker_type.PUBLIC_DELIVERY_WORKER, mode_type.DISABLED),
            (worker_type.LEARNING_WORKER, mode_type.DISABLED),
            (worker_type.RECONCILIATION_WORKER, mode_type.SHADOW),
            (worker_type.QUALIFICATION_CONTROLLER, mode_type.DISABLED),
        )
        policy = policy_type(
            capability_matrix=capability_rows,
            worker_modes=worker_rows,
            guard_semantics=tuple(guard_type),
        )

        self.assertEqual(
            tuple(field.name for field in fields(policy_type)),
            ("capability_matrix", "worker_modes", "guard_semantics"),
        )
        self.assertEqual(policy_type.SCHEMA, "phase8-capability-policy")
        self.assertEqual(policy_type.VERSION, 1)
        self.assertEqual(policy_type.DOMAIN, "phase8-capability-policy-v1")
        self.assertEqual(
            policy.canonical_hash(),
            "6e8761645380f978ad63091535f84a2c2cce657643377aabc944b4d5e2a671a4",
        )
        self.assertEqual(
            json.loads(policy.to_canonical_bytes()),
            {
                "schema": "phase8-capability-policy",
                "version": 1,
                "data": {
                    "capability_matrix": [
                        [capability.value, disposition.value]
                        for capability, disposition in capability_rows
                    ],
                    "worker_modes": [
                        [worker.value, mode.value] for worker, mode in worker_rows
                    ],
                    "guard_semantics": [guard.value for guard in guard_type],
                },
            },
        )
        for override in (
            {"capability_matrix": list(capability_rows)},
            {"capability_matrix": capability_rows[:-1]},
            {"capability_matrix": capability_rows[1:] + capability_rows[:1]},
            {"worker_modes": worker_rows[:-1]},
            {"guard_semantics": tuple(reversed(tuple(guard_type)))},
        ):
            with self.subTest(override=override):
                with self.assertRaises((TypeError, ValueError)):
                    policy_type(
                        **(
                            {
                                "capability_matrix": capability_rows,
                                "worker_modes": worker_rows,
                                "guard_semantics": tuple(guard_type),
                            }
                            | override
                        )
                    )


if __name__ == "__main__":
    unittest.main()
