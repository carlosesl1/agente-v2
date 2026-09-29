"""Operator-owned liability forms keyed by canonical tour and language."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType
from urllib.parse import urlsplit

from v2_contracts.localization import CustomerLanguage


@dataclass(frozen=True, slots=True)
class BookingFormRoute:
    category: str
    language: CustomerLanguage
    url: str
    link_field_id: int
    flow_ns: str

    def __post_init__(self):
        if type(self.language) is not CustomerLanguage:
            raise TypeError("form route requires canonical language")
        if type(self.link_field_id) is not int or self.link_field_id < 1:
            raise ValueError("form field must be a positive integer")
        if not self.category or not self.flow_ns.startswith("content"):
            raise ValueError("form category/flow is invalid")
        url = urlsplit(self.url)
        if (
            url.scheme != "https"
            or url.netloc != "forms.gle"
            or not url.path.strip("/")
            or url.query
            or url.fragment
        ):
            raise ValueError("form requires the operator-supplied Google Forms URL")

    @property
    def text(self) -> str:
        # Factual internal outbox label, not customer prose or a raw-link message.
        label = (
            "Termo de responsabilidade"
            if self.language is CustomerLanguage.PT_BR
            else "Liability waiver"
        )
        return f"{label}: {self.url}"


class BookingFormCatalog:
    def __init__(self, config):
        self.products = MappingProxyType(dict(config["products"]))
        self._routes = {}
        for category, forms in config["forms"].items():
            for language in CustomerLanguage:
                self._routes[category, language] = BookingFormRoute(
                    category,
                    language,
                    forms[language.value],
                    config["link_field_id"],
                    config["flows"][language.value],
                )
        if any(
            not p.startswith("product:")
            or (c, CustomerLanguage.PT_BR) not in self._routes
            for p, c in self.products.items()
        ):
            raise ValueError("form product mapping is invalid")

    @classmethod
    def load(cls):
        path = Path(__file__).resolve().parents[1] / "config/v2_booking_forms.json"
        return cls(json.loads(path.read_text()))

    def route(
        self, product_id: str, language: CustomerLanguage
    ) -> BookingFormRoute | None:
        return self._routes.get((self.products.get(product_id), language))

    def product_for_lookup(self, lookup_id: str) -> str | None:
        # Lookup IDs are minted by the canonical activity read owner. This is
        # identity decoding, not matching the lead's text or public tour name.
        return next(
            (p for p in self.products if lookup_id.startswith(f"lookup:{p}:")), None
        )
