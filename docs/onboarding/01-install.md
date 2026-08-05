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

The embeddings container downloads its model (~300MB) on first start, so the
first `docker compose` step is slow in a way later ones are not.

Then:

```bash
./verify.sh
echo "exit: $?"
```

Six checks: services healthy, `sill-mcp --help`, seed loaded, one hook
parsing a canned event, schema level current, adapter conformance. Success
is one `pass:` line per check and `All checks passed.` at the end.

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
didn't reach `~/.claude/.mcp.json` (or `~/.codex/config.toml`); re-run
`./install.sh` and read step 7's output.

Conformance is check 6 of `./verify.sh` above — the four-slot contract
(inject / mint / capture / track) that makes "supports harness X" something
you can check rather than something the README asserts. `docs/adapters.md`
has the contract, the Claude Code / Codex divergence table, and what's
version-fragile on the Codex side. Run it directly if you want it on its
own (pytest is a dev-only extra — `pip install -e backend[dev]` if it isn't
installed; `verify.sh` degrades to a dependency-free capture-slot check
without it):

```bash
cd backend && python3.10 -m pytest tests/test_adapter_conformance.py -q
```

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
file is the only channel it has. Delete `proof.txt`, write the grant, and
run the same command *without* the flag:

```bash
rm proof.txt
mkdir -p .claude
cat > .claude/settings.local.json <<'EOF'
{
  "permissions": {
    "allow": [
      "Bash(sill notice *)",
      "Write",
      "Edit"
    ]
  }
}
EOF
claude --print -p "Use the Write tool to create proof.txt containing 'hi'. Report what happened in one sentence." --setting-sources project < /dev/null
cat proof.txt
```

`Bash(sill notice *)` prefix-matches any command starting with
`sill notice ` — the beat's mint path. `Write` and `Edit` are already scoped
to this project by living in this project's settings file; narrow them
further once the broad grant is confirmed working, not before, or you will
be debugging scope and pattern syntax at the same time.

**Rung 1 passes and rung 2 fails.** This is the common case, and it has one
usual cause: the workspace has never been trusted, so Claude Code ignores
the settings file's `permissions.allow` entries with a warning rather than
an error. Confirmed here — a directory with a correct settings file and no
trust entry took the command-line grant and ignored the file one. Its own
warning names both fixes; the simpler is to run `claude` with no arguments
once in this directory and accept the trust dialog. The warning appears
inside a beat's **transcript**, not on any console you are watching, which
is why it goes unnoticed for weeks.

Clean up when both rungs pass:

```bash
rm proof.txt
```

Keep `.claude/settings.local.json`. It is the phase's real output.

---

Next: `docs/onboarding/02-backfill.md` — the consent-scoped scan, and the
first thing this system does that touches your working life.
