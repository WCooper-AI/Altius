"""A7: CI and supply-chain files stay complete as the repo grows."""
import os
import shutil
import subprocess
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[2]
GITHUB = ROOT / ".github"
pytestmark = pytest.mark.skipif(not GITHUB.is_dir(), reason="needs the git checkout, not an installed copy")


def _workflows():
    return {p.name: yaml.safe_load(p.read_text(encoding="utf-8")) for p in (GITHUB / "workflows").glob("*.yml")}


def test_every_workflow_sets_read_only_default_permissions():
    for name, wf in _workflows().items():
        assert wf.get("permissions", {}).get("contents") == "read", name


def test_tests_workflow_has_the_a7_jobs():
    jobs = _workflows()["tests.yml"]["jobs"]
    assert {"lint", "lint-js", "test", "test-os", "docker"} <= set(jobs)
    assert set(jobs["test-os"]["strategy"]["matrix"]["os"]) == {"windows-latest", "macos-latest"}


def test_dependabot_watches_every_manifest():
    watched = {(u["package-ecosystem"], u["directory"].strip("/"))
               for u in yaml.safe_load((GITHUB / "dependabot.yml").read_text(encoding="utf-8"))["updates"]}
    skip = {"node_modules", "target", "build", "dist", "data", "tests", "gen"}
    kinds = {"pyproject.toml": "pip", "package.json": "npm", "Cargo.toml": "cargo", "Dockerfile": "docker"}
    for here, dirs, files in os.walk(ROOT):
        dirs[:] = [d for d in dirs if d not in skip and not d.startswith(".")]
        rel = Path(here).relative_to(ROOT).as_posix().lstrip(".")
        for name in kinds.keys() & set(files):
            assert (kinds[name], rel) in watched, f"{kinds[name]} /{rel}"
    assert ("github-actions", "") in watched


def test_coverage_has_a_floor():
    text = (ROOT / "engine" / "pyproject.toml").read_text(encoding="utf-8")
    assert "fail_under" in text and "pytest-cov" in text


@pytest.mark.skipif(shutil.which("node") is None or not (ROOT / "node_modules" / "eslint").is_dir(),
                    reason="needs node and `npm ci` at the repo root")
def test_interface_javascript_lints_clean():
    out = subprocess.run([shutil.which("npm"), "run", "-s", "lint:js"], cwd=ROOT, capture_output=True, text=True, encoding="utf-8")
    assert out.returncode == 0, out.stdout + out.stderr
