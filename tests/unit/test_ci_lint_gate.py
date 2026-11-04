"""Regression coverage for CI's fail-closed changed-Python Ruff gate."""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest
import yaml

REPOSITORY_ROOT = Path(__file__).parents[2]
WORKFLOW_PATH = REPOSITORY_ROOT / ".github" / "workflows" / "ci.yml"


def _ruff_step() -> dict[str, object]:
    workflow = yaml.safe_load(WORKFLOW_PATH.read_text(encoding="utf-8"))
    steps = workflow["jobs"]["validate"]["steps"]
    return next(step for step in steps if step["name"] == "Ruff changed Python files")


def _unit_test_step() -> dict[str, object]:
    workflow = yaml.safe_load(WORKFLOW_PATH.read_text(encoding="utf-8"))
    steps = workflow["jobs"]["validate"]["steps"]
    return next(step for step in steps if step["name"] == "Run unit tests")


def test_unit_test_gate_uses_fail_closed_offline_harness():
    step = _unit_test_step()
    command = str(step["run"])

    assert "scripts/run_offline_tests.py" in command
    assert '-m "not slow"' in command
    assert "tests/unit/" in command


def test_changed_python_ruff_gate_is_fail_closed_for_an_invalid_temp_fixture(
    tmp_path: Path,
):
    step = _ruff_step()
    assert step.get("continue-on-error") is not True

    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    subprocess.run(
        ["git", "config", "user.email", "ci-test@example.invalid"],
        cwd=tmp_path,
        check=True,
    )
    subprocess.run(["git", "config", "user.name", "CI test"], cwd=tmp_path, check=True)
    fixture = tmp_path / "bad_fixture.py"
    fixture.write_text("import json\nimport os\n", encoding="utf-8")
    subprocess.run(["git", "add", "bad_fixture.py"], cwd=tmp_path, check=True)
    subprocess.run(["git", "commit", "-qm", "baseline"], cwd=tmp_path, check=True)
    base_sha = subprocess.check_output(
        ["git", "rev-parse", "HEAD"], cwd=tmp_path, text=True
    ).strip()
    fixture.write_text("import json\nimport os\n\n\nVALUE = 1\n", encoding="utf-8")
    subprocess.run(["git", "add", "bad_fixture.py"], cwd=tmp_path, check=True)
    subprocess.run(
        ["git", "commit", "-qm", "introduce lint violation"], cwd=tmp_path, check=True
    )
    head_sha = subprocess.check_output(
        ["git", "rev-parse", "HEAD"], cwd=tmp_path, text=True
    ).strip()

    ruff = shutil.which("ruff")
    if ruff is None:
        pytest.skip("ruff is not installed on PATH")
    result = subprocess.run(
        ["bash", "-o", "pipefail", "-c", step["run"]],
        cwd=tmp_path,
        env={**os.environ, "base_sha": base_sha, "GITHUB_SHA": head_sha},
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode != 0, result.stdout + result.stderr
