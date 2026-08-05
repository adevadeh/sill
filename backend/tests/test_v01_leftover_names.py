"""Four v0.1.0-era leaks a prior task's whole-tree sweep found and correctly
declined to fix in files it didn't own (Plan 5, Task 3's sweep). This file
pins the fix for each so a future edit can't silently reintroduce any of
them:

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
