"""Closed passenger update and non-PII progress contracts for Maya V2."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
import re
from typing import Final


PASSENGER_FIELD_ORDER: Final = (
    "full_name",
    "birth_date",
    "gender",
    "country_code",
)
_COUNTRY_RE: Final = re.compile(r"^[A-Z]{2}$")


@dataclass(frozen=True, slots=True)
class PassengerInput:
    """One partial passenger update emitted by the closed model protocol."""

    position: int
    participant_type: str
    full_name: str | None
    birth_date: date | None
    gender: str | None
    country_code: str | None

    def __post_init__(self) -> None:
        if type(self.position) is not int or self.position < 1:
            raise ValueError("passenger input position must be an integer >= 1")
        if self.participant_type not in ("adult", "child"):
            raise ValueError("passenger input type must be adult or child")
        if self.full_name is not None:
            if type(self.full_name) is not str:
                raise TypeError("passenger input full_name must be exact text or None")
            name = " ".join(self.full_name.split())
            if not name or len(name) > 200:
                raise ValueError("passenger input full_name is invalid")
            object.__setattr__(self, "full_name", name)
        if self.birth_date is not None and type(self.birth_date) is not date:
            raise TypeError("passenger input birth_date must be an exact date or None")
        if self.gender is not None and self.gender not in ("m", "f"):
            raise ValueError("passenger input gender must be m, f or None")
        if self.country_code is not None:
            if type(self.country_code) is not str:
                raise TypeError("passenger input country_code must be exact text or None")
            country = self.country_code.strip().upper()
            if _COUNTRY_RE.fullmatch(country) is None:
                raise ValueError("passenger input country_code must be ISO alpha-2")
            object.__setattr__(self, "country_code", country)
        if all(getattr(self, name) is None for name in PASSENGER_FIELD_ORDER):
            raise ValueError("passenger input must carry at least one material field")


@dataclass(frozen=True, slots=True)
class PassengerManifestStatus:
    """Non-PII progress marker that may be sent back to the model."""

    required_adults: int
    required_children: int
    complete_positions: tuple[int, ...]
    missing_by_position: tuple[tuple[int, tuple[str, ...]], ...]

    def __post_init__(self) -> None:
        if type(self.required_adults) is not int or self.required_adults < 1:
            raise ValueError("required_adults must be an integer >= 1")
        if type(self.required_children) is not int or self.required_children < 0:
            raise ValueError("required_children must be an integer >= 0")
        total = self.required_adults + self.required_children
        if type(self.complete_positions) is not tuple or any(
            type(position) is not int for position in self.complete_positions
        ):
            raise TypeError("complete_positions must be an exact integer tuple")
        if self.complete_positions != tuple(sorted(set(self.complete_positions))):
            raise ValueError("complete_positions must be sorted and unique")
        if type(self.missing_by_position) is not tuple:
            raise TypeError("missing_by_position must be an exact tuple")
        missing_positions: list[int] = []
        for item in self.missing_by_position:
            if type(item) is not tuple or len(item) != 2:
                raise TypeError("missing_by_position entries must be exact pairs")
            position, fields = item
            if type(position) is not int or type(fields) is not tuple or not fields:
                raise TypeError("missing_by_position entry is invalid")
            canonical = tuple(
                name for name in PASSENGER_FIELD_ORDER if name in set(fields)
            )
            if fields != canonical or len(fields) != len(set(fields)):
                raise ValueError("missing passenger fields must be canonical")
            missing_positions.append(position)
        if missing_positions != sorted(set(missing_positions)):
            raise ValueError("missing passenger positions must be sorted and unique")
        positions = (*self.complete_positions, *missing_positions)
        if set(positions) != set(range(1, total + 1)) or len(positions) != total:
            raise ValueError("manifest status must cover every required position exactly")

    def to_public_dict(self) -> dict[str, object]:
        return {
            "required_adults": self.required_adults,
            "required_children": self.required_children,
            "complete_positions": list(self.complete_positions),
            "missing_by_position": [
                {"position": position, "fields": list(fields)}
                for position, fields in self.missing_by_position
            ],
        }


__all__ = [
    "PASSENGER_FIELD_ORDER",
    "PassengerInput",
    "PassengerManifestStatus",
]
