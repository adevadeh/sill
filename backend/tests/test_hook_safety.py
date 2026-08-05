"""Executable exit-0-on-every-path invariant for every hook in plugin/hooks/.

Plan 2's whole-branch review found four hooks (later found: five — see
track-verification.py below) with a bare ``with open(LOG_FILE, "a")`` or
``STATE_FILE.write_text(...)`` outside any try/except. Any hook that reaches
that line while ``SILL_LOG_DIR`` points at a directory that doesn't exist
raises ``FileNotFoundError`` uncaught, which crashes the hook with a non-zero
exit — turning a "log a line, keep going" helper into a Stop/PreToolUse/
UserPromptSubmit event that can abort the parent tool call.

A prior sweep for this class of bug was `grep -rn "sys.exit(1)"
plugin/hooks/` (see plan2-task-7-brief.md step 3) — structurally blind to
*unhandled* exceptions, since none of these hooks call sys.exit(1)
explicitly; the crash comes from an uncaught traceback instead. This test
replaces that grep with something that actually runs each hook.

Every hook is discovered by globbing plugin/hooks/*.py (not hardcoded), so a
hook added later is covered automatically — falling back to an empty `{}`
payload if it isn't in PAYLOADS below. A hook-specific payload is worth
having where it actually reaches a write call (the whole point is to
exercise the code path that used to crash); an empty payload only proves the
hook doesn't crash on `{}`, which is a weaker but still real guarantee for
any hook not listed here.

No DB, no network: every invocation points SILL_DB_CONTAINER at a container
name that doesn't exist (docker exec fails in well under a second — timed at
~0.17s locally) and POSTGRES_HOST/PORT at a closed loopback port, so
track-reuse.py's direct psycopg2.connect() can't reach the real sill_db that
may happen to be running on this machine. SILL_INSIGHT_DETECT stays off so
response-patterns.py never calls out to ollama.
"""

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

HOOKS_DIR = Path(__file__).resolve().parents[2] / "plugin" / "hooks"

ALL_HOOKS = sorted(p.name for p in HOOKS_DIR.glob("*.py"))

# name -> (payload, env_extra). Chosen to reach at least one write call where
# the hook has one, not just the shortest early-exit branch.
PAYLOADS: dict[str, tuple[dict, dict]] = {
    "attribution-check.py": (
        {
            "hook_event_name": "PreToolUse",
            "tool_name": "mcp__sill__remember",
            "tool_input": {
                "content": "William wrote this finding and I concluded it held up.",
            },
        },
        {},
    ),
    "check-agreement.py": (
        {
            "hook_event_name": "Stop",
            "last_assistant_message": "You're right, my mistake — let me fix that.",
        },
        {},
    ),
    "check-corrections.py": (
        {
            "hook_event_name": "UserPromptSubmit",
            "prompt": "are you sure that is right? actually I think that's wrong",
        },
        {},
    ),
    "clear-handoff.py": (
        {"hook_event_name": "SessionStart", "source": "startup", "session_id": "s1"},
        {},
    ),
    "goodnight-checkpoint.py": (
        {"hook_event_name": "UserPromptSubmit", "prompt": "goodnight!"},
        {},
    ),
    "precompact-snapshot.py": ({}, {}),
    "response-patterns.py": (
        {
            "hook_event_name": "Stop",
            "last_assistant_message": "I should store this insight but I will do it later.",
        },
        {"SILL_INSIGHT_DETECT": "0"},
    ),
    "shell-idiom-guard.py": (
        {"tool_name": "Bash", "tool_input": {"command": "echo === && ls"}},
        {},
    ),
    "spontaneous-recall.py": (
        {
            "hook_event_name": "UserPromptSubmit",
            "prompt": "what did we discuss about the migration plan last time?",
            "session_id": "testsess1",
        },
        {},
    ),
    "state-language-check.py": (
        {
            "tool_name": "mcp__sill__remember",
            "tool_input": {
                "content": "I am feeling tired and need a break after this long session.",
            },
        },
        {},
    ),
    "track-reuse.py": (
        {
            "hook_event_name": "Stop",
            "last_assistant_message": "This response references nothing recalled.",
            "session_id": "testsess1",
        },
        {},
    ),
    "track-verification.py": (
        {"hook_event_name": "PostToolUse", "tool_name": "Bash"},
        {},
    ),
}


def run(hook_name: str, payload: dict, env_extra: dict, tmp_path: Path) -> subprocess.CompletedProcess:
    log_dir = tmp_path / "sill-log-does-not-exist"  # deliberately never created
    project_root = tmp_path / "project"
    project_root.mkdir(exist_ok=True)

    env = {
        **os.environ,
        # Safe-by-construction DB settings: docker-exec-based hooks fail in
        # well under a second against a container name that can't exist;
        # track-reuse.py's direct psycopg2.connect() gets refused instantly
        # against a closed loopback port instead of possibly reaching this
        # machine's real sill_db on the default host/port.
        "SILL_DB_CONTAINER": "sill_test_nonexistent_zzz",
        "POSTGRES_HOST": "127.0.0.1",
        "POSTGRES_PORT": "1",
        "SILL_INSIGHT_DETECT": "0",
        "SILL_PROJECT_ROOT": str(project_root),
        **env_extra,
        # SILL_LOG_DIR always wins: it's the one variable this test exists
        # to point at a nonexistent directory.
        "SILL_LOG_DIR": str(log_dir),
    }
    return subprocess.run(
        [sys.executable, str(HOOKS_DIR / hook_name)],
        input=json.dumps(payload),
        capture_output=True,
        text=True,
        env=env,
        timeout=15,
    )


@pytest.mark.parametrize("hook_name", ALL_HOOKS)
def test_hook_exits_zero_when_log_dir_missing(hook_name, tmp_path):
    payload, env_extra = PAYLOADS.get(hook_name, ({}, {}))
    r = run(hook_name, payload, env_extra, tmp_path)
    assert r.returncode == 0, (
        f"{hook_name} exited {r.returncode} with SILL_LOG_DIR missing.\n"
        f"stdout: {r.stdout}\nstderr: {r.stderr}"
    )


def test_every_hook_file_is_covered_by_the_glob():
    # Guard against the glob itself silently finding nothing (e.g. a path
    # typo), which would make the parametrized test above vacuously "pass"
    # with zero cases.
    assert len(ALL_HOOKS) >= 12, ALL_HOOKS


# ---------------------------------------------------------------------------
# Non-object JSON payload: every hook starts with `json.load(sys.stdin)`
# (or an equivalent), which happily accepts any valid JSON document — not
# just the `{"tool_name": ..., ...}` object shape every harness actually
# sends. Nine hooks called `.get(...)` (or `"key" in data`, which raises on
# a non-iterable-of-the-right-kind like an int/None/bool) on the parsed
# result without first checking it was a dict, so a bare JSON array,
# string, number, null, or bool crashed them with an unhandled
# AttributeError/TypeError — the same class of "every hook exits 0 on
# every path" violation test_hook_exits_zero_when_log_dir_missing exists
# to catch for a missing SILL_LOG_DIR, just from malformed stdin instead
# of a bad environment. Found by probing every hook in plugin/hooks/ with
# each payload below; run() is reused so a real SILL_LOG_DIR is still
# missing on top, proving both defenses compose.
# ---------------------------------------------------------------------------

NON_OBJECT_JSON_PAYLOADS = ["[]", '"just a string"', "42", "null", "true"]


def run_raw(hook_name: str, raw_stdin: str, tmp_path: Path) -> subprocess.CompletedProcess:
    log_dir = tmp_path / "sill-log-does-not-exist"
    project_root = tmp_path / "project"
    project_root.mkdir(exist_ok=True)
    env = {
        **os.environ,
        "SILL_DB_CONTAINER": "sill_test_nonexistent_zzz",
        "POSTGRES_HOST": "127.0.0.1",
        "POSTGRES_PORT": "1",
        "SILL_INSIGHT_DETECT": "0",
        "SILL_PROJECT_ROOT": str(project_root),
        "SILL_LOG_DIR": str(log_dir),
    }
    return subprocess.run(
        [sys.executable, str(HOOKS_DIR / hook_name)],
        input=raw_stdin,
        capture_output=True,
        text=True,
        env=env,
        timeout=15,
    )


@pytest.mark.parametrize("payload", NON_OBJECT_JSON_PAYLOADS)
@pytest.mark.parametrize("hook_name", ALL_HOOKS)
def test_hook_exits_zero_on_non_object_json_payload(hook_name, payload, tmp_path):
    r = run_raw(hook_name, payload, tmp_path)
    assert r.returncode == 0, (
        f"{hook_name} exited {r.returncode} on non-object JSON payload {payload!r}.\n"
        f"stdout: {r.stdout}\nstderr: {r.stderr}"
    )
    assert "Traceback" not in r.stderr, (
        f"{hook_name} printed a traceback on non-object JSON payload {payload!r}:\n{r.stderr}"
    )
