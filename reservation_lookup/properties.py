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

from .identity import lookup_id_for, offer_id_for
from .selection import (
    SelectionErrorCode,
    SelectionRejected,
    revalidate_offer,
    select_offer,
)
from .types import LookupProvenance, LookupResult, ProviderKind

_BASE_TIME = datetime(2027, 1, 1, tzinfo=timezone.utc)
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
            "mutation_counts": dict(sorted(self.mutation_counts.items())),
            "false_authorizations": self.false_authorizations,
            "missed_invalidations": self.missed_invalidations,
            "unexpected_exceptions": self.unexpected_exceptions,
            "violations": list(self.violations),
        }


def run_lookup_properties(*, cases: int, seed: int) -> Phase3PropertyReport:
    if type(cases) is not int or cases <= 0:
        raise ValueError("cases must be a positive integer")
    if type(seed) is not int or isinstance(seed, bool):
        raise TypeError("seed must be an integer")

    counters = {
        "positive_authorizations": 0,
        "label_equivalence_cases": 0,
        "executable_mutation_cases": 0,
        "expired_cases": 0,
        "zero_match_cases": 0,
        "multiple_match_cases": 0,
        "false_authorizations": 0,
        "missed_invalidations": 0,
        "unexpected_exceptions": 0,
    }
    mutation_counts = {name: 0 for name in _MUTATION_KINDS}
    violations: list[str] = []

    for index in range(cases):
        observed_at = _BASE_TIME + timedelta(seconds=index * 10)
        base = _positive_result(index=index, observed_at=observed_at)
        offer = base.offers[0]

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

        relabeled = _positive_result(
            index=index,
            observed_at=observed_at + timedelta(seconds=2),
            label=_label_variant(index),
        )
        try:
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

        mutation_kind = _MUTATION_KINDS[(index + seed) % len(_MUTATION_KINDS)]
        mutation_counts[mutation_kind] += 1
        mutated = _mutated_result(
            index=index,
            observed_at=observed_at + timedelta(seconds=3),
            kind=mutation_kind,
        )
        try:
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
            at=base.evidence.expires_at + timedelta(microseconds=1),
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

    return Phase3PropertyReport(
        cases=cases,
        seed=seed,
        positive_authorizations=counters["positive_authorizations"],
        label_equivalence_cases=counters["label_equivalence_cases"],
        executable_mutation_cases=counters["executable_mutation_cases"],
        expired_cases=counters["expired_cases"],
        zero_match_cases=counters["zero_match_cases"],
        multiple_match_cases=counters["multiple_match_cases"],
        mutation_counts=mutation_counts,
        false_authorizations=counters["false_authorizations"],
        missed_invalidations=counters["missed_invalidations"],
        unexpected_exceptions=counters["unexpected_exceptions"],
        violations=tuple(violations),
    )


def _base_query(
    *, provider: ProviderKind, party: Party | None = None
) -> SearchQuery:
    resolved_party = party or Party(adults=2, children=0)
    if provider is ProviderKind.CLOUDBEDS:
        return SearchQuery(
            service=ServiceKind.LODGING,
            start_date=date(2027, 2, 1),
            end_date=date(2027, 2, 3),
            start_time=None,
            party=resolved_party,
        )
    return SearchQuery(
        service=ServiceKind.ACTIVITY,
        start_date=date(2027, 2, 1),
        end_date=date(2027, 2, 8),
        start_time=None,
        party=resolved_party,
    )


def _positive_result(
    *,
    index: int,
    observed_at: datetime,
    label: str = "Passeio nº 2",
    provider: ProviderKind = ProviderKind.BOKUN,
    provider_ref: str | None = None,
    offer_date: date = date(2027, 2, 1),
    start_time: str = "07:30",
    party: Party | None = None,
    amount: Decimal = Decimal("500.00"),
    currency: str = "BRL",
) -> LookupResult:
    resolved_party = party or Party(adults=2, children=0)
    query = _base_query(provider=provider, party=resolved_party)
    provenance = _provenance(index=index, observed_at=observed_at, provider=provider)
    lookup_id = lookup_id_for(
        provider=provider,
        query=query,
        observed_at=observed_at,
        response_hashes=provenance.response_hashes,
    )
    if provider is ProviderKind.CLOUDBEDS:
        resolved_ref = provider_ref or "cloudbeds.room.100.rate.standard"
        service = ServiceKind.LODGING
        resolved_end_date = query.end_date
        resolved_start_time = None
        resolved_label = "Quarto nº 2" if label == "Passeio nº 2" else label
    else:
        resolved_ref = (
            provider_ref or "bokun.product.100.start.0730.rate.standard"
        )
        service = ServiceKind.ACTIVITY
        resolved_end_date = None
        resolved_start_time = start_time
        resolved_label = label
    base = OfferSnapshot(
        offer_id="offer:pending",
        lookup_id=lookup_id,
        service=service,
        provider_ref=resolved_ref,
        public_label=resolved_label,
        start_date=offer_date,
        end_date=resolved_end_date,
        start_time=resolved_start_time,
        party=resolved_party,
        total=Money(amount=amount, currency=currency),
        available=True,
    )
    offer = replace(base, offer_id=offer_id_for(provider=provider, offer=base))
    evidence = LookupEvidence(
        lookup_id=lookup_id,
        service=query.service,
        query_signature=query.signature,
        observed_at=observed_at,
        expires_at=observed_at + timedelta(minutes=5),
        snapshot_hash=provenance.snapshot_hash,
        status=LookupStatus.POSITIVE,
    )
    return LookupResult(
        query=query,
        evidence=evidence,
        provenance=provenance,
        offers=(offer,),
    )


def _negative_result(*, index: int, observed_at: datetime) -> LookupResult:
    provider = ProviderKind.BOKUN
    query = _base_query(provider=provider)
    provenance = _provenance(index=index, observed_at=observed_at, provider=provider)
    lookup_id = lookup_id_for(
        provider=provider,
        query=query,
        observed_at=observed_at,
        response_hashes=provenance.response_hashes,
    )
    return LookupResult(
        query=query,
        evidence=LookupEvidence(
            lookup_id=lookup_id,
            service=query.service,
            query_signature=query.signature,
            observed_at=observed_at,
            expires_at=observed_at + timedelta(minutes=5),
            snapshot_hash=provenance.snapshot_hash,
            status=LookupStatus.NEGATIVE,
        ),
        provenance=provenance,
        offers=(),
    )


def _mutated_result(*, index: int, observed_at: datetime, kind: str) -> LookupResult:
    if kind == "provider":
        return _positive_result(
            index=index,
            observed_at=observed_at,
            provider=ProviderKind.CLOUDBEDS,
        )
    if kind == "provider_ref":
        return _positive_result(
            index=index,
            observed_at=observed_at,
            provider_ref="bokun.product.101.start.0730.rate.standard",
        )
    if kind == "date":
        return _positive_result(
            index=index,
            observed_at=observed_at,
            offer_date=date(2027, 2, 2),
        )
    if kind == "time":
        return _positive_result(
            index=index,
            observed_at=observed_at,
            start_time="08:00",
        )
    if kind == "party":
        return _positive_result(
            index=index,
            observed_at=observed_at,
            party=Party(adults=3, children=0),
        )
    if kind == "amount":
        return _positive_result(
            index=index,
            observed_at=observed_at,
            amount=Decimal("501.00"),
        )
    if kind == "currency":
        return _positive_result(
            index=index,
            observed_at=observed_at,
            currency="USD",
        )
    if kind == "availability":
        return _negative_result(index=index, observed_at=observed_at)
    raise AssertionError(f"unknown mutation kind: {kind}")


def _provenance(
    *, index: int, observed_at: datetime, provider: ProviderKind
) -> LookupProvenance:
    suffix = observed_at.isoformat()
    return LookupProvenance(
        provider=provider,
        request_fingerprints=(
            _digest(f"request:0:{index}:{suffix}"),
            _digest(f"request:1:{index}:{suffix}"),
        ),
        response_hashes=(
            _digest(f"response:0:{index}:{suffix}"),
            _digest(f"response:1:{index}:{suffix}"),
        ),
    )


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


def _label_variant(index: int) -> str:
    return (
        "PASSEIO N° 2",
        "Passeio n.º 2",
        "  Passeio   nº 2  ",
        "Passeio 2",
    )[index % 4]


def _digest(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _violate(violations: list[str], index: int, detail: str) -> None:
    if len(violations) < 50:
        violations.append(f"case_{index}:{detail}")
