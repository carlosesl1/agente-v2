"""Closed policy and bounded read-only CSV source for Bókun activity groups."""

from __future__ import annotations

import csv
from collections.abc import Callable, Iterator, Mapping
from dataclasses import dataclass
from datetime import date
from io import StringIO
import json
import math
from pathlib import Path
import re
from typing import Final, Literal
import unicodedata
from urllib.parse import urlsplit

import httpx


MAX_CSV_BYTES: Final = 2 * 1024 * 1024
DEFAULT_TIMEOUT_SECONDS: Final = 10.0
_ID_RE: Final = re.compile(r"^[1-9][0-9]*$")
_PRODUCT_RE: Final = re.compile(r"^product:[a-z0-9]+(?:-[a-z0-9]+)*$")
_GOOGLE_SHEETS_CONTENT_HOST_RE: Final = re.compile(
    r"^doc-[a-z0-9-]+-sheets\.googleusercontent\.com$"
)
_SOLO_EXPECTED: Final = {
    "product:aguas-claras": ("913781", "2375647", "1160099"),
    "product:buracao": ("913372", "2375672", "1160099"),
    "product:mixila-1d": ("913776", "2375659", "1160099"),
    "product:marimbus": ("913348", "2375663", "1160099"),
    "product:pati-4d": ("1001204", "2375428", "1160099"),
    "product:pati-5d": ("1001205", "2375667", "1160099"),
}
_HEADER_ALIASES: Final = {
    "date": frozenset({"data", "dia", "date"}),
    "tour": frozenset({"passeio", "tour", "roteiro", "atividade", "activity"}),
    "participants": frozenset(
        {
            "qtd clt",
            "qtd clts",
            "qtd clientes",
            "quantidade",
            "qtd",
            "pax",
            "qtde",
            "participantes",
            "participants",
            "participant count",
        }
    ),
}
_MONTHS: Final = {
    "jan": 1,
    "janeiro": 1,
    "january": 1,
    "fev": 2,
    "fevereiro": 2,
    "feb": 2,
    "february": 2,
    "mar": 3,
    "marco": 3,
    "march": 3,
    "abr": 4,
    "abril": 4,
    "apr": 4,
    "april": 4,
    "mai": 5,
    "maio": 5,
    "may": 5,
    "jun": 6,
    "junho": 6,
    "june": 6,
    "jul": 7,
    "julho": 7,
    "july": 7,
    "ago": 8,
    "agosto": 8,
    "aug": 8,
    "august": 8,
    "set": 9,
    "setembro": 9,
    "sep": 9,
    "sept": 9,
    "september": 9,
    "out": 10,
    "outubro": 10,
    "oct": 10,
    "october": 10,
    "nov": 11,
    "novembro": 11,
    "november": 11,
    "dez": 12,
    "dezembro": 12,
    "dec": 12,
    "december": 12,
}

FetchCsv = Callable[[str, float, int], bytes]
GroupStatus = Literal["matched", "not_matched", "unavailable"]


@dataclass(frozen=True, slots=True)
class ActivityGroupProductPolicy:
    canonical_product_id: str
    bokun_product_id: str
    aliases: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class SoloMinimumTwoPolicy:
    canonical_product_id: str
    bokun_product_id: str
    rate_id: str
    adult_category_id: str


@dataclass(frozen=True, slots=True)
class ActivityGroupPolicy:
    version: int
    products: tuple[ActivityGroupProductPolicy, ...]
    solo_minimum_two: tuple[SoloMinimumTwoPolicy, ...]

    def product(self, canonical_product_id: str) -> ActivityGroupProductPolicy | None:
        return next(
            (
                item
                for item in self.products
                if item.canonical_product_id == canonical_product_id
            ),
            None,
        )

    def solo_policy(
        self, canonical_product_id: str
    ) -> SoloMinimumTwoPolicy | None:
        return next(
            (
                item
                for item in self.solo_minimum_two
                if item.canonical_product_id == canonical_product_id
            ),
            None,
        )


@dataclass(frozen=True, slots=True)
class GroupLookupResult:
    status: GroupStatus
    canonical_product_id: str
    activity_date: date
    participant_count: int | None

    def __post_init__(self) -> None:
        if self.status not in ("matched", "not_matched", "unavailable"):
            raise ValueError("group status is invalid")
        if (
            type(self.canonical_product_id) is not str
            or _PRODUCT_RE.fullmatch(self.canonical_product_id) is None
        ):
            raise ValueError("canonical product ID is invalid")
        if type(self.activity_date) is not date:
            raise TypeError("activity_date must be an exact date")
        if self.status == "matched":
            if type(self.participant_count) is not int or self.participant_count < 1:
                raise ValueError("matched group requires a positive participant count")
        elif self.participant_count is not None:
            raise ValueError("unmatched group cannot contain a participant count")


@dataclass(frozen=True, slots=True)
class GroupDateCandidate:
    canonical_product_id: str
    activity_date: date
    group_participants: int


def _unique_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("JSON contains a duplicate key")
        result[key] = value
    return result


def _load_json_object(path: str | Path, label: str) -> dict[str, object]:
    try:
        raw = Path(path).read_text(encoding="utf-8")
        value = json.loads(raw, object_pairs_hook=_unique_object)
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"{label} is not valid JSON") from exc
    if type(value) is not dict:
        raise ValueError(f"{label} must be a JSON object")
    return value


def _exact_keys(value: Mapping[str, object], expected: set[str], label: str) -> None:
    if set(value) != expected:
        raise ValueError(f"{label} fields are invalid")


def _provider_id(value: object, label: str) -> str:
    if type(value) is not str or _ID_RE.fullmatch(value) is None:
        raise ValueError(f"{label} must be a positive decimal string")
    return value


def _canonical_product_id(value: object) -> str:
    if type(value) is not str or _PRODUCT_RE.fullmatch(value) is None:
        raise ValueError("canonical product ID is malformed")
    return value


def normalize_alias(value: object) -> str:
    if type(value) is not str:
        return ""
    decomposed = unicodedata.normalize("NFD", value.strip().casefold())
    without_accents = "".join(
        character for character in decomposed if not unicodedata.combining(character)
    )
    return " ".join(without_accents.split())


def load_activity_group_policy(
    policy_path: str | Path, product_map_path: str | Path
) -> ActivityGroupPolicy:
    """Load the closed policy and prove exact agreement with the active map."""

    payload = _load_json_object(policy_path, "activity group policy")
    _exact_keys(payload, {"version", "products", "solo_minimum_two"}, "policy")
    if type(payload["version"]) is not int or payload["version"] != 1:
        raise ValueError("activity group policy version is unsupported")

    raw_product_map = _load_json_object(product_map_path, "Bókun product map")
    product_map: dict[str, str] = {}
    for raw_canonical_id, raw_bokun_id in raw_product_map.items():
        canonical_id = _canonical_product_id(raw_canonical_id)
        product_map[canonical_id] = _provider_id(
            raw_bokun_id, "mapped Bókun product ID"
        )
    if len(product_map) != 16:
        raise ValueError("Bókun product map must contain exactly 16 products")

    raw_products = payload["products"]
    if type(raw_products) is not dict:
        raise ValueError("policy products must be an object")
    if set(raw_products) != set(product_map):
        raise ValueError("policy products disagree with the active product map")

    products: list[ActivityGroupProductPolicy] = []
    alias_owners: dict[str, str] = {}
    for raw_canonical_id, raw_entry in raw_products.items():
        canonical_id = _canonical_product_id(raw_canonical_id)
        if type(raw_entry) is not dict:
            raise ValueError("product policy must be an object")
        _exact_keys(raw_entry, {"bokun_product_id", "aliases"}, "product policy")
        bokun_id = _provider_id(raw_entry["bokun_product_id"], "Bókun product ID")
        if bokun_id != product_map[canonical_id]:
            raise ValueError("policy product ID disagrees with the active product map")
        raw_aliases = raw_entry["aliases"]
        if type(raw_aliases) is not list or not raw_aliases:
            raise ValueError("product aliases must be a non-empty list")
        aliases: list[str] = []
        for raw_alias in raw_aliases:
            if type(raw_alias) is not str:
                raise ValueError("product alias must be text")
            normalized = normalize_alias(raw_alias)
            if not normalized:
                raise ValueError("product alias cannot be empty")
            if normalized in alias_owners:
                raise ValueError("duplicate normalized product alias")
            alias_owners[normalized] = canonical_id
            aliases.append(normalized)
        products.append(
            ActivityGroupProductPolicy(canonical_id, bokun_id, tuple(aliases))
        )

    raw_solo = payload["solo_minimum_two"]
    if type(raw_solo) is not dict or set(raw_solo) != set(_SOLO_EXPECTED):
        raise ValueError("solo minimum-two policy must contain the exact closed six")
    solo: list[SoloMinimumTwoPolicy] = []
    for raw_canonical_id, raw_entry in raw_solo.items():
        canonical_id = _canonical_product_id(raw_canonical_id)
        if type(raw_entry) is not dict:
            raise ValueError("solo policy must be an object")
        _exact_keys(
            raw_entry,
            {"bokun_product_id", "rate_id", "adult_category_id"},
            "solo policy",
        )
        ids = (
            _provider_id(raw_entry["bokun_product_id"], "solo Bókun product ID"),
            _provider_id(raw_entry["rate_id"], "solo rate ID"),
            _provider_id(raw_entry["adult_category_id"], "adult category ID"),
        )
        if ids != _SOLO_EXPECTED[canonical_id]:
            raise ValueError("solo policy disagrees with the closed provider IDs")
        if ids[0] != product_map[canonical_id]:
            raise ValueError("solo policy disagrees with the active product map")
        solo.append(SoloMinimumTwoPolicy(canonical_id, *ids))

    return ActivityGroupPolicy(1, tuple(products), tuple(solo))


def _header_roles(fieldnames: list[str] | None) -> dict[str, str]:
    if not fieldnames or any(type(name) is not str for name in fieldnames):
        raise ValueError("CSV headers are missing")
    roles: dict[str, str] = {}
    for fieldname in fieldnames:
        normalized = normalize_alias(fieldname.lstrip("\ufeff"))
        for role, aliases in _HEADER_ALIASES.items():
            if normalized in aliases:
                if role in roles:
                    raise ValueError("CSV contains ambiguous headers")
                roles[role] = fieldname
    if set(roles) != set(_HEADER_ALIASES):
        raise ValueError("CSV headers are invalid")
    return roles


def _sheet_date(
    value: str,
    *,
    default_year: int,
    period_start: date | None = None,
    period_end: date | None = None,
) -> date:
    normalized = normalize_alias(value).replace(".", "")
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", normalized):
        try:
            return date.fromisoformat(normalized)
        except ValueError as exc:
            raise ValueError("sheet date is invalid") from exc

    numeric = re.fullmatch(r"(\d{1,2})[-/](\d{1,2})[-/](\d{4})", normalized)
    if numeric:
        day, month, year = (int(part) for part in numeric.groups())
        try:
            return date(year, month, day)
        except ValueError as exc:
            raise ValueError("sheet date is invalid") from exc

    month_name = re.fullmatch(
        r"(\d{1,2})(?:\s*[-/]\s*|\s+(?:de\s+)?)"
        r"([a-z]+)(?:(?:\s*[-/]\s*|\s+(?:de\s+)?)(\d{4}))?",
        normalized,
    )
    if month_name:
        day_text, name, year_text = month_name.groups()
        month = _MONTHS.get(name)
        if month is not None:
            try:
                parsed = date(
                    int(year_text) if year_text else default_year,
                    month,
                    int(day_text),
                )
                if (
                    year_text is None
                    and period_start is not None
                    and period_end is not None
                    and period_start.year != period_end.year
                    and not period_start <= parsed <= period_end
                ):
                    end_year_date = date(period_end.year, month, int(day_text))
                    if period_start <= end_year_date <= period_end:
                        return end_year_date
                return parsed
            except ValueError as exc:
                raise ValueError("sheet date is invalid") from exc
    raise ValueError("sheet date is invalid")


def _participant_count(value: object) -> int | None:
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    if re.fullmatch(r"[+-]?\d+", text) is None:
        raise ValueError("participant count is malformed")
    parsed = int(text)
    return parsed if parsed > 0 else None


def _iter_group_rows(
    csv_bytes: bytes,
    *,
    default_year: int,
    period_start: date | None = None,
    period_end: date | None = None,
) -> Iterator[tuple[date, str, int | None]]:
    if type(csv_bytes) is not bytes or len(csv_bytes) > MAX_CSV_BYTES:
        raise ValueError("CSV payload is invalid")
    try:
        text = csv_bytes.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise ValueError("CSV encoding is invalid") from exc
    if "\x00" in text:
        raise ValueError("CSV contains a NUL character")

    reader = csv.DictReader(StringIO(text, newline=""), strict=True)
    roles = _header_roles(reader.fieldnames)
    inherited_date: date | None = None
    try:
        for row in reader:
            if None in row:
                raise ValueError("CSV row has unexpected fields")
            raw_date = row.get(roles["date"])
            if raw_date is not None and raw_date.strip():
                inherited_date = _sheet_date(
                    raw_date,
                    default_year=default_year,
                    period_start=period_start,
                    period_end=period_end,
                )
            if inherited_date is None:
                continue
            yield (
                inherited_date,
                normalize_alias(row.get(roles["tour"])),
                _participant_count(row.get(roles["participants"])),
            )
    except csv.Error as exc:
        raise ValueError("CSV is malformed") from exc


def parse_groups_csv(
    csv_bytes: bytes,
    *,
    policy: ActivityGroupPolicy,
    canonical_product_id: str,
    activity_date: date,
) -> GroupLookupResult:
    """Parse bounded CSV bytes and aggregate one exact product/date match."""

    if type(policy) is not ActivityGroupPolicy:
        raise TypeError("policy must be an exact ActivityGroupPolicy")
    product = policy.product(canonical_product_id)
    if product is None or type(activity_date) is not date:
        raise ValueError("group query is invalid")
    total = 0
    for row_date, tour, participants in _iter_group_rows(
        csv_bytes, default_year=activity_date.year
    ):
        if (
            row_date == activity_date
            and tour in product.aliases
            and participants is not None
        ):
            total += participants

    return GroupLookupResult(
        status="matched" if total else "not_matched",
        canonical_product_id=canonical_product_id,
        activity_date=activity_date,
        participant_count=total or None,
    )


def discover_group_candidates(
    csv_bytes: bytes,
    *,
    policy: ActivityGroupPolicy,
    period_start: date,
    period_end: date,
    max_candidates: int = 24,
) -> tuple[GroupDateCandidate, ...]:
    """Discover exact formed groups across one inclusive bounded period."""

    if type(policy) is not ActivityGroupPolicy:
        raise TypeError("policy must be an exact ActivityGroupPolicy")
    if type(period_start) is not date or type(period_end) is not date:
        raise ValueError("group discovery period is invalid")
    if (
        type(max_candidates) is not int
        or max_candidates < 1
        or period_end < period_start
        or (period_end - period_start).days >= 14
    ):
        raise ValueError("group discovery bounds are invalid")

    alias_to_product = {
        alias: product.canonical_product_id
        for product in policy.products
        for alias in product.aliases
    }
    totals: dict[tuple[str, date], int] = {}
    for row_date, tour, participants in _iter_group_rows(
        csv_bytes,
        default_year=period_start.year,
        period_start=period_start,
        period_end=period_end,
    ):
        canonical_product_id = alias_to_product.get(tour)
        if (
            period_start <= row_date <= period_end
            and canonical_product_id is not None
            and participants is not None
        ):
            key = (canonical_product_id, row_date)
            totals[key] = totals.get(key, 0) + participants

    if len(totals) > max_candidates:
        raise ValueError("group candidate count exceeds limit")
    return tuple(
        GroupDateCandidate(product_id, activity_date, participants)
        for (product_id, activity_date), participants in sorted(
            totals.items(), key=lambda item: (item[0][1], item[0][0])
        )
    )


def _valid_https_url(url: object) -> bool:
    if type(url) is not str or not url or len(url) > 2048:
        return False
    try:
        parsed = urlsplit(url)
        port = parsed.port
    except (TypeError, ValueError):
        return False
    return (
        parsed.scheme == "https"
        and parsed.hostname is not None
        and parsed.username is None
        and parsed.password is None
        and parsed.fragment == ""
        and port in (None, 443)
        and not any(character.isspace() for character in url)
    )


def _content_length(response: httpx.Response) -> int | None:
    raw = response.headers.get("content-length")
    if raw is None:
        return None
    if re.fullmatch(r"\d+", raw) is None:
        raise ValueError("response content length is invalid")
    value = int(raw)
    if value > MAX_CSV_BYTES:
        raise ValueError("response is too large")
    return value


def _read_http_response(response: httpx.Response) -> bytes:
    if response.status_code != 200:
        raise ValueError("group source response status is unavailable")
    _content_length(response)
    chunks: list[bytes] = []
    size = 0
    for chunk in response.iter_bytes():
        size += len(chunk)
        if size > MAX_CSV_BYTES:
            raise ValueError("response is too large")
        chunks.append(chunk)
    return b"".join(chunks)


def _google_sheets_redirect(source_url: str, response: httpx.Response) -> str:
    if response.status_code != 307:
        raise ValueError("group source response status is unavailable")
    target = response.headers.get("location")
    if not _valid_https_url(target):
        raise ValueError("group source redirect is invalid")
    source = urlsplit(source_url)
    destination = urlsplit(target)
    if (
        source.hostname != "docs.google.com"
        or destination.hostname is None
        or _GOOGLE_SHEETS_CONTENT_HOST_RE.fullmatch(destination.hostname) is None
    ):
        raise ValueError("group source redirect is not allowed")
    return target


class BokunGroupsSource:
    """GET-only HTTPS group source that exposes only a closed typed result."""

    def __init__(
        self,
        *,
        sheet_csv_url: str,
        policy: ActivityGroupPolicy,
        client: httpx.Client | None = None,
        fetcher: FetchCsv | None = None,
        timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
    ) -> None:
        if type(policy) is not ActivityGroupPolicy:
            raise TypeError("policy must be an exact ActivityGroupPolicy")
        if client is not None and fetcher is not None:
            raise ValueError("provide either an HTTP client or a fetcher, not both")
        if (
            isinstance(timeout_seconds, bool)
            or not isinstance(timeout_seconds, (int, float))
            or not math.isfinite(timeout_seconds)
            or not 0 < timeout_seconds <= 30
        ):
            raise ValueError("timeout_seconds must be between zero and 30")
        if fetcher is not None and not callable(fetcher):
            raise TypeError("fetcher must be callable")
        self._url = sheet_csv_url
        self._policy = policy
        self._client = client
        self._fetcher = fetcher
        self._timeout = float(timeout_seconds)

    def _fetch_http(self, client: httpx.Client) -> bytes:
        with client.stream(
            "GET",
            self._url,
            timeout=self._timeout,
            follow_redirects=False,
        ) as response:
            if response.status_code == 200:
                return _read_http_response(response)
            redirect = _google_sheets_redirect(self._url, response)
        with client.stream(
            "GET",
            redirect,
            timeout=self._timeout,
            follow_redirects=False,
        ) as response:
            return _read_http_response(response)

    def _fetch(self) -> bytes:
        if self._fetcher is not None:
            payload = self._fetcher(self._url, self._timeout, MAX_CSV_BYTES)
            if type(payload) is not bytes or len(payload) > MAX_CSV_BYTES:
                raise ValueError("fetcher returned an invalid payload")
            return payload
        if self._client is not None:
            return self._fetch_http(self._client)
        with httpx.Client(
            follow_redirects=False,
            headers={"user-agent": "maya-v2-bokun-groups/1"},
        ) as client:
            return self._fetch_http(client)

    def discover(
        self,
        *,
        period_start: date,
        period_end: date,
        max_candidates: int = 24,
    ) -> tuple[GroupDateCandidate, ...] | None:
        """Fetch once and expose only sanitized group candidates."""

        if not _valid_https_url(self._url):
            return None
        try:
            return discover_group_candidates(
                self._fetch(),
                policy=self._policy,
                period_start=period_start,
                period_end=period_end,
                max_candidates=max_candidates,
            )
        except Exception:
            return None

    def lookup(
        self, *, canonical_product_id: str, activity_date: date
    ) -> GroupLookupResult:
        def unavailable() -> GroupLookupResult:
            return GroupLookupResult(
                status="unavailable",
                canonical_product_id=canonical_product_id,
                activity_date=activity_date,
                participant_count=None,
            )
        if (
            type(canonical_product_id) is not str
            or _PRODUCT_RE.fullmatch(canonical_product_id) is None
            or type(activity_date) is not date
        ):
            raise ValueError("group query is invalid")
        if not _valid_https_url(self._url) or self._policy.product(
            canonical_product_id
        ) is None:
            return unavailable()
        try:
            return parse_groups_csv(
                self._fetch(),
                policy=self._policy,
                canonical_product_id=canonical_product_id,
                activity_date=activity_date,
            )
        except Exception:
            return unavailable()


__all__ = [
    "ActivityGroupPolicy",
    "ActivityGroupProductPolicy",
    "BokunGroupsSource",
    "GroupDateCandidate",
    "GroupLookupResult",
    "MAX_CSV_BYTES",
    "SoloMinimumTwoPolicy",
    "discover_group_candidates",
    "load_activity_group_policy",
    "normalize_alias",
    "parse_groups_csv",
]
