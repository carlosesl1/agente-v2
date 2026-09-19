"""Current Docker packaging is independent of historical Phase 7 wheel evidence."""
from pathlib import Path
import tomllib


ROOT = Path(__file__).resolve().parents[1]


def test_full_ci_executes_historical_contracts_without_exclusions():
    workflow = (ROOT / ".github/workflows/phase8.yml").read_text()
    full_gate = workflow.split("- name: Full regression", 1)[1].split("- name:", 1)[0]
    assert "python -m pytest -q" in full_gate
    assert "--deselect" not in full_gate
    assert "--ignore" not in full_gate


def test_current_runtime_is_packaged_by_docker_not_historical_wheel():
    project = tomllib.loads((ROOT / "pyproject.toml").read_text())
    assert project["project"]["version"] == "0.8.0"
    source = (ROOT / "Dockerfile.v2").read_text()
    for package in (*project["tool"]["phase7-wheel"]["packages"],
                    *project["tool"]["v2-fasttrack"]["packages"], "deploy"):
        assert f"COPY {package} /app/{package}" in source
        assert (ROOT / package).is_dir()
    for resource in (
        "cerebro_faq.yaml", "v2_activity_group_policy.json", "v2_bokun_product_map.json",
        "v2_terra_system_prompt.txt", "v2_payment_instructions.json",
        "v2_public_commercial_catalog.json",
    ):
        assert f"COPY config/{resource} /app/config/{resource}" in source
        assert (ROOT / "config" / resource).is_file()
    assert 'ENTRYPOINT ["python", "-m", "v2_host.api_main"]' in source
    assert "build_phase7_wheel" not in source


def test_historical_snapshot_failure_propagates():
    from tests.phase7_snapshot import assert_historical_phase7
    import pytest

    # Never turn missing history/selectors or a failed historical run into a skip.
    with pytest.raises(AssertionError, match="historical Phase 7 contract failed"):
        assert_historical_phase7("tests.test_phase7_package.Phase7PackageTests.test_absent_contract")
