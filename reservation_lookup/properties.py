from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
import hashlib

from reservation_domain import (
    LookupEvidence,
    LookupStatus,
    Money,
    OfferSnapshot,
    Party,
    SearchQuery,
    ServiceKind,
)

from .bokun import BokunReadAdapter
from .cloudbeds import CloudbedsReadAdapter
from .identity import offer_id_for
from .selection import (
    SelectionErrorCode,
    SelectionRejected,
    revalidate_offer,
    select_offer,
)
from .types import (
    BokunLookupRequest,
    CloudbedsLookupRequest,
    LookupProvenance,
    LookupResult,
    ProviderKind,
    ReadResponse,
)

_BASE_TIME = datetime(2027, 1, 1, tzinfo=timezone.utc)
_BASE_DATE = date(2027, 2, 1)
_MUTATION_KINDS = (
    "provider",
    "provider_ref",
    "date",
    "time",
    "party",
    "amount",
    "currency",
    "availability",
)


@dataclass(frozen=True, slots=True)
class Phase3PropertyReport:
    cases: int
    seed: int
    positive_authorizations: int
    label_equivalence_cases: int
    executable_mutation_cases: int
    expired_cases: int
    zero_match_cases: int
    multiple_match_cases: int
    cloudbeds_adapter_cases: int
    bokun_adapter_cases: int
    cross_target_rejections: int
    lookup_rebinding_rejections: int
    response_pair_swap_rejections: int
    zero_total_rejections: int
    mutation_counts: dict[str, int]
    false_authorizations: int
    missed_invalidations: int
    unexpected_exceptions: int
    violations: tuple[str, ...]

    def to_dict(self) -> dict:
        return {
            "cases": self.cases,
            "seed": self.seed,
            "positive_authorizations": self.positive_authorizations,
            "label_equivalence_cases": self.label_equivalence_cases,
            "executable_mutation_cases": self.executable_mutation_cases,
            "expired_cases": self.expired_cases,
            "zero_match_cases": self.zero_match_cases,
            "multiple_match_cases": self.multiple_match_cases,
            "cloudbeds_adapter_cases": self.cloudbeds_adapter_cases,
            "bokun_adapter_cases": self.bokun_adapter_cases,
            "cross_target_rejections": self.cross_target_rejections,
            "lookup_rebinding_rejections": self.lookup_rebinding_rejections,
            "response_pair_swap_rejections": self.response_pair_swap_rejections,
            "zero_total_rejections": self.zero_total_rejections,
            "mutation_counts": dict(sorted(self.mutation_counts.items())),
            "false_authorizations": self.false_authorizations,
            "missed_invalidations": self.missed_invalidations,
            "unexpected_exceptions": self.unexpected_exceptions,
            "violations": list(self.violations),
        }


class _Responses:
    def __init__(self, responses: tuple[ReadResponse, ...]):
        self._responses = list(responses)

    def send(self, request):
        if not self._responses:
            raise RuntimeError("synthetic response underflow")
        return self._responses.pop(0)


def run_lookup_properties(*, cases: int, seed: int) -> Phase3PropertyReport:
    if type(cases) is not int or cases <= 0:
        raise ValueError("cases must be a positive integer")
    if type(seed) is not int or isinstance(seed, bool):
        raise TypeError("seed must be an integer")

    counter_names = (
        "positive_authorizations",
        "label_equivalence_cases",
        "executable_mutation_cases",
        "expired_cases",
        "zero_match_cases",
        "multiple_match_cases",
        "cloudbeds_adapter_cases",
        "bokun_adapter_cases",
        "cross_target_rejections",
        "lookup_rebinding_rejections",
        "response_pair_swap_rejections",
        "zero_total_rejections",
        "false_authorizations",
        "missed_invalidations",
        "unexpected_exceptions",
    )
    counters = {name: 0 for name in counter_names}
    mutation_counts = {name: 0 for name in _MUTATION_KINDS}
    violations: list[str] = []

    for index in range(cases):
        observed_at = _BASE_TIME + timedelta(seconds=index * 10)
        mutation_kind = _MUTATION_KINDS[(index + seed) % len(_MUTATION_KINDS)]
        provider = _provider_for_case(index=index, seed=seed, kind=mutation_kind)
        mutation_counts[mutation_kind] += 1
        try:
            base = _adapter_result(
                index=index,
                observed_at=observed_at,
                provider=provider,
            )
            offer = base.offers[0]
            counters[f"{provider.value}_adapter_cases"] += 1
        except Exception as exc:
            counters["unexpected_exceptions"] += 1
            _violate(violations, index, f"base_adapter_exception:{type(exc).__name__}")
            continue

        try:
            selected = select_offer(
                base,
                offer_id=offer.offer_id,
                at=observed_at + timedelta(seconds=1),
            )
            if selected != offer:
                _violate(violations, index, "exact_selection_returned_other_offer")
            else:
                counters["positive_authorizations"] += 1
        except Exception as exc:
            counters["unexpected_exceptions"] += 1
            _violate(violations, index, f"exact_selection_exception:{type(exc).__name__}")

        try:
            relabeled = _adapter_result(
                index=index,
                observed_at=observed_at + timedelta(seconds=2),
                provider=provider,
                label=_label_variant(index=index, provider=provider),
            )
            selected = revalidate_offer(
                offer,
                relabeled,
                at=observed_at + timedelta(seconds=2),
            )
            if selected.offer_id != offer.offer_id:
                _violate(violations, index, "label_changed_identity")
            else:
                counters["label_equivalence_cases"] += 1
        except Exception as exc:
            counters["unexpected_exceptions"] += 1
            _violate(violations, index, f"label_revalidation_exception:{type(exc).__name__}")

        try:
            mutated = _mutated_result(
                index=index,
                observed_at=observed_at + timedelta(seconds=3),
                provider=provider,
                kind=mutation_kind,
            )
            revalidate_offer(
                offer,
                mutated,
                at=observed_at + timedelta(seconds=3),
            )
        except SelectionRejected as exc:
            expected = (
                SelectionErrorCode.LOOKUP_NOT_POSITIVE
                if mutation_kind == "availability"
                else SelectionErrorCode.OFFER_CHANGED
            )
            if exc.code is expected:
                counters["executable_mutation_cases"] += 1
            else:
                _violate(
                    violations,
                    index,
                    f"mutation_{mutation_kind}_wrong_code:{exc.code.value}",
                )
        except Exception as exc:
            counters["unexpected_exceptions"] += 1
            _violate(violations, index, f"mutation_exception:{type(exc).__name__}")
        else:
            counters["false_authorizations"] += 1
            counters["missed_invalidations"] += 1
            _violate(violations, index, f"mutation_{mutation_kind}_authorized")

        _expect_rejection(
            result=base,
            offer_id=offer.offer_id,
            at=base.evidence.expires_at,
            expected=SelectionErrorCode.LOOKUP_EXPIRED,
            counter="expired_cases",
            index=index,
            counters=counters,
            violations=violations,
        )
        _expect_rejection(
            result=base,
            offer_id="offer:" + _digest(f"zero:{seed}:{index}"),
            at=observed_at + timedelta(seconds=1),
            expected=SelectionErrorCode.OFFER_ID_NOT_FOUND,
            counter="zero_match_cases",
            index=index,
            counters=counters,
            violations=violations,
        )
        duplicated = replace(base, offers=(offer, offer))
        _expect_rejection(
            result=duplicated,
            offer_id=offer.offer_id,
            at=observed_at + timedelta(seconds=1),
            expected=SelectionErrorCode.OFFER_ID_NOT_UNIQUE,
            counter="multiple_match_cases",
            index=index,
            counters=counters,
            violations=violations,
        )
        _expect_cross_target_rejection(
            base=base,
            index=index,
            observed_at=observed_at,
            provider=provider,
            counters=counters,
            violations=violations,
        )
        _expect_lookup_rebinding_rejection(
            base=base,
            index=index,
            counters=counters,
            violations=violations,
        )
        _expect_response_pair_swap_rejection(
            base=base,
            index=index,
            counters=counters,
            violations=violations,
        )
        _expect_zero_total_rejection(
            base=base,
            index=index,
            counters=counters,
            violations=violations,
        )

    return Phase3PropertyReport(
        cases=cases,
        seed=seed,
        positive_authorizations=counters["positive_authorizations"],
        label_equivalence_cases=counters["label_equivalence_cases"],
        executable_mutation_cases=counters["executable_mutation_cases"],
        expired_cases=counters["expired_cases"],
        zero_match_cases=counters["zero_match_cases"],
        multiple_match_cases=counters["multiple_match_cases"],
        cloudbeds_adapter_cases=counters["cloudbeds_adapter_cases"],
        bokun_adapter_cases=counters["bokun_adapter_cases"],
        cross_target_rejections=counters["cross_target_rejections"],
        lookup_rebinding_rejections=counters["lookup_rebinding_rejections"],
        response_pair_swap_rejections=counters["response_pair_swap_rejections"],
        zero_total_rejections=counters["zero_total_rejections"],
        mutation_counts=mutation_counts,
        false_authorizations=counters["false_authorizations"],
        missed_invalidations=counters["missed_invalidations"],
        unexpected_exceptions=counters["unexpected_exceptions"],
        violations=tuple(violations),
    )


def _provider_for_case(*, index: int, seed: int, kind: str) -> ProviderKind:
    if kind == "currency":
        return ProviderKind.CLOUDBEDS
    if kind in {"date", "time"}:
        return ProviderKind.BOKUN
    return (
        ProviderKind.CLOUDBEDS
        if (index + seed) % 2 == 0
        else ProviderKind.BOKUN
    )


def _adapter_result(
    *,
    index: int,
    observed_at: datetime,
    provider: ProviderKind,
    label: str | None = None,
    target_variant: int = 0,
    ref_variant: int = 0,
    offer_date: date = _BASE_DATE,
    start_time: str = "07:30",
    party: Party | None = None,
    amount: Decimal = Decimal("500.00"),
    currency: str = "BRL",
    available: bool = True,
) -> LookupResult:
    resolved_party = party or Party(adults=2, children=0)
    if provider is ProviderKind.CLOUDBEDS:
        query = SearchQuery(
            service=ServiceKind.LODGING,
            start_date=offer_date,
            end_date=offer_date + timedelta(days=2),
            start_time=None,
            party=resolved_party,
        )
        property_id = f"property.{index}.{target_variant}"
        room_id = f"ROOM{index}_{ref_variant}"
        rate_id = "RATE1"
        night = amount / Decimal("2")
        room_data = []
        if available:
            room_data = [
                {
                    "roomTypeID": room_id,
                    "roomTypeName": label or "Quarto sintético",
                    "roomsAvailable": 2,
                    "ratePlanID": rate_id,
                    "currency": currency,
                    "roomRateDetailed": [
                        {
                            "date": offer_date.isoformat(),
                            "rate": format(night, "f"),
                            "currency": currency,
                            "roomsAvailable": 2,
                        },
                        {
                            "date": (offer_date + timedelta(days=1)).isoformat(),
                            "rate": format(night, "f"),
                            "currency": currency,
                            "roomsAvailable": 2,
                        },
                    ],
                }
            ]
        transport = _Responses(
            (
                ReadResponse(200, {"success": True, "data": room_data}),
                ReadResponse(
                    200,
                    {
                        "success": True,
                        "data": [
                            {"ratePlanID": rate_id, "ratePlanName": "Sintético"}
                        ],
                    },
                ),
            )
        )
        result = CloudbedsReadAdapter(transport).lookup(
            CloudbedsLookupRequest(property_id=property_id, query=query),
            observed_at=observed_at,
            ttl=timedelta(minutes=5),
        )
    else:
        query = SearchQuery(
            service=ServiceKind.ACTIVITY,
            start_date=_BASE_DATE,
            end_date=_BASE_DATE + timedelta(days=7),
            start_time=None,
            party=resolved_party,
        )
        product_id = f"PRODUCT{index}_{target_variant}"
        start_time_id = f"START{index}_{ref_variant}"
        availability_data = []
        if available:
            availability_data = [
                {
                    "date": offer_date.isoformat(),
                    "startTimeId": start_time_id,
                    "startTime": start_time,
                    "availabilityCount": 8,
                    "available": True,
                    "soldOut": False,
                    "unavailable": False,
                    "totalAmount": format(amount, "f"),
                    "currency": currency,
                    "defaultRateId": "RATE1",
                }
            ]
        transport = _Responses(
            (
                ReadResponse(200, {"id": product_id, "title": label or "Passeio sintético"}),
                ReadResponse(200, {"success": True, "data": availability_data}),
            )
        )
        result = BokunReadAdapter(transport).lookup(
            BokunLookupRequest(product_id=product_id, query=query),
            observed_at=observed_at,
            ttl=timedelta(minutes=5),
        )
    expected = LookupStatus.POSITIVE if available else LookupStatus.NEGATIVE
    if result.evidence.status is not expected:
        raise AssertionError(
            f"synthetic {provider.value} adapter returned {result.evidence.status.value}"
        )
    if available and len(result.offers) != 1:
        raise AssertionError("synthetic positive adapter must return one offer")
    return result


def _mutated_result(
    *,
    index: int,
    observed_at: datetime,
    provider: ProviderKind,
    kind: str,
) -> LookupResult:
    if kind == "provider":
        other = (
            ProviderKind.BOKUN
            if provider is ProviderKind.CLOUDBEDS
            else ProviderKind.CLOUDBEDS
        )
        return _adapter_result(
            index=index,
            observed_at=observed_at,
            provider=other,
        )
    if kind == "provider_ref":
        return _adapter_result(
            index=index,
            observed_at=observed_at,
            provider=provider,
            ref_variant=1,
        )
    if kind == "date":
        return _adapter_result(
            index=index,
            observed_at=observed_at,
            provider=ProviderKind.BOKUN,
            offer_date=_BASE_DATE + timedelta(days=1),
        )
    if kind == "time":
        return _adapter_result(
            index=index,
            observed_at=observed_at,
            provider=ProviderKind.BOKUN,
            start_time="08:00",
        )
    if kind == "party":
        return _adapter_result(
            index=index,
            observed_at=observed_at,
            provider=provider,
            party=Party(adults=3, children=0),
        )
    if kind == "amount":
        return _adapter_result(
            index=index,
            observed_at=observed_at,
            provider=provider,
            amount=Decimal("501.00"),
        )
    if kind == "currency":
        return _adapter_result(
            index=index,
            observed_at=observed_at,
            provider=ProviderKind.CLOUDBEDS,
            currency="USD",
        )
    if kind == "availability":
        return _adapter_result(
            index=index,
            observed_at=observed_at,
            provider=provider,
            available=False,
        )
    raise AssertionError(f"unknown mutation kind: {kind}")


def _expect_cross_target_rejection(
    *,
    base: LookupResult,
    index: int,
    observed_at: datetime,
    provider: ProviderKind,
    counters: dict[str, int],
    violations: list[str],
) -> None:
    try:
        other_target = _adapter_result(
            index=index,
            observed_at=observed_at + timedelta(seconds=4),
            provider=provider,
            target_variant=1,
        )
        if other_target.offers[0].offer_id == base.offers[0].offer_id:
            raise AssertionError("target change preserved offer identity")
        revalidate_offer(
            base.offers[0],
            other_target,
            at=observed_at + timedelta(seconds=4),
        )
    except SelectionRejected as exc:
        if exc.code is SelectionErrorCode.OFFER_CHANGED:
            counters["cross_target_rejections"] += 1
        else:
            _violate(violations, index, f"cross_target_wrong_code:{exc.code.value}")
    except Exception as exc:
        counters["unexpected_exceptions"] += 1
        _violate(violations, index, f"cross_target_exception:{type(exc).__name__}")
    else:
        counters["false_authorizations"] += 1
        _violate(violations, index, "cross_target_authorized")


def _expect_lookup_rebinding_rejection(
    *,
    base: LookupResult,
    index: int,
    counters: dict[str, int],
    violations: list[str],
) -> None:
    rebound_id = "lookup:" + _digest(f"rebound:{index}")
    evidence = replace(base.evidence, lookup_id=rebound_id)
    offers = tuple(replace(offer, lookup_id=rebound_id) for offer in base.offers)
    try:
        replace(base, evidence=evidence, offers=offers)
    except ValueError:
        counters["lookup_rebinding_rejections"] += 1
    except Exception as exc:
        counters["unexpected_exceptions"] += 1
        _violate(violations, index, f"lookup_rebind_exception:{type(exc).__name__}")
    else:
        counters["false_authorizations"] += 1
        _violate(violations, index, "lookup_rebind_accepted")


def _expect_response_pair_swap_rejection(
    *,
    base: LookupResult,
    index: int,
    counters: dict[str, int],
    violations: list[str],
) -> None:
    swapped = LookupProvenance(
        provider=base.provenance.provider,
        request_fingerprints=base.provenance.request_fingerprints,
        response_hashes=tuple(reversed(base.provenance.response_hashes)),
    )
    if swapped.snapshot_hash != base.provenance.snapshot_hash:
        counters["response_pair_swap_rejections"] += 1
    else:
        counters["missed_invalidations"] += 1
        _violate(violations, index, "response_pair_swap_preserved_snapshot")


def _expect_zero_total_rejection(
    *,
    base: LookupResult,
    index: int,
    counters: dict[str, int],
    violations: list[str],
) -> None:
    original = base.offers[0]
    zero_base = replace(
        original,
        total=Money(amount=Decimal("0.00"), currency=original.total.currency),
    )
    zero = replace(
        zero_base,
        offer_id=offer_id_for(provider=base.provenance.provider, offer=zero_base),
    )
    try:
        replace(base, offers=(zero,))
    except ValueError:
        counters["zero_total_rejections"] += 1
    except Exception as exc:
        counters["unexpected_exceptions"] += 1
        _violate(violations, index, f"zero_total_exception:{type(exc).__name__}")
    else:
        counters["false_authorizations"] += 1
        _violate(violations, index, "zero_total_authorized")


def _expect_rejection(
    *,
    result: LookupResult,
    offer_id: str,
    at: datetime,
    expected: SelectionErrorCode,
    counter: str,
    index: int,
    counters: dict[str, int],
    violations: list[str],
) -> None:
    try:
        select_offer(result, offer_id=offer_id, at=at)
    except SelectionRejected as exc:
        if exc.code is expected:
            counters[counter] += 1
        else:
            _violate(violations, index, f"{counter}_wrong_code:{exc.code.value}")
    except Exception as exc:
        counters["unexpected_exceptions"] += 1
        _violate(violations, index, f"{counter}_exception:{type(exc).__name__}")
    else:
        counters["false_authorizations"] += 1
        _violate(violations, index, f"{counter}_authorized")


def _label_variant(*, index: int, provider: ProviderKind) -> str:
    if provider is ProviderKind.CLOUDBEDS:
        return (
            "QUARTO SINTÉTICO",
            "Quarto sintético relabel",
            "  Quarto   sintético  ",
            "Quarto de teste",
        )[index % 4]
    return (
        "PASSEIO SINTÉTICO",
        "Passeio sintético relabel",
        "  Passeio   sintético  ",
        "Passeio de teste",
    )[index % 4]


def _digest(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _violate(violations: list[str], index: int, detail: str) -> None:
    if len(violations) < 50:
        violations.append(f"case_{index}:{detail}")
