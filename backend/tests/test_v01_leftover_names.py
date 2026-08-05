"""v0.1.0-era leaks found by whole-tree sweeps and fixed in files their
finder didn't own. This file pins each fix so a future edit can't silently
reintroduce any of them.

The first four were named by Plan 5, Task 3's own whole-tree sweep and
handed to Task 4 to fix:

1. `.gitignore` didn't cover `charter.md` / `journal/` / `notes/` — an
   operator's charter and beat journals are their content, not the
   software's, and belong out of `git status` by default.
2. `backend/sill_mcp_server.py` read the DSN override from `AGI_DB_DSN`,
   a name from the upstream research project's env-var vocabulary.
3. `backend/worker.py` read the `--mode` default from `AGI_WORKER_MODE`,
   same vocabulary.
4. `docs/concepts.md`'s Source Card example attributed a verbatim quote to
   a real, named individual from a private discussion they never published.
   The fix keeps the same teaching point (quote + source pointer +
   discriminator) with a placeholder attribution — the same convention
   `docs/extending.md`'s parallel "Alice" example already uses.

The rest were found by Task 4's own whole-tree sweep (a superset pattern
of Task 3's — it also matches `agi_memory`/`agi-memory` and bare
`AGI `-prefixed module titles, neither of which Task 3's pattern covered):

5. `backend/worker.py`'s own module docstring was titled "AGI Workers" —
   in the same file as fix #3, missed because a string search for
   `AGI_WORKER_MODE` doesn't match `AGI Workers`.
6. `backend/memory_tools.py` (dead code — nothing in this tree imports
   from it; it ships only because it's smoke-tested for importability
   and listed in `pyproject.toml`'s `py-modules`) was titled "AGI Memory
   MCP Tools" and its legacy `psycopg2` connection helpers defaulted
   `dbname` to `'agi_memory'` in five places.
7. `plugin/codex.hooks.json.template` — the live config Codex hook-wiring
   renders — matched MCP tool names against
   `mcp__(agi_memory|agi-memory|sill)__...`. A fresh Sill install's MCP
   server only ever registers as `sill` (`Server("sill")` in
   `sill_mcp_server.py`), so the other two alternatives were dead
   matcher branches from the pre-rename project, mirrored verbatim into
   `docs/hooks.md`'s own documentation of the same matcher — which
   directly contradicted that same doc's nearby claim, two sections
   earlier, that "Nothing in this repo still defaults to the old
   agi-memory-project names."
"""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def read(rel: str) -> str:
    return (ROOT / rel).read_text(encoding="utf-8")


def test_gitignore_excludes_operator_content():
    text = read(".gitignore")
    for pattern in ("charter.md", "journal/", "notes/"):
        assert pattern in text, (
            f".gitignore does not exclude '{pattern}' — an operator's charter "
            "and beat journals would show up in git status by accident"
        )


def test_mcp_server_dsn_env_var_has_no_v01_name():
    text = read("backend/sill_mcp_server.py")
    assert "AGI_DB_DSN" not in text, "the v0.1.0-era DSN env var name survives"
    assert "SILL_DB_DSN" in text, "the --dsn flag should default from SILL_DB_DSN"


def test_worker_mode_env_var_has_no_v01_name():
    text = read("backend/worker.py")
    assert "AGI_WORKER_MODE" not in text, "the v0.1.0-era mode env var name survives"
    assert "SILL_WORKER_MODE" in text, "the --mode flag should default from SILL_WORKER_MODE"


def test_concepts_doc_does_not_attribute_a_quote_to_a_real_person():
    text = read("docs/concepts.md")
    assert "William" not in text, (
        "docs/concepts.md still attributes a quote to a real, named individual "
        "from a conversation they did not publish"
    )
    # The teaching point (verbatim quote + source pointer + discriminator)
    # must survive the de-identification, not just the name disappear.
    assert "in-context programming" in text
    assert "source pointer" in text.lower()


def test_worker_docstring_has_no_v01_title():
    text = read("backend/worker.py")
    assert "AGI Workers" not in text
    assert "Sill Workers" in text


def test_memory_tools_has_no_v01_names():
    text = read("backend/memory_tools.py")
    assert "agi_memory" not in text, "a legacy dbname default still reads 'agi_memory'"
    assert "AGI Memory" not in text, "the module docstring still says 'AGI Memory'"


def test_codex_hooks_template_matcher_has_no_dead_v01_alternatives():
    text = read("plugin/codex.hooks.json.template")
    assert "agi_memory" not in text and "agi-memory" not in text, (
        "the PreToolUse matcher still carries dead agi_memory/agi-memory "
        "alternatives — a fresh install's MCP server only ever registers as 'sill'"
    )
    assert text.count("mcp__sill__") >= 2, "the matcher should still match the real server name"


def test_hooks_doc_matcher_quotes_match_the_template_not_the_old_names():
    """docs/hooks.md legitimately says 'agi_memory' once, inside its own
    self-check grep command (a detector literal, same idiom as
    test_prompts.py's HOUSE regex) — so this checks for the specific dead
    matcher substring, not the bare word."""
    text = read("docs/hooks.md")
    assert "(agi_memory|agi-memory|sill)" not in text, (
        "docs/hooks.md still quotes the dead matcher alternation that no "
        "longer matches plugin/codex.hooks.json.template"
    )
