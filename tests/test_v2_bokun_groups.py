from __future__ import annotations

import dataclasses
from datetime import date
import importlib
import json
from pathlib import Path

import httpx
import pytest


ROOT = Path(__file__).resolve().parents[1]
POLICY_PATH = ROOT / "config" / "v2_activity_group_policy.json"
PRODUCT_MAP_PATH = ROOT / "config" / "v2_bokun_product_map.json"
EXPECTED_SOLO = {
    "product:aguas-claras": ("913781", "2375647", "1160099"),
    "product:buracao": ("913372", "2375672", "1160099"),
    "product:mixila-1d": ("913776", "2375659", "1160099"),
    "product:marimbus": ("913348", "2375663", "1160099"),
    "product:pati-4d": ("1001204", "2375428", "1160099"),
    "product:pati-5d": ("1001205", "2375667", "1160099"),
}


def _module():
    module_path = ROOT / "v2_adapters" / "bokun_groups.py"
    assert module_path.is_file(), "Task 1 group-source adapter is not implemented"
    return importlib.import_module("v2_adapters.bokun_groups")


def _policy():
    module = _module()
    return module.load_activity_group_policy(POLICY_PATH, PRODUCT_MAP_PATH)


def _policy_payload() -> dict[str, object]:
    assert POLICY_PATH.is_file(), "Task 1 versioned policy is not implemented"
    return json.loads(POLICY_PATH.read_text(encoding="utf-8"))


def _write_json(path: Path, payload: object) -> Path:
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def _product_index(policy) -> dict[str, object]:
    return {item.canonical_product_id: item for item in policy.products}


def _solo_index(policy) -> dict[str, object]:
    return {item.canonical_product_id: item for item in policy.solo_minimum_two}


def _result(csv_text: str, *, product: str = "product:aguas-claras", target: date = date(2026, 8, 14)):
    module = _module()
    return module.parse_groups_csv(
        csv_text.encode("utf-8"),
        policy=_policy(),
        canonical_product_id=product,
        activity_date=target,
    )


def test_versioned_policy_covers_exact_active_product_map_and_closed_solo_subset() -> None:
    policy = _policy()
    product_map = json.loads(PRODUCT_MAP_PATH.read_text(encoding="utf-8"))
    products = _product_index(policy)
    solo = _solo_index(policy)

    assert policy.version == 1
    assert len(products) == 16
    assert set(products) == set(product_map)
    assert {
        canonical_id: item.bokun_product_id for canonical_id, item in products.items()
    } == product_map
    assert all(item.aliases for item in products.values())
    assert {
        canonical_id: (
            item.bokun_product_id,
            item.rate_id,
            item.adult_category_id,
        )
        for canonical_id, item in solo.items()
    } == EXPECTED_SOLO


def test_policy_and_result_contracts_are_recursively_immutable_and_closed() -> None:
    module = _module()
    policy = _policy()
    product = policy.products[0]
    result = module.GroupLookupResult(
        status="matched",
        canonical_product_id="product:aguas-claras",
        activity_date=date(2026, 8, 14),
        participant_count=2,
    )

    with pytest.raises(dataclasses.FrozenInstanceError):
        policy.version = 2
    with pytest.raises(dataclasses.FrozenInstanceError):
        product.aliases = ("other",)
    assert isinstance(policy.products, tuple)
    assert isinstance(product.aliases, tuple)
    assert [field.name for field in dataclasses.fields(result)] == [
        "status",
        "canonical_product_id",
        "activity_date",
        "participant_count",
    ]
    with pytest.raises(dataclasses.FrozenInstanceError):
        result.participant_count = 99


@pytest.mark.parametrize("mutation", ["missing", "extra", "map_mismatch"])
def test_policy_rejects_product_catalog_drift(tmp_path: Path, mutation: str) -> None:
    module = _module()
    payload = _policy_payload()
    products = payload["products"]
    assert isinstance(products, dict)
    if mutation == "missing":
        products.pop("product:sossego")
    elif mutation == "extra":
        products["product:not-active"] = {
            "bokun_product_id": "999999",
            "aliases": ["not active"],
        }
    else:
        products["product:sossego"]["bokun_product_id"] = "999999"

    with pytest.raises(ValueError):
        module.load_activity_group_policy(
            _write_json(tmp_path / "policy.json", payload), PRODUCT_MAP_PATH
        )


def test_policy_rejects_duplicate_normalized_aliases(tmp_path: Path) -> None:
    module = _module()
    payload = _policy_payload()
    products = payload["products"]
    products["product:sossego"]["aliases"] = ["  ÁGUAS   CLARAS "]

    with pytest.raises(ValueError, match="alias"):
        module.load_activity_group_policy(
            _write_json(tmp_path / "policy.json", payload), PRODUCT_MAP_PATH
        )


@pytest.mark.parametrize(
    ("section", "product", "field", "value"),
    [
        ("products", "product:sossego", "bokun_product_id", "9x"),
        ("solo_minimum_two", "product:buracao", "rate_id", "0"),
        ("solo_minimum_two", "product:marimbus", "adult_category_id", 1160099),
    ],
)
def test_policy_rejects_malformed_provider_ids(
    tmp_path: Path, section: str, product: str, field: str, value: object
) -> None:
    module = _module()
    payload = _policy_payload()
    payload[section][product][field] = value

    with pytest.raises(ValueError):
        module.load_activity_group_policy(
            _write_json(tmp_path / "policy.json", payload), PRODUCT_MAP_PATH
        )


def test_policy_rejects_solo_product_outside_exact_closed_six(tmp_path: Path) -> None:
    module = _module()
    payload = _policy_payload()
    payload["solo_minimum_two"]["product:sossego"] = {
        "bokun_product_id": "913775",
        "rate_id": "2375000",
        "adult_category_id": "1160099",
    }

    with pytest.raises(ValueError, match="solo"):
        module.load_activity_group_policy(
            _write_json(tmp_path / "policy.json", payload), PRODUCT_MAP_PATH
        )


@pytest.mark.parametrize(
    "raw_date",
    [
        "2026-08-14",
        "14-08-2026",
        "14/08/2026",
        "14/agosto/2026",
        "14/August/2026",
        "14 August 2026",
    ],
)
def test_csv_accepts_supported_date_formats_and_header_aliases(raw_date: str) -> None:
    result = _result(f"date,tour,pax\n{raw_date},ÁGUAS CLARAS,2\n")

    assert result.status == "matched"
    assert result.canonical_product_id == "product:aguas-claras"
    assert result.activity_date == date(2026, 8, 14)
    assert result.participant_count == 2


def test_csv_inherits_dates_and_sums_only_positive_exact_matches() -> None:
    result = _result(
        "Data,Passeio,Qtd Clts\n"
        "14/08/2026,Águas Claras,2\n"
        ",  aguas   claras  ,3\n"
        ",Águas Claras,0\n"
        ",Águas Claras,-4\n"
        ",Marimbus,8\n"
        "15/08/2026,Águas Claras,11\n"
    )

    assert result.status == "matched"
    assert result.participant_count == 5


def test_csv_accepts_current_operational_qtd_clt_header_and_ignores_private_columns() -> None:
    result = _result(
        "Data,Passeio,Guia,Clientes,Qtd Clt,Comentários\n"
        "14/08/2026,Águas Claras,ignored,ignored,2,ignored\n"
    )

    assert result.status == "matched"
    assert result.participant_count == 2


@pytest.mark.parametrize("similar_name", ["Águas Claras - Grupo", "Águas", "Passeio Águas Claras"])
def test_similar_or_substring_product_names_do_not_match(similar_name: str) -> None:
    result = _result(f"data,passeio,qtd\n14/08/2026,{similar_name},4\n")

    assert result.status == "not_matched"
    assert result.participant_count is None


def test_empty_and_zero_participant_rows_do_not_establish_group() -> None:
    result = _result(
        "data,atividade,participantes\n"
        "14/08/2026,Águas Claras,\n"
        ",Águas Claras,0\n"
        ",,9\n"
    )

    assert result.status == "not_matched"
    assert result.participant_count is None


def test_valid_csv_with_zero_data_rows_is_not_matched() -> None:
    result = _result("data,passeio,qtd\n")

    assert result.status == "not_matched"
    assert result.participant_count is None


def test_rows_for_another_exact_date_do_not_match() -> None:
    result = _result("data,passeio,qtd\n13/08/2026,Águas Claras,7\n")

    assert result.status == "not_matched"
    assert result.participant_count is None


def test_fetcher_is_https_bounded_and_receives_timeout() -> None:
    module = _module()
    calls: list[tuple[str, float, int]] = []

    def fetcher(url: str, timeout_seconds: float, max_bytes: int) -> bytes:
        calls.append((url, timeout_seconds, max_bytes))
        return b"data,passeio,qtd\n14/08/2026,Aguas Claras,2\n"

    source = module.BokunGroupsSource(
        sheet_csv_url="https://example.test/groups.csv",
        policy=_policy(),
        fetcher=fetcher,
        timeout_seconds=4.5,
    )
    result = source.lookup(
        canonical_product_id="product:aguas-claras",
        activity_date=date(2026, 8, 14),
    )

    assert result.status == "matched"
    assert calls == [
        ("https://example.test/groups.csv", 4.5, 2 * 1024 * 1024)
    ]


@pytest.mark.parametrize(
    "url",
    [
        "http://example.test/groups.csv",
        "file:///tmp/groups.csv",
        "https://user:secret@example.test/groups.csv",
        "https://example.test/groups.csv#fragment",
        "not a url",
    ],
)
def test_invalid_source_is_typed_unavailable_without_fetch(url: str) -> None:
    module = _module()
    called = False

    def fetcher(_url: str, _timeout: float, _limit: int) -> bytes:
        nonlocal called
        called = True
        return b""

    source = module.BokunGroupsSource(
        sheet_csv_url=url,
        policy=_policy(),
        fetcher=fetcher,
    )
    result = source.lookup(
        canonical_product_id="product:aguas-claras",
        activity_date=date(2026, 8, 14),
    )

    assert result.status == "unavailable"
    assert result.participant_count is None
    assert called is False


@pytest.mark.parametrize(
    "payload",
    [
        b"wrong,headers\nsecret-name,secret-comment\n",
        b"data,passeio,qtd\n14/08/2026,Aguas Claras,not-a-count\n",
        b"data,passeio,qtd\n14/08/2026,Aguas Claras,\"unterminated\n",
        b"\xff\xfe\xfd",
    ],
)
def test_invalid_headers_or_malformed_csv_are_typed_unavailable_without_raw_leakage(
    payload: bytes,
) -> None:
    module = _module()
    source = module.BokunGroupsSource(
        sheet_csv_url="https://example.test/groups.csv",
        policy=_policy(),
        fetcher=lambda _url, _timeout, _limit: payload,
    )

    result = source.lookup(
        canonical_product_id="product:aguas-claras",
        activity_date=date(2026, 8, 14),
    )

    assert result.status == "unavailable"
    assert "secret" not in repr(result)
    assert [field.name for field in dataclasses.fields(result)] == [
        "status",
        "canonical_product_id",
        "activity_date",
        "participant_count",
    ]


def test_oversized_fetcher_response_is_typed_unavailable() -> None:
    module = _module()
    source = module.BokunGroupsSource(
        sheet_csv_url="https://example.test/groups.csv",
        policy=_policy(),
        fetcher=lambda _url, _timeout, limit: b"x" * (limit + 1),
    )

    result = source.lookup(
        canonical_product_id="product:aguas-claras",
        activity_date=date(2026, 8, 14),
    )

    assert result.status == "unavailable"
    assert result.participant_count is None


def test_transport_exception_is_typed_unavailable_without_exception_text() -> None:
    module = _module()

    def broken(_url: str, _timeout: float, _limit: int) -> bytes:
        raise RuntimeError("raw customer secret")

    source = module.BokunGroupsSource(
        sheet_csv_url="https://example.test/groups.csv",
        policy=_policy(),
        fetcher=broken,
    )
    result = source.lookup(
        canonical_product_id="product:aguas-claras",
        activity_date=date(2026, 8, 14),
    )

    assert result.status == "unavailable"
    assert "secret" not in repr(result)


@pytest.mark.parametrize(
    ("response", "expected_calls"),
    [
        (httpx.Response(302, headers={"location": "https://other.test/raw.csv"}), 1),
        (httpx.Response(307, headers={"location": "https://other.test/raw.csv"}), 1),
        (httpx.Response(200, headers={"content-length": str(2 * 1024 * 1024 + 1)}), 1),
        (httpx.Response(200, headers={"content-length": "not-an-int"}), 1),
    ],
)
def test_http_redirect_or_invalid_content_length_is_unavailable(
    response: httpx.Response, expected_calls: int
) -> None:
    module = _module()
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return response

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        source = module.BokunGroupsSource(
            sheet_csv_url="https://example.test/groups.csv",
            policy=_policy(),
            client=client,
        )
        result = source.lookup(
            canonical_product_id="product:aguas-claras",
            activity_date=date(2026, 8, 14),
        )

    assert result.status == "unavailable"
    assert calls == expected_calls


def test_google_sheet_single_https_content_redirect_is_followed() -> None:
    module = _module()
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(str(request.url.host))
        if request.url.host == "docs.google.com":
            return httpx.Response(
                307,
                headers={
                    "location": (
                        "https://doc-14-98-sheets.googleusercontent.com/"
                        "export/groups.csv?token=opaque"
                    )
                },
            )
        if request.url.host == "doc-14-98-sheets.googleusercontent.com":
            return httpx.Response(
                200,
                content=b"data,passeio,qtd\n14/08/2026,Aguas Claras,2\n",
                headers={"content-type": "text/csv"},
            )
        raise AssertionError("unexpected redirect target")

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        source = module.BokunGroupsSource(
            sheet_csv_url=(
                "https://docs.google.com/spreadsheets/d/public/export?format=csv&gid=0"
            ),
            policy=_policy(),
            client=client,
        )
        result = source.lookup(
            canonical_product_id="product:aguas-claras",
            activity_date=date(2026, 8, 14),
        )

    assert result.status == "matched"
    assert result.participant_count == 2
    assert calls == [
        "docs.google.com",
        "doc-14-98-sheets.googleusercontent.com",
    ]


def test_upcoming_groups_fetches_sheet_once_and_returns_only_sanitized_matches() -> None:
    module = _module()
    calls = 0

    def fetcher(url: str, timeout: float, limit: int) -> bytes:
        nonlocal calls
        calls += 1
        return (
            "data,passeio,qtd\n"
            "14/08/2026,Aguas Claras,2\n"
            "14/08/2026,Aguas Claras,1\n"
            "15/08/2026,Roteiro 4Ps,4\n"
            "20/08/2026,Passeio desconhecido,9\n"
        ).encode()

    source = module.BokunGroupsSource(
        sheet_csv_url="https://example.test/groups.csv",
        policy=_policy(),
        fetcher=fetcher,
    )

    groups = source.upcoming_groups(
        start_date=date(2026, 8, 14),
        days=7,
        max_groups=24,
    )

    assert calls == 1
    assert groups == (
        module.GroupLookupResult(
            status="matched",
            canonical_product_id="product:aguas-claras",
            activity_date=date(2026, 8, 14),
            participant_count=3,
        ),
        module.GroupLookupResult(
            status="matched",
            canonical_product_id="product:tour-4ps",
            activity_date=date(2026, 8, 15),
            participant_count=4,
        ),
    )


def test_google_sheet_second_redirect_is_unavailable() -> None:
    module = _module()
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(
            307,
            headers={
                "location": (
                    "https://doc-14-98-sheets.googleusercontent.com/"
                    "export/groups.csv?token=opaque"
                )
            },
        )

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        source = module.BokunGroupsSource(
            sheet_csv_url=(
                "https://docs.google.com/spreadsheets/d/public/export?format=csv&gid=0"
            ),
            policy=_policy(),
            client=client,
        )
        result = source.lookup(
            canonical_product_id="product:aguas-claras",
            activity_date=date(2026, 8, 14),
        )

    assert result.status == "unavailable"
    assert calls == 2


def test_http_timeout_and_streamed_oversize_are_unavailable() -> None:
    module = _module()

    def timeout_handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("raw timeout detail", request=request)

    with httpx.Client(transport=httpx.MockTransport(timeout_handler)) as client:
        timeout_source = module.BokunGroupsSource(
            sheet_csv_url="https://example.test/groups.csv",
            policy=_policy(),
            client=client,
        )
        timeout_result = timeout_source.lookup(
            canonical_product_id="product:aguas-claras",
            activity_date=date(2026, 8, 14),
        )

    huge = b"x" * (2 * 1024 * 1024 + 1)
    with httpx.Client(
        transport=httpx.MockTransport(lambda request: httpx.Response(200, content=huge))
    ) as client:
        huge_source = module.BokunGroupsSource(
            sheet_csv_url="https://example.test/groups.csv",
            policy=_policy(),
            client=client,
        )
        huge_result = huge_source.lookup(
            canonical_product_id="product:aguas-claras",
            activity_date=date(2026, 8, 14),
        )

    assert timeout_result.status == "unavailable"
    assert huge_result.status == "unavailable"
