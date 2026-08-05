# Clean-Machine Acceptance Rehearsal — v0.2.0

**Date:** 2026-08-05
**Ref:** `275bd70` (branch `feat/v0.2-release`), rehearsed from a fresh clone
**Question:** does "please get this working" actually work, for someone who
has never run this?

Every plan in the v0.2 series verified its own phases against a development
machine that already had a live Sill stack, a populated tool-permission
allow-list, both agent harnesses configured, and containers running. None of
that is true of the machine this ships to. This document is the record of
walking `docs/onboarding/README.md` end to end with as little of that as
could be arranged, and it exists mainly for the failures it found.

**Bottom line.** A first real operator will not be the first person to walk
this path — nine of the nine phases were reached, and eight of them reached
their stated done-condition. But **four phases could not be completed as
documented** (1, 2, 4, 7) and a fifth printed output the doc does not match
(8); one of those was a defect no documentation change could fix — `sill-mcp`
did not start at all on a fresh install. All are fixed here. The
parts of the christening that need an actual person — a charter in their own
words, and a name they will be glad to type — were not rehearsed and cannot
be.

---

## 1. The environment, and exactly what leaked

Host: macOS 25.5.0, Apple Silicon (`arm64`). Docker via OrbStack, server
29.4.0. `python3` is Homebrew's **3.14.4**. **`pipx` is not installed**, so
`install.sh` took its documented venv fallback throughout.

**Isolated:**

| Thing | How |
|---|---|
| `HOME` | `/tmp/sill-rehearsal-home`, created empty. No pre-existing `.claude`, `.codex`, `.local`, or state directory. |
| Repo | `git clone` of the working repo into `/tmp/sill-rehearsal/sill`, checked out at `275bd70`. The development checkout was never installed from. |
| `PATH` | Rebuilt from scratch, **with `~/miniconda3/bin` removed** — that is where this machine's `python3.10` and an already-installed `sill`/`sill-mcp`/`sill-worker` live. Probe after isolation: `python3.10 -> NOT FOUND`, `pipx -> NOT FOUND`, `sill -> NOT FOUND`. |
| Console scripts | `install.sh`'s venv fallback put them in `/tmp/sill-rehearsal-home/.local/share/sill-venv`, symlinked into the throwaway `~/.local/bin`. |
| State dir | `$XDG_STATE_HOME` under the throwaway HOME; the identity card and backfill archive landed there. |
| Docker | Compose project `sillrehearsal`, containers `sillx_db` / `sillx_embeddings` / `sillx_rabbitmq` / `sillx_maintenance_worker`, host ports 55432 / 55672 / 45672. Nothing shared with the live stack. |
| Transcripts | `sill backfill` was only ever pointed at a synthetic fixture tree (`/tmp/sill-rehearsal/fixture-home`). No real history was scanned, planned against, or archived. |

**Leaked, named:**

1. **The docker daemon.** There is one per machine. A fresh `HOME` also loses
   OrbStack's docker *context* (it lives in the real `~/.docker`), so the CLI
   fell back to `unix:///var/run/docker.sock`, which OrbStack does not create,
   and `install.sh` stopped at preflight with `docker is installed but the
   daemon isn't responding` — a correct message for a condition the isolation
   created. Bridged with an explicit `DOCKER_HOST` pointing at the real
   socket.

2. **The `claude` CLI's login.** `claude` under the throwaway HOME answers
   `Not logged in · Please run /login`. The token is in the macOS Keychain but
   the CLI gates on the real `~/.claude.json`; copying the account-metadata
   keys across (no secret is in them) did not help. **So the two agent-CLI
   phases — 4 and 6 — ran with `HOME=/Users/…`, everything else still
   isolated.** Two mitigations kept that from contaminating the result:
   phase 4 uses `--setting-sources`, which the runbook already prescribes; and
   for phase 6 the worker's `SILL_BEAT_CLI` pointed at a shim injecting
   `--setting-sources project,local`, so no user-scope settings or hooks
   applied. A probe confirmed the beat children could not see the operator's
   ambient instructions file. The live memory store was counted immediately
   before and immediately after the beat window: **39,936 rows both times**,
   and it holds zero rows from any rehearsal voice. (That is a statement about
   the beats. For what the rehearsing *session* wrote there, see (4).)

3. **Residue the CLI wrote in the real HOME**, as a side effect of (2), none
   of it by hand: session transcripts under
   `~/.claude/projects/-private-tmp-sill-rehearsal-sill/` and
   `…-sill-permission-demo/`, and one `projects` entry in `~/.claude.json`
   recording the rehearsal directory as **untrusted**. `mcpServers` there is
   unchanged, and `~/.codex/config.toml` was not touched at all.

4. **The rehearsing session's own Stop hook.** Separate from the beats, and
   found only by checking afterward: the operator's `response-patterns` hook
   auto-stored six rows into the live store during the rehearsal window,
   because it attributes by working directory and this work happened in the
   Sill checkout rather than the one directory where that hook is configured
   log-only. Nothing the rehearsal *ran* did this — the beats wrote to the
   isolated store, and the live store holds zero rows from any rehearsal
   voice. It is recorded here because "the live store was not written to"
   would otherwise have been the natural thing to say, and it would have been
   false.

5. **The multi-instance config path.** A genuinely clean machine needs no
   `backend/.env`; this one does, because the live stack already owns the
   default container names and ports. So the rehearsal exercised the
   documented side-by-side configuration throughout — which is how findings
   F2, F8 and part of F9 below were found, and which means the *default* single-stack
   path got less exercise than the non-default one in exactly those places.

6. **Not attempted: the collision itself.** Proving that
   `SILL_DB_CONTAINER`-alone is insufficient by letting Compose try to create
   `sill_embeddings` would have aimed a `docker compose up` at the live
   stack's containers. The `.env.example` finding at the end of §3 is
   therefore argued from the compose file (four `container_name:` keys, one
   of them documented) rather than from a crash.

---

## 2. The walk

Nine phases, in the runbook's order. "Done?" is the doc's own stated
done-condition, not a summary judgment.

| # | Phase | Command | What actually happened | Done? |
|---|---|---|---|---|
| 1a | Install | `./install.sh` | Steps 1–3 fine; step 4 exited 1: `db/embeddings not healthy after 600s`. The embeddings model was still loading. | **No** — see F1 |
| 1b | Install, retried | `./install.sh` | Exit 0. Steps 4–10 ran; seed reported `inserted=22, skipped=0, errors=0`. | Yes |
| 1c | `verify.sh` | `./verify.sh` | Checks 1–2 pass, then `FAIL: memories count is '0', expected >= 22`. It had queried a *different* container. | **No** — see F2 |
| 1d | `verify.sh`, fixed | `./verify.sh` | Six `pass:` lines, `All checks passed.`, exit 0. | Yes |
| 2a | Adapters — Codex | `codex mcp list` | `sill  sill-mcp  …  enabled`. | Yes |
| 2b | Adapters — Claude | `claude mcp list` | `No MCP servers configured.` after a successful install. | **No** — see F3 |
| 2c | Adapters, after F3 fix | `claude mcp list` | `sill: sill-mcp - ✘ Failed to connect`; the server itself was broken. | **No** — see F4 |
| 2d | Adapters, after F4 fix | `claude mcp list` | `sill: sill-mcp - ✔ Connected`. | Yes |
| 2e | Conformance | `… -m pytest tests/test_adapter_conformance.py -q` | `11 passed`. The doc's own command failed three ways first — see F5. | Yes |
| 2f | Project hooks | `./install.sh --hooks-for <project>` | Wrote `.codex/hooks.json`; merged 8 entries across 4 events into `.claude/settings.local.json`. | Yes |
| 3 | Identity card | `sill identity init` / `set` / `show` | Card printed with `name: null (not yet christened)` — matching the doc line for line. | Yes |
| 4a | Permissions, the demo | `claude --print … --setting-sources project` in a cold dir | `The write was denied … so the file was not created.` / `exit: 0` / no file. The documented pair, reproduced. | Yes |
| 4b | Permissions, rung 1 | `… --allowedTools Write` | `proof.txt` contains `hi`. | Yes |
| 4c | Permissions, rung 2 | the doc's `cat > .claude/settings.local.json` heredoc, then `--setting-sources project` | Clobbered phase 2f's hooks (8 entries → 0), then the write was refused anyway. | **No** — see F6, F7 |
| 4d | Rung 2, corrected | merge + `--setting-sources project,local` | `hook entries preserved: 8`, and `proof.txt` contains `hi`. | Yes |
| 5a | Backfill `plan` | `sill backfill plan --home <fixture>` | 3 files across 3 projects, `Nothing has been read, written, stored, or logged`. Narrowed scope dropped `codex` from `scanned=` as documented. | Yes |
| 5b | Consent gate | `sill backfill run --home <fixture>` | `refusing to read or archive anything without --confirm`, exit **64**. | Yes |
| 5c | Backfill `run` | `… --projects=-…-orrery --confirm` | Archived 1 file + manifest into the throwaway state dir, printed its own undo command. | Yes |
| 6 | Supervised beats | `SILL_BEAT_INTERVAL_SECONDS=1 sill-worker --mode beat` | Startup banner matched the doc including the derived guard scope. `[analyst] Beat complete in 238.4s`, `[reflector] Beat complete in 142.6s`. Both wrote real files; the analyst's mint spliced a real receipt (`7a2bef1d…`, verified present in the store). Both entries read. | Yes |
| 7a | Fault drill | the documented refusal payload | **Silence.** Store healthy, guard mute — the exact failure the drill exists to make impossible. | **No** — see F8 |
| 7b | Fault drill, resolved | same payload, guard's DB coordinates supplied | The full `permissionDecision: deny` JSON, byte-for-byte as the doc quotes it. | Yes |
| 7c | Positive control | placeholder-line payload | No output, exit 0 — allowed. | Yes |
| 7d | Second guard | `tool-type-witness.py` payload | Denied, naming the honest alternatives. | Yes |
| 8a | Charter | `date -u … >> charter.md` | Placeholder file stamped `2026-08-05T19:44:15Z`. **The charter itself was not rehearsed** — see §4. | Partly |
| 8b | Naming | `sill identity set --name … --charter charter.md` | `christened_at: 2026-08-05T19:44:15.454837+00:00`. | Yes |
| 8c | First mint | `sill notice … --receipt-to journal/christening-001.md` | First attempt **failed**, and the placeholder line stayed literal — the receipt architecture working from the honest side. Second attempt: `Receipt written by the store into … (line 5)`, placeholder gone, id has a row. | Yes |
| 8d | Cadence | recorded in `charter.md` | `SILL_BEAT_INTERVAL_SECONDS=21600`, written down, nothing started. | Yes |
| 9 | Nothing scheduled | `launchctl list`, `crontab -l`, `pgrep` | No `com.sill.*` job loaded, no plist installed, no cron entry, no worker running. | Yes |

---

## 3. What the documentation got wrong

Nine findings. Each was hit by running the documented command, and each fix
was re-verified by re-running that phase — the "Verified" column says with
what.

### F1 — `install.sh` gives up before the model is ready, and says nothing about that

`install.sh` waits 600 s for the embeddings healthcheck. Measured here:
container start `19:04:16Z`, first healthy probe `19:19:22Z` — **903 s**. The
compose file pins `platform: linux/amd64`, so on Apple Silicon the image runs
emulated, and the healthcheck's own `start_period` is also 600 s, so the
service passes *through* `unhealthy` on a normal first boot. The failure
message names a log command and does not mention that the install is
idempotent, that the download is still running, or that
`SILL_INSTALL_WAIT_HEALTHY_S` exists — that variable appeared in no `.md`
file in the repo.

**Fixed:** README and `01-install.md` now give the measured number, say the
retry is safe, and document the override.
**Verified:** re-ran `./install.sh` after the container went healthy — exit 0,
steps 4–10 complete.

### F2 — `verify.sh` and `upgrade.sh` ignored `backend/.env`, so they could check the wrong database

`verify.sh` check 1 runs Compose from `backend/`, so `.env` is loaded and the
check goes green. Check 3 shells `docker exec "${SILL_DB_CONTAINER:-sill_db}"`
straight from the process environment, which nothing sets. The result here was
`FAIL: memories count is '0'` — it had queried the *other* Sill on this
machine. `01-install.md`'s own remedy for a name collision is "set
`SILL_DB_CONTAINER` in `backend/.env`", which is precisely what breaks it.
Worse than the failure: with two installs sharing credentials, this reports a
**pass** off the wrong database.

**Fixed:** both scripts now load `backend/.env` with Compose's precedence
(exported shell variable wins).
**Verified:** `./verify.sh` → six passes, exit 0; new tests in
`test_env_and_mcp_wiring.py` (8 cases, both scripts) pin the parser, the
precedence, the missing-file case, and the ordering.

### F3 — `install.sh` registered the MCP server where Claude Code does not look

Step 7 wrote `~/.claude/.mcp.json` and reported
`added mcpServers.sill -> …`. Claude Code's user-scope registry is
`~/.claude.json`; that file does not exist on this machine's *working* install
either. `claude mcp list` after a clean install: `No MCP servers configured.`
Phase 2's remedy — "re-run `./install.sh` and read step 7's output" — is an
infinite loop, because step 7 reports success every time. The Codex half was
correct all along.

**Fixed:** step 7 now runs `claude mcp add --scope user sill -- sill-mcp` when
the CLI is present (idempotent by its own report), and merges into
`~/.claude.json` otherwise.
**Verified:** removed the entry, re-ran `./install.sh`, then
`claude mcp list` → `sill: sill-mcp - ✔ Connected`. Four tests pin the path,
the command, the entry shape, and that `--dry-run` writes nothing.

### F4 — the MCP server did not run on a fresh install, and `verify.sh` said it was fine

`backend/pyproject.toml` asked for `mcp>=1.0.0`. A fresh install resolves that
to **2.0.0**, which removed the `Server.list_tools()` API `sill_mcp_server.py`
registers through. An `initialize` handshake against the freshly installed
server returned, in full:

```
'Server' object has no attribute 'list_tools'
```

The development machine has mcp 1.25.0, which is why nothing caught it.
`verify.sh` check 2 passed the whole time, because `sill-mcp --help` exits
before touching the SDK. **This is the finding that most justifies the
exercise:** a green six-of-six verify on an MCP server that could not serve a
single tool.

**Fixed:** pinned `mcp>=1.0.0,<2`, with the reason in the dependency itself.
**Verified:** reinstalled (resolved 1.29.0); the handshake now returns
`{"jsonrpc":"2.0","id":1,"result":{…"serverInfo":{"name":"sill","version":"0.2.0"}}}`,
and `claude mcp list` reports Connected.
**Not fixed:** check 2 still only runs `--help`. See §4.

### F5 — the docs told operators to run an interpreter that does not exist

Eight invocations of `python3.10` across `01-install.md`, `02-backfill.md`,
and `identity.md`. On a machine whose Python is 3.11/3.12/3.13/3.14 that is
`command not found` — observed on the first one run here. The two follow-on
commands fail too: `python3 -m pytest` gives `No module named pytest`, and the
doc's own remedy `pip install -e backend[dev]` gives `command not found: pip`
(Homebrew Python ships `pip3`, and there was no pipx).

**Fixed:** all eight now say `python3`; the pytest instruction resolves the
interpreter that actually has the backend, the same way `scheduling/README.md`
and `install.sh` already do.
**Verified:** every corrected command re-run — the identity-path one-liners,
the backfill fixture block, the argparse-gotcha demonstration (which still
fails exactly as documented, `expected one argument`), and the conformance
suite (`11 passed`). A new test refuses any `python3.N` in any shipped doc.

### F6 — phase 4 deleted phase 2's work

Rung 2's `cat > .claude/settings.local.json <<'EOF'` overwrites the file
`install.sh --hooks-for` writes its hooks into, two phases earlier — and phase
2 says phase 7's drill depends on those hooks. Measured: `hook entries: 8`
before, `hook entries: 0` after.

**Fixed:** rung 2 merges, and says why.
**Verified:** re-ran from the phase-2 state — `hook entries preserved: 8`.

### F7 — rung 2 could not pass as written, and the doc misdiagnosed why

The command pairs `.claude/settings.local.json` with
`--setting-sources project`. Claude Code has three sources — `user`, `project`,
`local` — and `settings.local.json` is the **local** one, so the file was
never read. The doc attributes the failure to workspace trust. Trust is a real
second cause and it looks different: with the grants in `settings.json`, the
CLI announces itself —

```
Ignoring 3 permissions.allow entries from .claude/settings.json: this workspace
has not been trusted. …
```

— whereas the source mismatch is silent. Reproduced both ways in one sitting,
with `hasTrustDialogAccepted: False` throughout: `--setting-sources project`
fails, `--setting-sources project,local` writes the file.

**Fixed:** the command uses `project,local`; the diagnosis lists the source
mismatch first, with the silence as its tell, and quotes the real trust
warning.
**Verified:** corrected command run from a clean phase-2 state — `proof.txt`
contains `hi`, workspace still untrusted.

### F8 — the drill's silence has a third cause the doc does not name

The documented refusal payload printed **nothing** on the first run, with the
stack healthy and `verify.sh` green. The doc's only account of silence is "the
store could not be reached", and its only diagnostic is `docker compose ps`,
which looked perfect. The guard does not hold a connection; it shells to
`docker exec` using `SILL_DB_CONTAINER` / `SILL_DB_USER` / `SILL_DB_NAME` from
its own environment, and does not read `backend/.env` — so a side-by-side
install silently fails open. A guard that has never fired is
indistinguishable from a guard that is not wired, which is the drill's whole
premise, and here the doc's remedy would have sent the operator to check a
healthy container.

**Fixed:** "What silence means" now gives the guard's own query as the first
diagnostic, ahead of `docker compose ps`, and names the variables.
**Verified:** the new diagnostic errors clearly with the default name and
answers `23` with the right one; the refusal then printed in full.

### F9 — three smaller output mismatches

- `03-first-beats.md`'s transcript header omitted the `# Model:` line the
  worker really writes. **Fixed** (and flagged as best-effort, unlike
  `# Spawned:`).
- `05-christening.md`'s sample receipt reads `[<name>/assertive]`; an unforced
  mint prints `[<name>/untagged]`. **Fixed**, with the `--force` values named.
  **Verified:** `[rehearsal/untagged]` without the flag, `[rehearsal/assertive]`
  with it.
- README's status line still said `v0.1.0`, and the "~300MB" model figure
  appears in four places against a measured **1.2 GB** on disk (1.1 GB in one
  blob). **Fixed** in README, `concepts.md`, `01-install.md`, and
  `.env.example`.

Also fixed, found the same way: `.env.example` documented one of the four
container-name overrides and no Compose project name, while README's advice
for running two instances named only `SILL_DB_CONTAINER`. Both now carry the
full set, and README has a worked side-by-side block.

---

## 4. Explicitly unverified

Nothing below was observed to work. It is listed so the first operator knows
where they are still the first.

1. **A charter, and a name.** Phase 8's substance is prose in a person's own
   words and a name they chose. A placeholder file with a timestamp was
   produced to exercise the mechanism; the act itself is not rehearsable by
   anything, and no agent should simulate it.

2. **Phase 9's schedule.** Deliberately not installed — the phase's own
   done-condition is that *nothing is scheduled yet*. The launchd and systemd
   templates in `scheduling/` were not loaded, so the token substitution has
   never been executed on a clean machine. `test_scheduling_templates.py`
   covers their shape, not their loading.

3. **Ctrl-C stopping the worker.** Phase 6 says to press Ctrl-C. This
   rehearsal ran the worker detached, where SIGINT is ignored by inheritance,
   and stopped it with SIGTERM. `beat_worker.py` installs no
   `KeyboardInterrupt` handler, so a real Ctrl-C most likely prints a
   traceback and, mid-beat, may not return the terminal until the beat's
   timeout (30 minutes by default). Unconfirmed — it needs a TTY.

4. **Whether a beat child can reach memory through MCP.** The shim that kept
   the beats out of the operator's user-scope settings also excluded
   user-scope MCP servers, and the analyst beat reported the `sill` tools as
   not connected. On a real install the entry F3 now writes is user-scope, so
   it should be there — *should*, not *was observed to be*. This is the one
   remaining question that materially affects what a beat can do.

5. **`verify.sh` check 2 still cannot see a broken MCP server.** F4's pin
   prevents the specific resolution that broke it; the check that reported
   green throughout is unchanged, and `--help` will exit 0 the next time the
   SDK moves. A handshake-based check is the obvious fix and is not in this
   change.

6. **Where the beat's mint found the right database.** The analyst's beat ran
   a bare `sill notice` (confirmed from its session transcript — no
   environment prefix) and the row landed in the rehearsal store. The same
   bare command from the operator shell, in the same directory, fails against
   the default container name — reproduced twice. The mechanism was not
   determined. Recorded as an open question rather than explained.

7. **A truly clean machine.** The six leaks in §1 are the honest bound on
   everything above. In particular: no first boot was observed on a host
   without an existing Sill, so the *default* container names and ports —
   what an actual new operator uses — were never exercised end to end. The
   collision-avoiding config was.

8. **Linux, and any non-Apple-Silicon host.** Every measurement here is from
   one macOS ARM machine running emulated `linux/amd64` containers. F1's
   903 s in particular should be expected to be much smaller elsewhere.

---

## 5. If you are the first operator

Read §4 first, then `docs/onboarding/README.md`. If a phase behaves
differently than its doc says, that is worth reporting — the nine findings
above were all found by exactly that, and every one of them had been shipped
past by people who believed the document.
