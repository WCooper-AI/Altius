"""Command menu (Ctrl+K): cmdk.js is a plain-JS remake of cmdk; its ranking is checked in Node when Node is installed."""
import json
import shutil
import subprocess
from pathlib import Path

import pytest

STATIC = Path(__file__).resolve().parents[1] / "src" / "tradealerts" / "static"
NODE = shutil.which("node")


def score(tmp_path, pairs):
    """Run cmdk.js's commandScore on [value, search, keywords] triples."""
    mod = tmp_path / "cmdk.mjs"
    mod.write_text((STATIC / "cmdk.js").read_text(encoding="utf-8"), encoding="utf-8")
    script = f"import {{ commandScore }} from {json.dumps(mod.as_uri())};" \
             f"console.log(JSON.stringify({json.dumps(pairs)}.map(([v, s, k]) => commandScore(v, s, k))));"
    out = subprocess.run([NODE, "--input-type=module", "-e", script], capture_output=True, text=True, timeout=30, check=True)
    return json.loads(out.stdout)


needs_node = pytest.mark.skipif(not NODE, reason="Node isn't installed")


@needs_node
def test_exact_beats_prefix_beats_scattered_and_misses_score_zero(tmp_path):
    exact, prefix, scattered, miss = score(tmp_path, [
        ["Markets", "markets", []], ["Markets chart", "mark", []], ["Download a backup", "dab", []], ["Journal", "xyz", []]])
    assert exact > prefix > scattered > 0
    assert miss == 0


@needs_node
def test_word_starts_rank_above_mid_word_letters(tmp_path):
    starts, middle = score(tmp_path, [["Check now", "cn", []], ["Activity", "ti", []]])
    assert starts > middle


@needs_node
def test_keywords_are_searched_too(tmp_path):
    without, with_kw = score(tmp_path, [["Switch light / dark", "theme", []], ["Switch light / dark", "theme", ["theme"]]])
    assert without == 0 and with_kw > 0


@needs_node
def test_transposed_letters_still_match(tmp_path):
    (s,) = score(tmp_path, [["Journal", "joruanl", []]])
    assert s > 0


def test_palette_uses_the_cmdk_module_and_attributes():
    widgets = (STATIC / "widgets.js").read_text(encoding="utf-8")
    css = (STATIC / "app.css").read_text(encoding="utf-8")
    assert 'import { commandDialog } from "./cmdk.js";' in widgets
    for part in ("[cmdk-input]", "[cmdk-list]", "[cmdk-group-heading]", '[cmdk-item][data-selected="true"]', "[cmdk-empty]"):
        assert part in css


def test_every_searchable_setting_points_at_a_real_heading_and_label():
    import re
    src = (STATIC / "settings.js").read_text(encoding="utf-8")
    index = src[src.index("export const SETTINGS_INDEX"):src.index("];", src.index("export const SETTINGS_INDEX"))]
    rows = re.findall(r'\["([^"]+)", "([^"]+)", "([^"]*)", "[^"]*"\]', index)
    assert len(rows) >= 15
    for _title, section, find in rows:
        assert f'h("h2", {{}}, "{section}")' in src, section
        if find:
            assert f'"{find}' in src, find   # the label text starts with it


def test_palette_lists_settings():
    widgets = (STATIC / "widgets.js").read_text(encoding="utf-8")
    assert "SETTINGS_INDEX" in widgets and "revealSetting" in widgets
