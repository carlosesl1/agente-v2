"""Deterministic bridge from V2 public reads into authenticated Phase 8 artifacts."""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, time
from decimal import Decimal

from reservation_boundary.conversation import (
    ConversationProjection,
    SourceEventIdentity,
)
from reservation_boundary.reads import (
    PUBLIC_READ_POLICY_HASH,
    PUBLIC_READ_POLICY_ID,
    Phase8ToolReadRequest,
    ReadEvidenceDisposition,
    ReadEvidenceReceipt,
    ReadObservation,
    ReadObservationStatus,
    ReadService,
    SanitizedLookupResult,
    SanitizedLookupStatus,
    SanitizedOffer,
)
from reservation_boundary.types import ActivityReadArguments, LodgingReadArguments
from reservation_domain import Party, SearchQuery, ServiceKind
from v2_contracts.providers import (
    ReadKind,
    ReadRequest,
)
from v2_contracts.providers import (
    ReadObservation as V2ReadObservation,
)


class ReadBridgeError(ValueError):
    """A V2 observation cannot be represented by the closed Phase 8 read wire."""


def _canonical(schema: str, data: dict[str, object]) -> bytes:
    return json.dumps(
        {"schema": schema, "version": 1, "data": data},
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


def _domain_hash(domain: str, payload: bytes) -> str:
    return hashlib.sha256(domain.encode("ascii") + b"\x00" + payload).hexdigest()


def _public_payload_hash(value: object) -> str:
    return hashlib.sha256(
        json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    ).hexdigest()


def _payload_options(observation: V2ReadObservation) -> tuple[dict[str, object], ...]:
    payload = observation.public_payload
    options = payload.get("options")
    if options is None:
        return (payload,)
    if type(options) is not list or any(type(item) is not dict for item in options):
        raise ReadBridgeError("public read options are not exact objects")
    return tuple(options)


def _minute(value: object) -> time | None:
    if value is None:
        return None
    if type(value) is not str:
        raise ReadBridgeError("activity start_time must be exact text")
    try:
        parsed = time.fromisoformat(value)
    except ValueError as exc:
        raise ReadBridgeError("activity start_time is invalid") from exc
    if parsed.second or parsed.microsecond or parsed.tzinfo is not None:
        raise ReadBridgeError("activity start_time must have minute precision")
    return parsed


def _offers(
    request: ReadRequest,
    observation: V2ReadObservation,
) -> tuple[SanitizedOffer, ...]:
    result = []
    for option in _payload_options(observation):
        if option.get("available") is not True:
            continue
        try:
            if request.kind is ReadKind.LODGING:
                offer = SanitizedOffer(
                    offer_id=option["offer_id"],
                    service=ReadService.LODGING,
                    public_label=option["room_public_name"],
                    start_date=request.check_in,
                    end_date=request.check_out,
                    start_time=None,
                    adults=request.adults,
                    children=request.children,
                    total_amount=Decimal(option["total_amount"]),
                    currency=option["currency"],
                )
            elif request.kind is ReadKind.ACTIVITY:
                offer = SanitizedOffer(
                    offer_id=option["offer_id"],
                    service=ReadService.ACTIVITY,
                    public_label=option["product_public_name"],
                    start_date=request.activity_date,
                    end_date=None,
                    start_time=None,
                    adults=request.participants,
                    children=0,
                    total_amount=Decimal(option["total_amount"]),
                    currency=option["currency"],
                )
            else:
                raise ReadBridgeError("read kind is outside the availability bridge")
        except (KeyError, TypeError, ValueError) as exc:
            if type(exc) is ReadBridgeError:
                raise
            raise ReadBridgeError("public offer cannot enter the Phase 8 wire") from exc
        result.append(offer)
    return tuple(sorted(result, key=lambda item: item.offer_id))


def _phase8_request(
    request: ReadRequest,
    *,
    lead_id: str,
    aggregate_turn_id: str,
    source_event: SourceEventIdentity,
    deadline_at: datetime,
    locale: str,
    projection: ConversationProjection,
) -> Phase8ToolReadRequest:
    if request.kind is ReadKind.LODGING:
        arguments = LodgingReadArguments(
            request.check_in,
            request.check_out,
            request.adults,
            request.children,
        )
        tool_name = "cloudbeds_consultar_hospedagem_v2"
    elif request.kind is ReadKind.ACTIVITY:
        arguments = ActivityReadArguments(
            request.product_id,
            request.activity_date,
            request.participants,
        )
        tool_name = "bokun_consultar_passeio_v2"
    else:
        raise ReadBridgeError("read kind is outside the availability bridge")
    return Phase8ToolReadRequest(
        tool_name=tool_name,
        arguments=arguments,
        lead_key_hash=_domain_hash("phase8-lead-key-v1", lead_id.encode("utf-8")),
        aggregate_turn_id=aggregate_turn_id,
        source_event=source_event,
        deadline_at=deadline_at,
        locale=locale,
        projection_hash=projection.canonical_hash(),
    )


def _evidence_receipt(
    *,
    request_hash: str,
    result_content_hash: str,
    source_evidence_hash: str,
    observed_at: datetime,
    expires_at: datetime,
) -> ReadEvidenceReceipt:
    without_id = {
        "request_hash": request_hash,
        "result_content_hash": result_content_hash,
        "source_evidence_hash": source_evidence_hash,
        "policy_id": PUBLIC_READ_POLICY_ID,
        "policy_hash": PUBLIC_READ_POLICY_HASH,
        "disposition": ReadEvidenceDisposition.PUBLIC_SAFE.value,
        "observed_at": observed_at.isoformat(),
        "expires_at": expires_at.isoformat(),
    }
    preimage = _canonical(ReadEvidenceReceipt.ID_PREIMAGE_SCHEMA, without_id)
    receipt_id = "read-evidence:" + _domain_hash(
        ReadEvidenceReceipt.RECEIPT_ID_DOMAIN,
        preimage,
    )
    return ReadEvidenceReceipt(
        receipt_id=receipt_id,
        request_hash=request_hash,
        result_content_hash=result_content_hash,
        source_evidence_hash=source_evidence_hash,
        policy_id=PUBLIC_READ_POLICY_ID,
        policy_hash=PUBLIC_READ_POLICY_HASH,
        disposition=ReadEvidenceDisposition.PUBLIC_SAFE,
        observed_at=observed_at,
        expires_at=expires_at,
    )


def bridge_availability_observation(
    request: ReadRequest,
    observation: V2ReadObservation,
    *,
    lead_id: str,
    aggregate_turn_id: str,
    source_event: SourceEventIdentity,
    deadline_at: datetime,
    locale: str,
    projection: ConversationProjection,
    frame_commitment_hash: str,
) -> ReadObservation:
    """Bind one already-sanitized V2 availability result to the canonical v8 wire."""
    if type(request) is not ReadRequest or type(observation) is not V2ReadObservation:
        raise TypeError("request/observation must be exact V2 read contracts")
    if observation.request_hash != request.canonical_hash():
        raise ReadBridgeError("V2 observation does not bind its request")
    phase8_request = _phase8_request(
        request,
        lead_id=lead_id,
        aggregate_turn_id=aggregate_turn_id,
        source_event=source_event,
        deadline_at=deadline_at,
        locale=locale,
        projection=projection,
    )
    request_hash = phase8_request.read_request_hash()
    offers = _offers(request, observation)
    service = (
        ReadService.LODGING
        if request.kind is ReadKind.LODGING
        else ReadService.ACTIVITY
    )
    status = (
        SanitizedLookupStatus.POSITIVE if offers else SanitizedLookupStatus.NEGATIVE
    )
    lookup_id = "lookup:" + _domain_hash(
        "phase8-v2-lookup-id-v1",
        observation.request_hash.encode("ascii"),
    )
    if request.kind is ReadKind.LODGING:
        query_signature = SearchQuery(
            service=ServiceKind.LODGING,
            start_date=request.check_in,
            end_date=request.check_out,
            start_time=None,
            party=Party(request.adults, request.children),
        ).signature
    else:
        query_signature = SearchQuery(
            service=ServiceKind.ACTIVITY,
            start_date=request.activity_date,
            end_date=None,
            start_time=None,
            party=Party(request.participants, 0),
        ).signature
    snapshot_hash = _public_payload_hash(observation.public_payload)
    content_data = {
        "request_hash": request_hash,
        "service": service.value,
        "status": status.value,
        "query_signature": query_signature,
        "lookup_id": lookup_id,
        "observed_at": observation.observed_at.isoformat(),
        "expires_at": observation.expires_at.isoformat(),
        "snapshot_hash": snapshot_hash,
        "offers": [json.loads(item.to_canonical_bytes()) for item in offers],
        "failure_codes": [],
    }
    content_preimage = _canonical(
        SanitizedLookupResult.CONTENT_PREIMAGE_SCHEMA,
        content_data,
    )
    result_content_hash = _domain_hash(
        ReadEvidenceReceipt.RESULT_CONTENT_DOMAIN,
        content_preimage,
    )
    evidence = _evidence_receipt(
        request_hash=request_hash,
        result_content_hash=result_content_hash,
        source_evidence_hash=observation.private_binding_hash,
        observed_at=observation.observed_at,
        expires_at=observation.expires_at,
    )
    result = SanitizedLookupResult(
        request_hash=request_hash,
        service=service,
        status=status,
        query_signature=query_signature,
        lookup_id=lookup_id,
        observed_at=observation.observed_at,
        expires_at=observation.expires_at,
        snapshot_hash=snapshot_hash,
        offers=offers,
        failure_codes=(),
        evidence_receipt=evidence,
    )
    return ReadObservation(
        request_bytes=phase8_request.to_canonical_bytes(),
        request_hash=request_hash,
        status=(
            ReadObservationStatus.POSITIVE
            if status is SanitizedLookupStatus.POSITIVE
            else ReadObservationStatus.NEGATIVE
        ),
        typed_result_bytes=result.to_canonical_bytes(),
        result_hash=result.canonical_hash(),
        derived_facts=(),
        safe_for_public_claims=True,
        frame_commitment_hash=frame_commitment_hash,
    )


__all__ = ["ReadBridgeError", "bridge_availability_observation"]
