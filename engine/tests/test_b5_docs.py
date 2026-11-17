"""B5: reproducible benchmarks and documentation/evidence checks.

Everything here needs the git checkout (README.md, docs/, benchmarks/ at the repository root),
not just the installed package, so the whole module is skipped for an installed-only copy.
"""

from __future__ import annotations

import re
import tomllib
from pathlib import Path

import pytest

from tradealerts import __version__
from tradealerts import benchmarks as bm

ROOT = Path(__file__).resolve().parents[2]
DOCS = ROOT / "docs"
pytestmark = pytest.mark.skipif(not (ROOT / "README.md").is_file(),
                                 reason="needs the git checkout, not an installed copy")

LINK_RE = re.compile(r"\]\(([^)\s]+)\)")
HEADING_RE = re.compile(r"^(#{1,6})\s+(.+?)\s*$", re.MULTILINE)
# Not real filesystem paths: GitHub's own web routes, relative to how a repo page renders them.
NOT_A_FILE = {"../../releases/latest"}


def _slug(heading: str) -> str:
    """Approximates GitHub's heading-to-anchor slug: lowercase, drop punctuation, each space to
    '-' (not collapsed, so "A & B" -> "a--b", matching GitHub's own behaviour)."""
    heading = re.sub(r"[`*_]", "", heading)  # markdown emphasis/code markers don't count
    heading = re.sub(r"[^\w\s-]", "", heading.lower())
    return heading.strip(" ").replace(" ", "-")


def _headings(path: Path) -> set[str]:
    return {_slug(h) for _, h in HEADING_RE.findall(path.read_text(encoding="utf-8"))}


def _md_files():
    candidates = [ROOT / "README.md", ROOT / "CHANGES.md", ROOT / "benchmarks" / "README.md",
                  ROOT / "engine" / "README.md", *sorted(DOCS.glob("*.md"))]
    return [p for p in candidates if p.is_file()]


# --------------------------------------------------------------------- benchmarks (scope item 1)


def test_benchmarks_still_match_their_committed_metrics():
    """Rerunning the example benchmarks on the deterministic Sample data market gives the same
    metrics as the ones committed in benchmarks/expected/*.json. A mismatch means the simulator
    or its statistics changed, not that the (fixed, generated) market data did."""
    problems = bm.compare(ROOT / "benchmarks")
    assert problems == []


def test_every_benchmark_strategy_has_a_period_and_an_expected_file():
    root = ROOT / "benchmarks"
    strategies = sorted(p.stem for p in (root / "strategies").glob("*.yaml"))
    assert strategies, "no benchmark strategies found"
    for name in strategies:
        assert (root / "expected" / f"{name}.json").is_file(), name


def test_benchmarks_folder_is_small():
    """Keeps the committed fixture data small, per the plan (<300 KB total)."""
    total = sum(p.stat().st_size for p in (ROOT / "benchmarks").rglob("*") if p.is_file())
    assert total < 300_000, f"benchmarks/ is {total} bytes"


# --------------------------------------------------------------------- doc consistency (item 3)


def _pyproject():
    return tomllib.loads((ROOT / "engine" / "pyproject.toml").read_text(encoding="utf-8"))["project"]


def test_package_version_matches_pyproject():
    assert __version__ == _pyproject()["version"]


def test_python_version_floor_matches_pyproject_everywhere_it_is_stated():
    floor = re.search(r"(\d+\.\d+)", _pyproject()["requires-python"]).group(1)
    for path in (ROOT / "README.md", DOCS / "install.md"):
        assert floor in path.read_text(encoding="utf-8"), path


def test_product_name_altius_in_both_readmes():
    for path in (ROOT / "README.md", ROOT / "engine" / "README.md"):
        assert "Altius" in path.read_text(encoding="utf-8"), path


def test_licence_name_consistent():
    licence = _pyproject()["license"]
    assert licence.startswith("AGPL-3.0")
    for path in (ROOT / "README.md", ROOT / "engine" / "README.md"):
        assert "AGPL" in path.read_text(encoding="utf-8"), path
    assert "AFFERO GENERAL PUBLIC LICENSE" in (ROOT / "LICENSE").read_text(encoding="utf-8").upper()


def test_dev_install_extras_consistent():
    """README.md, engine/README.md and docs/install.md all give the same extras for a
    developer's ``pip install -e``, even though they're run from different directories."""
    extras = []
    for path in (ROOT / "README.md", ROOT / "engine" / "README.md", DOCS / "install.md"):
        text = path.read_text(encoding="utf-8")
        m = re.search(r"pip install -e [\"']?[.\w]*\[([\w,]+)\]", text)
        assert m, f"no `pip install -e ...[extras]` line in {path}"
        extras.append(m.group(1))
    assert len(set(extras)) == 1, extras


# --------------------------------------------------------------------- link checking (item 3)


def test_docs_readme_links_every_doc_page():
    text = (DOCS / "README.md").read_text(encoding="utf-8")
    pages = {p.name for p in DOCS.glob("*.md") if p.name != "README.md"}
    for page in pages:
        assert page in text, f"docs/README.md doesn't link {page}"


def test_every_relative_markdown_link_resolves():
    problems = []
    for path in _md_files():
        text = path.read_text(encoding="utf-8")
        for href in LINK_RE.findall(text):
            if href in NOT_A_FILE or href.startswith(("http://", "https://", "mailto:")):
                continue
            target, _, fragment = href.partition("#")
            dest = path if not target else (path.parent / target).resolve()
            if not dest.is_file():
                problems.append(f"{path.relative_to(ROOT)}: {href} -> no such file ({dest})")
                continue
            if fragment and dest.suffix == ".md" and _slug(fragment) not in _headings(dest):
                problems.append(f"{path.relative_to(ROOT)}: {href} -> no '{fragment}' heading in {dest.name}")
    assert problems == []
