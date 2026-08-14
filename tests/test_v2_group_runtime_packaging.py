from __future__ import annotations

from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[1]


def test_runtime_image_package_copy_includes_activity_recommendation_adapter() -> None:
    dockerfile = (ROOT / "Dockerfile.v2").read_text(encoding="utf-8")

    assert "COPY v2_adapters /app/v2_adapters" in dockerfile
    assert (ROOT / "v2_adapters" / "activity_recommendations.py").is_file()


def test_runtime_image_copies_activity_group_policy_to_production_path() -> None:
    dockerfile = (ROOT / "Dockerfile.v2").read_text(encoding="utf-8")

    assert (
        "COPY config/v2_activity_group_policy.json "
        "/app/config/v2_activity_group_policy.json"
    ) in dockerfile


def test_compose_requires_group_sheet_url_only_for_worker() -> None:
    compose = yaml.safe_load((ROOT / "compose.v2.yaml").read_text(encoding="utf-8"))
    worker_environment = compose["services"]["worker"]["environment"]
    api_environment = compose["services"]["api"]["environment"]
    router_environment = compose["services"]["router"]["environment"]

    assert worker_environment["V2_BOKUN_GROUPS_SHEET_CSV_URL"] == (
        "${V2_BOKUN_GROUPS_SHEET_CSV_URL:?set V2_BOKUN_GROUPS_SHEET_CSV_URL}"
    )
    assert "V2_BOKUN_GROUPS_SHEET_CSV_URL" not in api_environment
    assert "V2_BOKUN_GROUPS_SHEET_CSV_URL" not in router_environment
