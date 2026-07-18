from __future__ import annotations

from datetime import datetime
from enum import Enum
import re

from reservation_domain import LookupStatus, OfferSnapshot

from .types import LookupResult

_OFFER_ID_RE = re.compile(r"^offer:[a-f0-9]{64}$")


class SelectionErrorCode(str, Enum):
    LOOKUP_NOT_POSITIVE = "lookup_not_positive"
    LOOKUP_EXPIRED = "lookup_expired"
    OFFER_ID_NOT_FOUND = "offer_id_not_found"
    OFFER_ID_NOT_UNIQUE = "offer_id_not_unique"
    OFFER_CHANGED = "offer_changed"


class SelectionRejected(ValueError):
    def __init__(self, code: SelectionErrorCode):
        if type(code) is not SelectionErrorCode:
            raise TypeError("code must be SelectionErrorCode")
        self.code = code
        super().__init__(code.value)


def select_offer(
    result: LookupResult,
    *,
    offer_id: str,
    at: datetime,
) -> OfferSnapshot:
    if type(result) is not LookupResult:
        raise TypeError("result must be LookupResult")
    if type(offer_id) is not str:
        raise TypeError("offer_id must be a string")
    if result.evidence.status is not LookupStatus.POSITIVE:
        raise SelectionRejected(SelectionErrorCode.LOOKUP_NOT_POSITIVE)
    if not result.evidence.is_fresh(at):
        raise SelectionRejected(SelectionErrorCode.LOOKUP_EXPIRED)
    if not _OFFER_ID_RE.fullmatch(offer_id):
        raise SelectionRejected(SelectionErrorCode.OFFER_ID_NOT_FOUND)
    matches = tuple(offer for offer in result.offers if offer.offer_id == offer_id)
    if not matches:
        raise SelectionRejected(SelectionErrorCode.OFFER_ID_NOT_FOUND)
    if len(matches) != 1:
        raise SelectionRejected(SelectionErrorCode.OFFER_ID_NOT_UNIQUE)
    return matches[0]


def revalidate_offer(
    previous: OfferSnapshot,
    fresh: LookupResult,
    *,
    at: datetime,
) -> OfferSnapshot:
    if type(previous) is not OfferSnapshot:
        raise TypeError("previous must be OfferSnapshot")
    try:
        return select_offer(fresh, offer_id=previous.offer_id, at=at)
    except SelectionRejected as exc:
        if exc.code in {
            SelectionErrorCode.OFFER_ID_NOT_FOUND,
            SelectionErrorCode.OFFER_ID_NOT_UNIQUE,
        }:
            raise SelectionRejected(SelectionErrorCode.OFFER_CHANGED) from exc
        raise
