"""Packaging tests for the absorbed contributor tools (PR #3, feat/memory-tools).

backend/memory_health.py and backend/ingest_md_memories.py were merged from an
external contributor's fork (Paul Taysom, PR #3, filed 2026-06-12). Two gaps
had to be closed before they could ship:

  1. Neither module was listed in pyproject.toml's `py-modules` — the same
     silent-unshipping gap `backend/beat_worker.py` had before commit 8be59b4
     (see test_worker_modes.py::test_pyproject_packages_rabbit_bridge, which
     this file's py-modules test mirrors).
  2. memory_health.py imports numpy at module level. It was declared nowhere
     in pyproject.toml and is not pulled in transitively, so a clean install
     would fail on import.

These tests guard both gaps directly and, where possible, independent of
whatever happens to be installed in the ambient test environment — see the
sys.modules trick in test_memory_health_gives_install_hint_without_numpy,
which forces the "numpy absent" case deterministically rather than relying on
numpy actually being missing from this interpreter.
"""
import importlib
import importlib.util
import re
import sys
from pathlib import Path

import pytest

BACKEND_ROOT = Path(__file__).resolve().parents[1]
PYPROJECT = BACKEND_ROOT / "pyproject.toml"

CONTRIBUTOR_MODULES = ["memory_health", "ingest_md_memories"]


def _py_modules_block(text: str) -> str:
    m = re.search(r"py-modules\s*=\s*\[(.*?)\]", text, re.S)
    assert m, "could not find the py-modules list in pyproject.toml"
    return m.group(1)


def _core_dependencies_block(text: str):
    return re.search(r"(?m)^dependencies\s*=\s*\[(.*?)\]", text, re.S)


def _optional_dependencies_block(text: str):
    return re.search(r"\[project\.optional-dependencies\](.*?)(?=\n\[|\Z)", text, re.S)


# --- Gap 1: both modules must be packaged -----------------------------------

@pytest.mark.parametrize("module_name", CONTRIBUTOR_MODULES)
def test_pyproject_packages_contributor_module(module_name):
    """memory_health and ingest_md_memories must be declared in py-modules, or
    a pipx/Docker/pip install silently omits them — the same gap beat_worker
    hit before it (commit 8be59b4: importable from a checkout, but missing
    from py-modules, so a real editable install never shipped it)."""
    block = _py_modules_block(PYPROJECT.read_text())
    assert f'"{module_name}"' in block, (
        f"{module_name} is importable from a checkout but not packaged — "
        "a clean `pip install` would not ship it"
    )


# --- Gap 2: numpy must be declared, and consistently with the chosen design -

def test_numpy_declared_somewhere_in_pyproject():
    """memory_health.py imports numpy at module level, so numpy must be
    declared as a dependency *somewhere* in pyproject.toml (core deps or an
    extra) or a clean install's import of memory_health fails outright.

    Checks the two real dependency-declaration blocks ([project] dependencies
    and [project.optional-dependencies]) rather than grepping the whole file,
    so a stray mention of "numpy" in a comment or docstring can't pass this.
    """
    text = PYPROJECT.read_text()
    dep_block = _core_dependencies_block(text)
    optional_block = _optional_dependencies_block(text)
    hard_dep = bool(dep_block and "numpy" in dep_block.group(1))
    optional_dep = bool(optional_block and "numpy" in optional_block.group(1))
    assert hard_dep or optional_dep, (
        "numpy is not declared anywhere in pyproject.toml — memory_health.py "
        "would fail to import on a clean install"
    )


def test_numpy_is_an_optional_extra_not_a_hard_dependency():
    """Design choice (Task 1, Step 3): memory_health.py is a diagnostic tool,
    not on the core memory-store path, so a fresh `pip install sill-memory`
    should not pull in numpy. Encodes that decision so a future change to a
    hard dependency is deliberate, not a silent side effect."""
    text = PYPROJECT.read_text()
    dep_block = _core_dependencies_block(text)
    assert dep_block, "could not find the core [project] dependencies list"
    assert "numpy" not in dep_block.group(1), (
        "numpy has become a hard runtime dependency; if that's an intentional "
        "change, update this test (and see the task report for why it was "
        "originally made an optional extra instead)"
    )
    optional_block = _optional_dependencies_block(text)
    assert optional_block and "numpy" in optional_block.group(1), (
        "numpy should be declared under an optional extra (e.g. 'diagnostics')"
    )


# --- Behavior: both modules actually import ----------------------------------

def test_ingest_md_memories_imports_cleanly():
    """No numpy dependency here — stdlib plus the already-packaged `sill`
    module (imported lazily, inside main(), not at module level) — so this
    must import cleanly regardless of whether the diagnostics extra is
    installed."""
    sys.modules.pop("ingest_md_memories", None)
    module = importlib.import_module("ingest_md_memories")
    assert module is not None
    assert hasattr(module, "main") and callable(module.main)


@pytest.mark.skipif(
    importlib.util.find_spec("numpy") is None,
    reason="numpy not installed in this environment (it's an optional extra); "
           "install with `pip install -e '.[diagnostics]'` to exercise this path",
)
def test_memory_health_imports_cleanly_when_numpy_present():
    sys.modules.pop("memory_health", None)
    module = importlib.import_module("memory_health")
    assert module is not None
    assert hasattr(module, "main") and callable(module.main)


def test_memory_health_gives_install_hint_without_numpy(monkeypatch):
    """Force the numpy-absent case deterministically — independent of whether
    numpy actually happens to be installed in this environment — by seeding
    sys.modules with the standard "import halted" sentinel (None), then
    assert the resulting failure is a clear, actionable ImportError rather
    than numpy's own bare "No module named 'numpy'" / "import of numpy
    halted" text.
    """
    monkeypatch.setitem(sys.modules, "numpy", None)
    monkeypatch.delitem(sys.modules, "memory_health", raising=False)
    with pytest.raises(ImportError) as exc_info:
        importlib.import_module("memory_health")
    message = str(exc_info.value)
    assert "numpy" in message.lower()
    assert "pip install" in message.lower(), (
        f"expected an actionable install hint, got: {message!r}"
    )
    assert "halted" not in message.lower(), (
        "the raw/bare numpy ImportError text leaked through instead of the "
        f"custom hint: {message!r}"
    )
