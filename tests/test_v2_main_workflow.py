from pathlib import Path

import pytest
import yaml


ROOT = Path(__file__).resolve().parents[1]
WORKFLOWS = ROOT / ".github" / "workflows"


def _workflow(name: str) -> dict:
    return yaml.load((WORKFLOWS / name).read_text(), Loader=yaml.BaseLoader)


def test_main_has_an_integrated_full_suite_without_live_capability() -> None:
    workflow = _workflow("v2-main.yml")
    assert workflow["on"]["push"]["branches"] == ["main"]
    assert "pull_request" in workflow["on"]
    assert workflow["permissions"] == {"contents": "read"}
    job = workflow["jobs"]["integrated-suite"]
    assert job["env"]["HERMES_LEADS_AGENT_CONFIG_PATH"] == "/tmp/no-live-config"
    assert job["env"]["PYTEST_DISABLE_PLUGIN_AUTOLOAD"] == "1"
    steps = job["steps"]
    checkout = next(step for step in steps if step.get("uses", "").startswith("actions/checkout@"))
    assert checkout["with"]["fetch-depth"] == "0"
    run = "\n".join(step.get("run", "") for step in steps)
    assert "python -m pytest -q -p no:cacheprovider" in run
    assert "--deselect" not in run
    assert "--ignore" not in run
    assert " -k " not in run
    assert "scripts/check_fasttrack_boundaries.py" in run
    assert "mcr.microsoft.com/playwright:v1.55.0-noble" in run
    assert "@playwright/test@1.55.0" in run
    assert "generate_phase6_manifest.py --check" in run
    assert "generate_phase7_manifest.py --check" in run
    assert "git diff --exit-code" in run
    raw = (WORKFLOWS / "v2-main.yml").read_text()
    for forbidden in ("secrets.", "docker push", "compose up", "ssh ", "continue-on-error"):
        assert forbidden not in raw


@pytest.mark.parametrize("phase", range(7))
def test_historical_phase_workflows_are_explicit_manual_checks(phase: int) -> None:
    workflow = _workflow(f"phase{phase}.yml")
    assert set(workflow["on"]) == {"workflow_dispatch"}
    assert workflow["jobs"], "historical jobs must not be deleted"


def test_integrated_main_keeps_generated_financial_stress_evidence() -> None:
    workflow = _workflow("v2-main.yml")
    steps = workflow["jobs"]["financial-evidence"]["steps"]
    run = "\n".join(step.get("run", "") for step in steps)
    for runner in ("run_phase6_properties.py", "run_phase6_faults.py", "run_phase6_mutations.py"):
        assert runner in run
    assert "PYTHONHASHSEED=1" in run
    assert "PYTHONHASHSEED=777" in run
    assert "git diff --exit-code" in run
