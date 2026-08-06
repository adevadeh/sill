"""Version stamp: backend/pyproject.toml, plugin/plugin.json, and
`sill-mcp --version` must all agree, and the CHANGELOG's dev section must
have become a dated release with no `-dev` suffix.

v0.1.0's own CHANGELOG "Known issues" list named `sill-mcp` having no
`--version` flag as an open issue for the whole 0.1.0 line (see
CHANGELOG.md's "## v0.1.0" section) — this file is the regression pin for
closing it in 0.2.0.
"""
import json
import re
import subprocess
import sys
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
BACKEND_ROOT = REPO_ROOT / "backend"
PYPROJECT = BACKEND_ROOT / "pyproject.toml"
PLUGIN_JSON = REPO_ROOT / "plugin" / "plugin.json"
CHANGELOG = REPO_ROOT / "CHANGELOG.md"

EXPECTED_VERSION = "0.2.0"


def _pyproject_version() -> str:
    text = PYPROJECT.read_text()
    m = re.search(r'(?m)^version\s*=\s*"([^"]+)"', text)
    assert m, "could not find a top-level version = \"...\" in backend/pyproject.toml"
    return m.group(1)


def _plugin_json_version() -> str:
    data = json.loads(PLUGIN_JSON.read_text())
    assert "version" in data, "plugin/plugin.json has no 'version' key"
    return data["version"]


# --- Step 1: pyproject.toml and plugin.json both read 0.2.0 -----------------

def test_pyproject_version_is_0_2_0():
    assert _pyproject_version() == EXPECTED_VERSION


def test_plugin_json_version_is_0_2_0():
    assert _plugin_json_version() == EXPECTED_VERSION


def test_pyproject_and_plugin_json_versions_match():
    """One source of version truth, expressed twice — they must never drift
    independently of each other."""
    assert _pyproject_version() == _plugin_json_version()


# --- Step 2: sill-mcp --version -----------------------------------------

def test_version_flag_wired_into_build_parser():
    """Static check that --version is a real argparse action, not just a
    string this file happens to match — belt to the subprocess test's
    braces."""
    import sill_mcp_server as srv

    actions = {a.option_strings[0]: a for a in srv.build_parser()._actions if a.option_strings}
    assert "--version" in actions, "sill-mcp has no --version flag wired into build_parser()"
    assert actions["--version"].__class__.__name__ == "_VersionAction", (
        "--version should use argparse's built-in version action (exits 0 after "
        "printing, no DB/asyncio machinery involved)"
    )


def test_version_flag_exits_zero_and_prints_something_unconditionally():
    """Environment-independent half of the claim: regardless of whether this
    checkout is pip-installed anywhere importlib.metadata can find it, the
    flag itself must exit 0 and print a non-empty, prog-prefixed string —
    never a traceback, never a hang waiting on a DB connection."""
    r = subprocess.run(
        [sys.executable, "-m", "sill_mcp_server", "--version"],
        cwd=BACKEND_ROOT, capture_output=True, text=True, timeout=15,
    )
    assert r.returncode == 0, f"stdout={r.stdout!r} stderr={r.stderr!r}"
    assert "Traceback" not in r.stderr
    out = r.stdout.strip()
    assert out, "sill-mcp --version printed nothing"
    assert out.startswith("sill-mcp"), f"expected a 'sill-mcp <version>'-shaped line, got {out!r}"


def _sill_memory_dist_resolvable() -> bool:
    try:
        version("sill-memory")
        return True
    except PackageNotFoundError:
        return False


@pytest.mark.skipif(
    not _sill_memory_dist_resolvable(),
    reason="sill-memory isn't registered with importlib.metadata in this "
           "environment (run `pip install -e backend` to exercise this path); "
           "CI's 'pip install -e .[dev]' step always does, so this runs there.",
)
def test_version_flag_matches_pyproject_when_package_is_resolvable():
    """The real claim: when this checkout is installed (editable or not —
    CI always is, via 'pip install -e .[dev]'), --version's printed number
    must be the same number pyproject.toml declares, not a stale or
    hardcoded one."""
    r = subprocess.run(
        [sys.executable, "-m", "sill_mcp_server", "--version"],
        cwd=BACKEND_ROOT, capture_output=True, text=True, timeout=15,
    )
    assert r.returncode == 0, f"stdout={r.stdout!r} stderr={r.stderr!r}"
    assert _pyproject_version() in r.stdout, (
        f"expected pyproject version {_pyproject_version()!r} in --version "
        f"output, got: {r.stdout!r}"
    )


# --- CHANGELOG: dated release, not "-dev — unreleased" ----------------------

def test_changelog_has_no_dev_unreleased_marker():
    """The v0.2.0 section describes a dated release, not a pending one.

    Scoped to that section deliberately. The earlier form of this test read
    everything above `## v0.1.0`, which also condemned a `## Unreleased`
    heading sitting *above* v0.2.0 — the standard Keep a Changelog place for
    work that has landed but not yet shipped, and the format this file's own
    header says it follows. That made the next change to this repo, whatever
    it turned out to be, unable to record itself without failing CI: the
    author's choices were to leave the change undocumented or to edit a
    released section. The claim worth holding is about the release heading,
    so check the release heading.
    """
    text = CHANGELOG.read_text()
    assert "v0.2.0-dev" not in text, "CHANGELOG still carries the -dev marker"
    body = text.split("## v0.2.0", 1)[-1].split("## v0.1.0", 1)[0]
    assert "unreleased" not in body.lower(), (
        "CHANGELOG's 0.2.0 section still reads as unreleased"
    )


def test_changelog_has_dated_v0_2_0_heading():
    text = CHANGELOG.read_text()
    m = re.search(r"(?m)^## v0\.2\.0 — (\d{4}-\d{2}-\d{2})\s*$", text)
    assert m, "could not find a '## v0.2.0 — YYYY-MM-DD' release heading in CHANGELOG.md"


def test_changelog_v0_2_0_section_precedes_v0_1_0():
    text = CHANGELOG.read_text()
    i020 = text.index("## v0.2.0")
    i010 = text.index("## v0.1.0")
    assert i020 < i010, "the new release heading should sit above the v0.1.0 history"
