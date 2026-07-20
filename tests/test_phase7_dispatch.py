"""Unique closed ToolDispatch classification and command binding."""

from __future__ import annotations

import copy
from dataclasses import replace
from datetime import date, timedelta
from decimal import Decimal
import inspect
import unittest

from reservation_domain import ReservationOperation, UncertainState
from reservation_followup import BusinessUnit, PaymentStatus
from reservation_boundary.dispatch import (
    ALIASES,
    CATALOG,
    DispatchRejected,
    ToolDispatch,
    command_migration_counts,
)
from reservation_boundary.types import (
    ActivityDescriptionArguments,
    ActivityPaymentArguments,
    ActivityReadArguments,
    ActivityReservationArguments,
    BooleanSlot,
    BoundaryState,
    CommandMigrationDisposition,
    DecimalSlot,
    DispatchKind,
    FaqReadArguments,
    IntegerSlot,
    LodgingPaymentArguments,
    LodgingReadArguments,
    RoomDescriptionArguments,
    StateCommitArguments,
    StringSlot,
    StripeLinkArguments,
    ToolDispatchRequest,
    TurnPlanReason,
    TypedFact,
    WiseVerificationArguments,
)
from tests.phase7_helpers import DEADLINE, NOW
from tests.test_phase2_serialization import all_domain_samples, complete_flow
from tests.test_phase6_payment_reducer import queued_payment


EXPECTED_CATALOG = {
    "cerebro_consultar",
    "cloudbeds_consultar_hospedagem_v2",
    "cloudbeds_descrever_quartos",
    "bokun_consultar_passeio_v2",
    "bokun_consultar_descricao",
    "cloudbeds_criar_reserva_v2",
    "bokun_agendar_passeio_v2",
    "cloudbeds_lancar_pagamento_confirmar_reserva",
    "bokun_lancar_pagamento_confirmar_reserva",
    "wise_verificar_pagamento",
    "cloudbeds_gerar_link_pagamento_stripe",
    "bokun_gerar_link_pagamento_stripe",
    "chapada_commit_state",
}


def request(name: str, arguments, *, deadline=DEADLINE) -> ToolDispatchRequest:
    return ToolDispatchRequest(
        name,
        arguments,
        "lead-synthetic-001",
        "event-synthetic-001",
        deadline,
    )


def activity_queued_state() -> tuple[BoundaryState, object]:
    states, _, command = complete_flow()
    queued = states[-1]
    return (
        BoundaryState(
            7,
            "lead-synthetic-001",
            0,
            queued,
            None,
            (),
            (),
        ),
        command,
    )


def uncertain_boundary_state() -> BoundaryState:
    states, _, _ = all_domain_samples()
    uncertain = next(state for state in states if isinstance(state, UncertainState))
    return BoundaryState(7, "lead-synthetic-001", 0, uncertain, None, (), ())


def payment_boundary_state() -> tuple[BoundaryState, object]:
    payment, command = queued_payment()
    return BoundaryState(7, "lead-synthetic-001", 0, None, None, (payment,), ()), command


class Phase7DispatchTests(unittest.TestCase):
    def setUp(self) -> None:
        self.dispatch = ToolDispatch()

    def test_catalog_and_migration_counts_are_exact(self) -> None:
        self.assertEqual(set(CATALOG), EXPECTED_CATALOG)
        self.assertEqual(
            command_migration_counts(),
            {
                "reservation": 2,
                "payment_settlement": 2,
                "blocked_unmigrated": 3,
            },
        )
        self.assertEqual(set(ALIASES), {"availability", "activity_availability"})
        self.assertNotIn("provider", inspect.signature(ToolDispatch).parameters)
        self.assertNotIn("executor", inspect.signature(ToolDispatch).parameters)

    def test_each_read_is_typed_and_never_executes_a_provider(self) -> None:
        cases = (
            (
                "cerebro_consultar",
                FaqReadArguments("What is included?", "en"),
            ),
            (
                "cloudbeds_consultar_hospedagem_v2",
                LodgingReadArguments(date(2026, 8, 1), date(2026, 8, 2), 2),
            ),
            (
                "cloudbeds_descrever_quartos",
                RoomDescriptionArguments("room-offer-001"),
            ),
            (
                "bokun_consultar_passeio_v2",
                ActivityReadArguments(date(2026, 8, 1), 2),
            ),
            (
                "bokun_consultar_descricao",
                ActivityDescriptionArguments("activity-001"),
            ),
        )
        for name, arguments in cases:
            with self.subTest(name=name):
                result = self.dispatch.dispatch(
                    request(name, arguments),
                    current_state=BoundaryState(
                        7,
                        "lead-synthetic-001",
                        0,
                        None,
                        None,
                        (),
                        (),
                    ),
                    now=NOW,
                )
                self.assertIs(result.kind, DispatchKind.READ)
                self.assertIs(result.read_request.arguments, arguments)
                self.assertEqual(result.commands, ())

    def test_unknown_and_alias_category_escalation_fail_closed(self) -> None:
        state, command = activity_queued_state()
        arguments = ActivityReservationArguments(
            command.payload.components[0].offer_id,
            command.draft_version,
            command.subject_signature,
        )
        with self.assertRaises(DispatchRejected):
            self.dispatch.dispatch(request("unknown_tool", arguments), current_state=state, now=NOW)
        with self.assertRaises(DispatchRejected):
            self.dispatch.dispatch(request("availability", arguments), current_state=state, now=NOW)

    def test_activity_write_returns_existing_authorized_command_only(self) -> None:
        state, command = activity_queued_state()
        arguments = ActivityReservationArguments(
            command.payload.components[0].offer_id,
            command.draft_version,
            command.subject_signature,
        )
        result = self.dispatch.dispatch(
            request("bokun_agendar_passeio_v2", arguments),
            current_state=state,
            now=NOW,
        )
        self.assertIs(result.kind, DispatchKind.COMMAND)
        self.assertEqual(result.commands, (command,))
        self.assertIs(
            result.command_migration,
            CommandMigrationDisposition.RESERVATION,
        )
        self.assertIs(command.operation, ReservationOperation.BOOK_ACTIVITY)

        with self.assertRaises(DispatchRejected):
            self.dispatch.dispatch(
                request(
                    "bokun_agendar_passeio_v2",
                    replace(arguments, confirmation_signature="f" * 64),
                ),
                current_state=state,
                now=NOW,
            )

    def test_payment_write_returns_existing_settlement_command_only(self) -> None:
        state, command = payment_boundary_state()
        payment = state.payments[0]
        anchor = payment.subject.confirmed_reservation_anchor
        self.assertIs(payment.status, PaymentStatus.SETTLEMENT_QUEUED)
        arguments = LodgingPaymentArguments(
            anchor.payment_target_id,
            command.evidence_claim_key,
            DecimalSlot("125.00"),
            anchor.currency,
        )
        result = self.dispatch.dispatch(
            request("cloudbeds_lancar_pagamento_confirmar_reserva", arguments),
            current_state=state,
            now=NOW,
        )
        self.assertEqual(result.commands, (command,))
        self.assertIs(
            result.command_migration,
            CommandMigrationDisposition.PAYMENT_SETTLEMENT,
        )

        with self.assertRaises(DispatchRejected):
            self.dispatch.dispatch(
                request(
                    "bokun_lancar_pagamento_confirmar_reserva",
                    ActivityPaymentArguments(
                        anchor.payment_target_id,
                        command.evidence_claim_key,
                        DecimalSlot("125.00"),
                        anchor.currency,
                    ),
                ),
                current_state=state,
                now=NOW,
            )

    def test_unmigrated_writes_require_manual_review_without_command(self) -> None:
        state = BoundaryState(7, "lead-synthetic-001", 0, None, None, (), ())
        cases = (
            (
                "wise_verificar_pagamento",
                WiseVerificationArguments("anchor-001", "evidence-001"),
            ),
            (
                "cloudbeds_gerar_link_pagamento_stripe",
                StripeLinkArguments("anchor-001", DecimalSlot("125.00"), "BRL"),
            ),
            (
                "bokun_gerar_link_pagamento_stripe",
                StripeLinkArguments("anchor-001", DecimalSlot("125.00"), "BRL"),
            ),
        )
        for name, arguments in cases:
            with self.subTest(name=name):
                result = self.dispatch.dispatch(
                    request(name, arguments),
                    current_state=state,
                    now=NOW,
                )
                self.assertIs(result.reason, TurnPlanReason.MANUAL_REVIEW)
                self.assertIs(
                    result.command_migration,
                    CommandMigrationDisposition.BLOCKED_UNMIGRATED,
                )
                self.assertEqual(result.commands, ())

    def test_state_commit_is_whitelisted_and_llm_boolean_cannot_authorize(self) -> None:
        state = BoundaryState(7, "lead-synthetic-001", 0, None, None, (), ())
        allowed = StateCommitArguments(
            (
                TypedFact("language", StringSlot("pt-BR")),
                TypedFact("adults", IntegerSlot(2)),
            )
        )
        result = self.dispatch.dispatch(
            request("chapada_commit_state", allowed),
            current_state=state,
            now=NOW,
        )
        self.assertIs(result.kind, DispatchKind.STATE_COMMIT)
        self.assertEqual(result.facts, allowed.facts)

        forbidden = StateCommitArguments(
            (TypedFact("selected_offer_id", StringSlot("offer-001")),)
        )
        with self.assertRaises(DispatchRejected):
            self.dispatch.dispatch(
                request("chapada_commit_state", forbidden),
                current_state=state,
                now=NOW,
            )
        with self.assertRaises(TypeError):
            request("bokun_agendar_passeio_v2", {"confirmed": True})

    def test_deadline_called_unknown_and_package_paths_fail_closed(self) -> None:
        state, command = activity_queued_state()
        arguments = ActivityReservationArguments(
            command.payload.components[0].offer_id,
            command.draft_version,
            command.subject_signature,
        )
        with self.assertRaises(DispatchRejected):
            self.dispatch.dispatch(
                request("bokun_agendar_passeio_v2", arguments, deadline=NOW),
                current_state=state,
                now=NOW,
            )

        unknown = self.dispatch.dispatch(
            request("bokun_agendar_passeio_v2", arguments),
            current_state=uncertain_boundary_state(),
            now=NOW,
        )
        self.assertIs(unknown.reason, TurnPlanReason.MANUAL_REVIEW)
        self.assertEqual(unknown.commands, ())

        package_command = copy.deepcopy(command)
        object.__setattr__(package_command, "operation", ReservationOperation.RESERVE_PACKAGE)
        object.__setattr__(state.workflow, "command", package_command)
        with self.assertRaises(DispatchRejected):
            self.dispatch.dispatch(
                request("bokun_agendar_passeio_v2", arguments),
                current_state=state,
                now=NOW,
            )


if __name__ == "__main__":
    unittest.main()
