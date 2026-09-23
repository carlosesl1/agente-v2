"""Closed channel routes for authenticated payment offers, never for authored prose."""

from dataclasses import dataclass

from v2_contracts.payments import BusinessUnit, CustomerLanguage


@dataclass(frozen=True, slots=True)
class ManyChatPaymentRoute:
    business_unit: BusinessUnit
    customer_language: CustomerLanguage
    link_field_id: int
    description_field_id: int
    flow_ns: str

    def __post_init__(self):
        if type(self.business_unit) is not BusinessUnit:
            raise TypeError("payment route requires exact business_unit")
        if type(self.customer_language) is not CustomerLanguage:
            raise TypeError("payment route requires exact customer_language")
        if any(
            type(v) is not int or v < 1
            for v in (self.link_field_id, self.description_field_id)
        ):
            raise ValueError("payment route fields must be positive integers")
        if self.link_field_id == self.description_field_id:
            raise ValueError("payment route fields must be distinct")
        if (
            type(self.flow_ns) is not str
            or not self.flow_ns
            or any(c.isspace() or ord(c) < 32 for c in self.flow_ns)
        ):
            raise ValueError("payment route flow must be canonical text")


def validate_payment_routes(routes: tuple[ManyChatPaymentRoute, ...]) -> None:
    if type(routes) is not tuple or any(
        type(r) is not ManyChatPaymentRoute for r in routes
    ):
        raise TypeError("payment routes require exact tuple of ManyChatPaymentRoute")
    expected = {(u, lang) for u in BusinessUnit for lang in CustomerLanguage}
    if (
        len(routes) != len(expected)
        or {(r.business_unit, r.customer_language) for r in routes} != expected
    ):
        raise ValueError(
            "payment routes must cover each business unit and language once"
        )
    if len({r.flow_ns for r in routes}) != len(routes):
        raise ValueError("payment routes require distinct flows")
    fields = {
        u: {
            f
            for r in routes
            if r.business_unit is u
            for f in (r.link_field_id, r.description_field_id)
        }
        for u in BusinessUnit
    }
    if fields[BusinessUnit.HOSTEL] & fields[BusinessUnit.AGENCY]:
        raise ValueError("payment fields cannot be shared between business units")
