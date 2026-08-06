# Phases 1–4: Standing It Up

Four phases that end with software running, wired to the harnesses you
actually use, able to say what it is — and able to use its own tools when
nobody is watching. The last one is the one people skip, and skipping it
produces a system that looks healthy and does nothing.

Each phase below states what done looks like before it gives you commands.
If a phase doesn't reach its done-condition, stop there. The failures here
are all diagnosable, and none of them get easier by being carried forward
into a phase that assumes them.

## Phase 1 — Install

**Done when:** `./verify.sh` exits 0 and prints a `pass:` line for each of
its six checks.

```bash
./install.sh
```

Ten preflighted steps: the backend package and its console scripts, the
Docker stack (Postgres with pgvector and the graph extension, embeddings,
RabbitMQ, the maintenance worker), the methodology seed, the plugin symlink,
and the MCP server entry. `./install.sh --help` prints the full flag list;
`--scope home|project` is the one decision worth making deliberately, and
the README's "Install scope" section lays out the tradeoff.

The embeddings container downloads its model on first start (~1.2 GB of
float32 safetensors — the `-300m` in the model name is parameters, not
megabytes), so the first `docker compose` step is slow in a way later ones are
not.

**If step 4 gives up before the model is ready**, that is the one install
failure that is not a misconfiguration:

```text
install.sh: db/embeddings not healthy after 600s. Check 'docker compose -f .../docker-compose.yml logs'.
```

Measured on an Apple Silicon Mac, first boot took **903 s** from container
start to the first healthy probe — the compose file pins `platform:
linux/amd64`, so the embeddings image runs emulated there. The install is
idempotent: watch `docker compose -f backend/docker-compose.yml ps` until
`embeddings` reports `healthy`, then re-run `./install.sh`, and it will
continue from step 4. To wait longer in the first place, raise the timeout:

```bash
SILL_INSTALL_WAIT_HEALTHY_S=1800 ./install.sh
```

Then:

```bash
./verify.sh
echo "exit: $?"
```

Six checks: services healthy, the MCP server answering a real `initialize`
handshake over stdio, seed loaded, one hook parsing a canned event, schema
level current, adapter conformance. Success is one `pass:` line per check
and `All checks passed.` at the end; check 2's line names the server and
version that answered.

A failure names its own fix and stops at the first one. Its shape:

```text
=== Check 1/6: docker compose services healthy ===
  FAIL: db is 'missing', expected 'healthy'
```

`missing` means no such container — the daemon is down, or the stack was
never brought up, or another project on this machine has already claimed the
container name `sill_db` (set `SILL_DB_CONTAINER` in `backend/.env` if you
need two stacks side by side). Fix it and re-run; `verify.sh` is safe to run
as many times as you like.

## Phase 2 — Harness adapters

**Done when:** every harness you use lists a connected `sill` MCP server,
and adapter conformance passes.

Restart Claude Code and/or Codex first — the MCP server entry is read at
startup, so a session that was already open when you installed will not see
it.

```bash
claude mcp list
```

Look for a `sill` entry reported as connected. If it isn't there, the entry
didn't reach `~/.claude.json` (or `~/.codex/config.toml`); re-run
`./install.sh` and read step 7's output.

Conformance is check 6 of `./verify.sh` above — the four-slot contract
(inject / mint / capture / track) that makes "supports harness X" something
you can check rather than something the README asserts. `docs/adapters.md`
has the contract, the Claude Code / Codex divergence table, and what's
version-fragile on the Codex side. Run it directly if you want it on its
own. pytest is a dev-only extra, and it has to go into the *same* interpreter
`install.sh` put the backend in — a bare `pip` may not exist at all on a
machine that has only `python3`, and a bare `python3` will not have the
backend's dependencies:

```bash
SILL_PYTHON="$(ls "${PIPX_HOME:-$HOME/.local/pipx}/venvs/sill-memory/bin/python" 2>/dev/null \
  || ls "$HOME/.local/share/sill-venv/bin/python" 2>/dev/null \
  || command -v python3)"
"$SILL_PYTHON" -m pip install -e "backend[dev]"
cd backend && "$SILL_PYTHON" -m pytest tests/test_adapter_conformance.py -q
```

That is the same resolve-the-interpreter recipe `scheduling/README.md` uses,
and the same order `install.sh` itself resolves in. Without pytest,
`verify.sh` degrades to a dependency-free capture-slot check and says so.

Hooks are wired per project by default. If this project should have recall
and the guards, it needs `--hooks-for` — and phase 7's drill assumes the
guards are actually wired here:

```bash
./install.sh --hooks-for /path/to/this/project
```

## Phase 3 — The identity card

**Done when:** `sill identity show` prints a card whose name is
`null (not yet christened)`.

An instance should be able to answer "what am I, whose is this, and where is
the document that says why" from first boot — before recall, before a beat,
before anyone asks it to think about anything. That is all this file is.

```bash
sill identity init
sill identity set --engine claude --scope project --harnesses claude
sill identity show
```

```text
identity: /path/to/state/identity.json
  name:          null (not yet christened)
  charter_path:  null
  born_at:       2026-08-05T16:18:07.785014+00:00
  engine:        claude
  scope:         project
  harnesses:     ["claude"]
  christened_at: null
```

`null` here is a value, not a gap: it means *not yet christened*, and it is
stated in those words so an instance reading its own card mid-onboarding can
tell the difference between "nobody has named me yet" and "this file is
unreadable." Both cases are reported, in plain language, without a
traceback. The name and charter arrive in phase 8, not now. Field reference:
`docs/identity.md`.

## Phase 4 — Permissions verified

**Done when:** a headless agent-CLI run in this project's directory
**creates a file**. Not "exits 0" — a denied run also exits 0.

This is the phase whose absence is invisible. An agent CLI running
non-interactively with no tool permissions configured for its directory does
not hang waiting for a prompt, because there is nobody to prompt. It denies
the tool call, the agent notices the denial, gives up, and replies in prose
describing what it tried. Exit code 0. Nothing written. A beat worker with
this problem ticks on schedule forever, produces transcripts full of
plausible reports, and accomplishes nothing.

Confirm the failure yourself, in a throwaway directory with no settings
file, so you know what it looks like:

```bash
mkdir -p /tmp/sill-permission-demo
cd /tmp/sill-permission-demo
claude --print -p "Use the Write tool to create proof.txt containing 'hi'. Report what happened in one sentence." --setting-sources project < /dev/null
echo "exit: $?"
ls proof.txt
```

Observed:

```text
The write was blocked because permission to create proof.txt hasn't been granted yet, so the file was not created.
exit: 0
ls: proof.txt: No such file or directory
```

Exit 0 and no file. That pair is the whole problem: the exit code is not
evidence of anything, and the only discriminator is whether the file exists.

Now the two-rung check, in this order, in **this project's** directory.

**Rung 1 — does the CLI work here at all?** Grant the tool on the command
line, bypassing every settings file:

```bash
claude --print -p "Use the Write tool to create proof.txt containing 'hi'. Report what happened in one sentence." --setting-sources project --allowedTools Write < /dev/null
cat proof.txt
```

`hi` in `proof.txt` means the CLI, the model, and the working directory are
all fine, and any remaining problem is in the permission channel.

**Rung 2 — does the channel the worker actually uses work?** The beat worker
spawns a fixed command with no `--allowedTools` flag, so a project settings
file is the only channel it has. Delete `proof.txt`, **merge** the grant into
this project's settings file, and run the same command *without* the flag.

Merge rather than overwrite: phase 2's `./install.sh --hooks-for` already put
a `hooks` block in this exact file, and a `cat >` heredoc here silently
deletes it — which phase 7's drill then can't find.

```bash
rm proof.txt
mkdir -p .claude
python3 - <<'PY'
import json, pathlib
p = pathlib.Path(".claude/settings.local.json")
d = json.loads(p.read_text()) if p.exists() else {}
d.setdefault("permissions", {})["allow"] = ["Bash(sill notice *)", "Write", "Edit"]
p.write_text(json.dumps(d, indent=2) + "\n")
print("hook entries preserved:", sum(len(v) for v in d.get("hooks", {}).values()))
PY
claude --print -p "Use the Write tool to create proof.txt containing 'hi'. Report what happened in one sentence." --setting-sources project,local < /dev/null
cat proof.txt
```

**`project,local`, not `project`.** Claude Code has three setting sources —
`user`, `project`, `local` — and `.claude/settings.local.json` is the *local*
one. `--setting-sources project` does not load it, so a rung-2 check with
that flag alone fails no matter how correct the file is, and fails **silently**:
the grant is not ignored with a warning, the file is simply never read. The
worker passes no `--setting-sources` at all, so it gets every source; the
comma form above is what makes this check test the worker's channel instead
of a narrower one.

`Bash(sill notice *)` prefix-matches any command starting with
`sill notice ` — the beat's mint path. `Write` and `Edit` are already scoped
to this project by living in this project's settings file; narrow them
further once the broad grant is confirmed working, not before, or you will
be debugging scope and pattern syntax at the same time.

**What this list does not grant is a read path into memory**, and there is no
`Bash(sill recall *)` to add — the CLI has no `recall` subcommand (`sill
--help` lists `seed`, `db`, `verify`, `notice`, `identity`, `backfill`).
Recall reaches a beat through the **MCP server**, not the shell, so a beat
that needs to look something up needs `sill` connected in the harness the
worker spawns (phase 2), not another `Bash(...)` entry here. A beat asked to
measure recall with only the grants above will correctly report that it
cannot — which is the right behavior and a confusing hour if you were not
expecting it.

**Rung 1 passes and rung 2 still fails.** Two causes, in the order worth
checking:

1. **The source mismatch above** — the symptom is total silence: no warning,
   no mention of the settings file, just an agent reporting that permission
   was not granted. Re-run with `--setting-sources project,local`.
2. **The workspace has never been trusted**, so Claude Code reads the file
   and then discards its `permissions.allow` entries. Unlike (1), this one
   announces itself, and names both fixes:

```text
Ignoring 3 permissions.allow entries from .claude/settings.json: this workspace
has not been trusted. Run Claude Code interactively here once and accept the
trust dialog, or set projects["/path/to/this/project"].hasTrustDialogAccepted:
true in ~/.claude.json.
```

The simpler fix is the first one it names: run `claude` with no arguments once
in this directory and accept the trust dialog. Note the path in that warning is
the *resolved* one — on macOS a project under `/tmp` is recorded as
`/private/tmp/...`. The warning appears inside a beat's **transcript**, not on
any console you are watching, which is why it goes unnoticed for weeks.

Clean up when both rungs pass:

```bash
rm proof.txt
```

Keep `.claude/settings.local.json`. It is the phase's real output.

---

Next: `docs/onboarding/02-backfill.md` — the consent-scoped scan, and the
first thing this system does that touches your working life.
