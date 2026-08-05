"""Smoke tests for the sill-memory backend.

These deliberately avoid any database, network, or external-service access.
They only assert that the package's public modules import cleanly and that
the declared console-script entry points are present and callable. The intent
is a fast canary that catches syntax errors, broken imports, and accidental
import-time side effects before anything heavier runs.
"""

import importlib

import pytest

# Top-level modules declared in pyproject.toml's [tool.setuptools] py-modules.
# (beat_worker and rabbit_bridge added here to actually match that list —
# beat_worker was missing since its own addition; see commit 8be59b4, which
# fixed pyproject.toml/Dockerfile.worker for the same gap but not this file.)
#
# memory_health is also in py-modules (contributor PR #3) but is deliberately
# NOT in this unconditional list: it imports numpy, which is an optional
# `diagnostics` extra, not part of `.[dev]` — adding it here would fail this
# blanket import-cleanly test on any environment that installed `.[dev]`
# without also installing `.[diagnostics]` (e.g. plain CI). Its import
# behavior (clean when numpy is present, a clear install hint when it is
# not) is covered by backend/tests/test_contributor_tools.py instead.
MODULES = [
    "memory_tools",
    "cognitive_memory_api",
    "prompt_resources",
    "sill",
    "sill_cli",
    "sill_mcp_server",
    "worker",
    "rabbit_bridge",
    "beat_worker",
    "ingest_md_memories",
]

# Modules backing the [project.scripts] entry points, each expected to expose
# a callable ``main``.
ENTRY_POINT_MODULES = [
    "sill_cli",
    "sill_mcp_server",
    "worker",
]


@pytest.mark.parametrize("module_name", MODULES)
def test_module_imports(module_name):
    """Every declared backend module imports without error or side effects."""
    module = importlib.import_module(module_name)
    assert module is not None


@pytest.mark.parametrize("module_name", ENTRY_POINT_MODULES)
def test_entry_point_has_callable_main(module_name):
    """Each console-script module exposes a callable ``main``."""
    module = importlib.import_module(module_name)
    main = getattr(module, "main", None)
    assert callable(main), f"{module_name}.main is missing or not callable"
