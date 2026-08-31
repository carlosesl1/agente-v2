from __future__ import annotations

import csv
import io
from typing import Iterable, Mapping

from v2_ops.records import (
    ExecutionLink,
    LeadDetail,
    RecordsSnapshot,
    passenger_manifest_public,
)


DATASETS = frozenset(
    {"leads", "executions", "reservations", "payments", "handoffs", "lead-history"}
)

_COLUMNS: dict[str, tuple[str, ...]] = {
    "leads": (
        "lead_id",
        "first_activity_at",
        "last_activity_at",
        "state_code",
        "state_label",
        "fact_count",
        "dialogue_turn_count",
        "passenger_manifest_count",
        "inbound_count",
        "public_reply_count",
        "execution_count",
        "reservation_count",
        "payment_count",
        "payment_initiation_count",
        "settled_payment_count",
        "handoff_count",
        "latest_execution_status",
    ),
    "executions": (
        "execution_id",
        "lead_id",
        "received_at",
        "completed_at",
        "status",
    ),
    "reservations": (
        "lead_id",
        "command_id",
        "workflow_id",
        "draft_id",
        "draft_version",
        "operation",
        "status_code",
        "status_label",
        "certainty",
        "normalized_status",
        "provider_reference",
        "bokun_booking_id",
        "cloudbeds_reservation_id",
        "total_minor",
        "currency",
        "payment_method",
        "customer_ref",
        "customer_name",
        "customer_email",
        "customer_phone",
        "component_count",
        "created_at",
        "updated_at",
    ),
    "payments": (
        "record_id",
        "lead_id",
        "phase",
        "payment_id",
        "initiation_id",
        "settlement_command_id",
        "reservation_anchor_id",
        "method",
        "amount_due_minor",
        "amount_paid_minor",
        "currency",
        "due_kind",
        "status_code",
        "status_label",
        "settled",
        "workflow_status",
        "ledger_status",
        "outcome_certainty",
        "reconciliation_status",
        "payment_link_prepared",
        "steps",
        "settled_at",
        "updated_at",
    ),
    "handoffs": (
        "lead_id",
        "handoff_id",
        "incident_key",
        "reason_code",
        "status_code",
        "status_label",
        "event_count",
        "receipt_count",
        "created_at",
        "updated_at",
    ),
    "lead-history": (
        "event_at",
        "category",
        "record_id",
        "status",
        "name",
        "content",
    ),
}


def _cell(value: object) -> str:
    if value is None:
        text = ""
    elif type(value) is bool:
        text = "true" if value else "false"
    else:
        text = str(value)
    if text.startswith(("=", "+", "-", "@", "\t", "\r")):
        return "'" + text
    return text


def _rows(
    dataset: str,
    *,
    snapshot: RecordsSnapshot,
    executions: tuple[ExecutionLink, ...],
    lead_id: str | None,
    lead_detail: LeadDetail | None,
) -> Iterable[Mapping[str, object]]:
    if dataset == "leads":
        for lead in snapshot.leads:
            yield {
                "lead_id": lead.lead_id,
                "first_activity_at": lead.first_activity_at,
                "last_activity_at": lead.last_activity_at,
                "state_code": lead.state_code,
                "state_label": lead.state_label,
                "fact_count": lead.fact_count,
                "dialogue_turn_count": lead.dialogue_turn_count,
                "passenger_manifest_count": lead.passenger_manifest_count,
                "inbound_count": lead.inbound_count,
                "public_reply_count": lead.public_reply_count,
                "execution_count": lead.execution_count,
                "reservation_count": lead.reservation_count,
                "payment_count": lead.payment_count,
                "payment_initiation_count": lead.payment_initiation_count,
                "settled_payment_count": lead.settled_payment_count,
                "handoff_count": lead.handoff_count,
                "latest_execution_status": lead.latest_execution_status,
            }
        return
    if dataset == "executions":
        for execution in executions:
            yield {
                "execution_id": execution.execution_id,
                "lead_id": execution.lead_id,
                "received_at": execution.received_at.isoformat(),
                "completed_at": (
                    None if execution.completed_at is None else execution.completed_at.isoformat()
                ),
                "status": execution.status,
            }
        return
    if dataset == "reservations":
        for reservation in snapshot.reservations:
            yield {
                "lead_id": reservation.lead_id,
                "command_id": reservation.command_id,
                "workflow_id": reservation.workflow_id,
                "draft_id": reservation.draft_id,
                "draft_version": reservation.draft_version,
                "operation": reservation.operation,
                "status_code": reservation.status_code,
                "status_label": reservation.status_label,
                "certainty": reservation.certainty,
                "normalized_status": reservation.normalized_status,
                "provider_reference": reservation.provider_reference,
                "bokun_booking_id": reservation.bokun_booking_id,
                "cloudbeds_reservation_id": reservation.cloudbeds_reservation_id,
                "total_minor": reservation.total_minor,
                "currency": reservation.currency,
                "payment_method": reservation.payment_method,
                "customer_ref": reservation.customer.customer_ref,
                "customer_name": reservation.customer.full_name,
                "customer_email": reservation.customer.email,
                "customer_phone": reservation.customer.phone_e164,
                "component_count": len(reservation.components),
                "created_at": reservation.created_at,
                "updated_at": reservation.updated_at,
            }
        return
    if dataset == "payments":
        for payment in snapshot.payments:
            yield {
                "record_id": payment.record_id,
                "lead_id": payment.lead_id,
                "phase": payment.phase,
                "payment_id": payment.payment_id,
                "initiation_id": payment.initiation_id,
                "settlement_command_id": payment.settlement_command_id,
                "reservation_anchor_id": payment.reservation_anchor_id,
                "method": payment.method,
                "amount_due_minor": payment.amount_due_minor,
                "amount_paid_minor": payment.amount_paid_minor,
                "currency": payment.currency,
                "due_kind": payment.due_kind,
                "status_code": payment.status_code,
                "status_label": payment.status_label,
                "settled": payment.settled,
                "workflow_status": payment.workflow_status,
                "ledger_status": payment.ledger_status,
                "outcome_certainty": payment.outcome_certainty,
                "reconciliation_status": payment.reconciliation_status,
                "payment_link_prepared": payment.payment_link_prepared,
                "steps": ";".join(f"{item.step}:{item.status}" for item in payment.steps),
                "settled_at": payment.settled_at,
                "updated_at": payment.updated_at,
            }
        return
    if dataset == "handoffs":
        for handoff in snapshot.handoffs:
            yield {
                "lead_id": handoff.lead_id,
                "handoff_id": handoff.handoff_id,
                "incident_key": handoff.incident_key,
                "reason_code": handoff.reason_code,
                "status_code": handoff.status_code,
                "status_label": handoff.status_label,
                "event_count": handoff.event_count,
                "receipt_count": handoff.receipt_count,
                "created_at": handoff.created_at,
                "updated_at": handoff.updated_at,
            }
        return
    if (
        dataset != "lead-history"
        or lead_id is None
        or lead_detail is None
        or lead_detail.summary.lead_id != lead_id
    ):
        raise ValueError("lead-history requires an exact lead id")

    history: list[dict[str, object]] = []
    for fact in lead_detail.facts:
        if fact.lead_id == lead_id:
            history.append(
                {
                    "event_at": fact.persisted_at,
                    "category": "fact",
                    "record_id": fact.name,
                    "status": f"revision:{fact.revision}",
                    "name": fact.name,
                    "content": fact.value,
                }
            )
    for manifest in lead_detail.passenger_manifests:
        if lead_detail.summary.lead_id == lead_id:
            projected = passenger_manifest_public(manifest)
            history.append(
                {
                    "event_at": manifest.persisted_at,
                    "category": "passenger_manifest",
                    "record_id": f"manifest-revision-{manifest.revision}",
                    "status": f"revision:{manifest.revision}",
                    "name": "passenger_manifest",
                    "content": (
                        f"adults={projected['adults']};children={projected['children']}"
                    ),
                }
            )
            passengers = projected["passengers"]
            if type(passengers) is not list:
                raise TypeError("invalid passenger projection")
            for passenger in passengers:
                if type(passenger) is not dict:
                    raise TypeError("invalid passenger projection")
                position = passenger["position"]
                content = ";".join(
                    f"{field}={passenger[field]}"
                    for field in (
                        "full_name",
                        "birth_date",
                        "gender",
                        "country_code",
                    )
                    if passenger[field] is not None
                )
                history.append(
                    {
                        "event_at": manifest.persisted_at,
                        "category": "passenger",
                        "record_id": (
                            f"manifest-revision-{manifest.revision}:passenger-{position}"
                        ),
                        "status": f"revision:{manifest.revision}",
                        "name": passenger["participant_type"],
                        "content": content,
                    }
                )
    for turn in lead_detail.dialogue_turns:
        if turn.lead_id != lead_id:
            continue
        history.append(
            {
                "event_at": turn.committed_at,
                "category": "customer_message",
                "record_id": turn.source_turn_id,
                "status": "committed",
                "name": "customer",
                "content": turn.customer_message,
            }
        )
        for index, chunk in enumerate(turn.assistant_reply_chunks, start=1):
            history.append(
                {
                    "event_at": turn.committed_at,
                    "category": "maya_reply",
                    "record_id": f"{turn.source_turn_id}:{index}",
                    "status": "committed",
                    "name": "maya",
                    "content": chunk,
                }
            )
    for inbound in lead_detail.inbound_events:
        if inbound.lead_id == lead_id:
            history.append(
                {
                    "event_at": inbound.completed_at or inbound.occurred_at,
                    "category": "inbound_event",
                    "record_id": inbound.event_id,
                    "status": inbound.status,
                    "name": "inbound",
                    "content": "",
                }
            )
    for reply in lead_detail.public_replies:
        if reply.lead_id == lead_id:
            history.append(
                {
                    "event_at": reply.updated_at,
                    "category": "public_reply",
                    "record_id": reply.reply_id,
                    "status": reply.status,
                    "name": reply.author,
                    "content": reply.text,
                }
            )
    for reservation in lead_detail.reservations:
        if reservation.lead_id == lead_id:
            history.append(
                {
                    "event_at": reservation.updated_at,
                    "category": "reservation",
                    "record_id": reservation.command_id,
                    "status": reservation.status_code,
                    "name": reservation.operation,
                    "content": reservation.status_label,
                }
            )
    for payment in lead_detail.payments:
        if payment.lead_id == lead_id:
            history.append(
                {
                    "event_at": payment.updated_at,
                    "category": f"payment_{payment.phase}",
                    "record_id": payment.initiation_id
                    or payment.settlement_command_id
                    or payment.payment_id,
                    "status": payment.status_code,
                    "name": payment.method or "payment",
                    "content": payment.status_label,
                }
            )
    for handoff in lead_detail.handoffs:
        if handoff.lead_id == lead_id:
            history.append(
                {
                    "event_at": handoff.updated_at,
                    "category": "handoff",
                    "record_id": handoff.handoff_id,
                    "status": handoff.status_code,
                    "name": handoff.reason_code or "handoff",
                    "content": handoff.status_label,
                }
            )
    for execution in lead_detail.executions:
        if execution.lead_id == lead_id:
            history.append(
                {
                    "event_at": (
                        execution.completed_at or execution.received_at
                    ).isoformat(),
                    "category": "execution",
                    "record_id": execution.execution_id,
                    "status": execution.status,
                    "name": "execution",
                    "content": "",
                }
            )
    history.sort(
        key=lambda item: (
            str(item["event_at"]),
            str(item["category"]),
            str(item["record_id"]),
        )
    )
    yield from history


def render_csv(
    dataset: str,
    *,
    snapshot: RecordsSnapshot,
    executions: tuple[ExecutionLink, ...],
    lead_id: str | None = None,
    lead_detail: LeadDetail | None = None,
) -> bytes:
    if dataset not in DATASETS:
        raise ValueError("dataset is outside the closed catalog")
    if type(snapshot) is not RecordsSnapshot:
        raise TypeError("snapshot must be an exact RecordsSnapshot")
    if type(executions) is not tuple or any(
        type(item) is not ExecutionLink for item in executions
    ):
        raise TypeError("executions must be an exact tuple of ExecutionLink values")
    if dataset == "lead-history" and (
        type(lead_id) is not str
        or type(lead_detail) is not LeadDetail
        or lead_detail.summary.lead_id != lead_id
    ):
        raise ValueError("lead-history requires an existing exact lead id")
    if dataset != "lead-history" and (lead_id is not None or lead_detail is not None):
        raise ValueError("lead id is not accepted for this dataset")

    output = io.StringIO(newline="")
    columns = _COLUMNS[dataset]
    writer = csv.DictWriter(
        output,
        fieldnames=columns,
        extrasaction="raise",
        lineterminator="\r\n",
    )
    writer.writeheader()
    for row in _rows(
        dataset,
        snapshot=snapshot,
        executions=executions,
        lead_id=lead_id,
        lead_detail=lead_detail,
    ):
        writer.writerow({column: _cell(row.get(column)) for column in columns})
    return b"\xef\xbb\xbf" + output.getvalue().encode("utf-8")
