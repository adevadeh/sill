# Phase 6: Supervised First Beats

**Done when:** every configured voice has logged `Beat complete` at least
once, the files they wrote exist, and you have read one of them.

Supervised means exactly what it says: the worker runs in the foreground, on
a short interval, while you watch the log. Nothing is scheduled in this
phase. You are answering one question — *does a beat, run by this machine,
with these permissions, actually produce something?* — and you want the
answer in five minutes rather than in two hours of silence.

Do phase 4 (`docs/onboarding/01-install.md`) first. A beat with unverified
permissions exits 0, writes nothing, and reports plausibly. This phase
cannot distinguish that from a working install if you skip it.

## Configure the voices

```bash
cp backend/beats.example.json beats.json
mkdir -p notes journal logs/analyst logs/reflector
```

The example ships two voices — an analyst that measures, tests and builds,
and a reflector that thinks about something without needing to produce an
artifact. Each declares a standing prompt, a transcripts directory, and an
`output_glob`. Keep the `output_glob`s: a voice without one is judged on
exit code alone, and exit code cannot see the failure phase 4 is about.
`docs/beats.md` has the full field reference.

## Seed the first beats with something to bite on

A first beat in an empty room writes about the empty room. It orients, finds
no prior entries, no open questions, nothing anyone wants to know, and
produces a competent paragraph about beginning. That entry is not a system
failure and it is also not worth reading.

So put one or two real questions where the voices will find them while
orienting — in `notes/` for the analyst, in `journal/` for the reflector, or
in the `kickoff` line of `beats.json` if you want to be direct about it.
Take them from your charter if you have already drafted it, or from whatever
you actually want to know: something checkable for the analyst ("does recall
return anything useful for the last three things I worked on?"), something
open for the reflector. A question the beat can fail at is better than a
theme it can only elaborate.

## Run it by hand

```bash
SILL_BEAT_INTERVAL_SECONDS=1 sill-worker --mode beat
```

The one-second interval makes the loop fire again immediately instead of
waiting two hours. Run it in the foreground and read the log as it goes.
The startup banner names every voice and the guard scope it derived:

```text
INFO -   Voice [analyst]: prompt=prompts/analyst.md, transcripts=logs/analyst
INFO -   Voice [reflector]: prompt=prompts/reflector.md, transcripts=logs/reflector
INFO -   Guard scope (SILL_BEAT_JOURNAL_DIRS for each child): 'notes/:logs/analyst/:journal/:logs/reflector/'
```

Note that last line — phase 7's drill uses it. The worker derives the guard
scope from your own `beats.json` on every spawn; you never set it by hand.

Then, per beat, exactly one outcome line. Success looks like this:

```text
INFO - [analyst] Beat complete in 0.3s — transcript 20260805-091928.txt
INFO - [reflector] Beat complete in 0.1s — transcript 20260805-091929.txt
```

"Complete" means exit 0 **and** the voice's `output_glob` gained a new file.
A real beat takes minutes, not the fractions of a second above.

The failure this phase exists to catch looks like this:

```text
WARNING - [analyst] Beat exited 0 in 0.3s but produced no file matching 'notes/analyst-*.md' — the agent CLI may be denying tools non-interactively; see docs/beats.md permissions. Rotation remains on [analyst].
```

Exit 0, no file. Go back to phase 4. Note the last sentence: rotation does
**not** advance past a failed voice, so the same voice retries next
interval. That is right for the voice and consequential for everything else
— the rotation index is shared, so one stuck voice starves every other voice
indefinitely, and the schedule keeps ticking the whole time. If a configured
voice's name never appears in your log, it is not idle; it is blocked behind
whichever voice does appear. `docs/beats.md`'s "Rotation starvation" section
covers diagnosis.

Once each voice has logged `Beat complete` once, press Ctrl-C. Interrupting
an in-flight later beat is expected, not a failure.

## Read what they wrote

```bash
ls notes journal
cat "$(ls -t logs/analyst/*.txt | head -1)"
```

The transcript's header is written by the worker, not the beat:

```text
# Analyst beat
# Spawned: 2026-08-05T09:19:06-07:00 — worker-written wall-clock receipt at child spawn; the child cannot fabricate this line
# Timestamp: 2026-08-05T09:19:07.312960
# Duration: 0.3s
# Exit code: 0
```

The spawn clock is stamped by the parent process before the child gets
control, so nothing the beat does afterward can change it. A beat's own
account of when it ran is checkable against this line. Its own account of
what it *did*, though, is not checkable here: the transcript holds the
child's final message only, not its tool calls. A term's absence from a
transcript is not evidence the beat never touched it.

Which is why the actual done-condition of this phase is the last one, and it
is yours rather than the software's: **open the note and the journal entry
and read them.** Not to grade the prose. To confirm that something specific
happened — that the analyst measured a real thing and the reflector followed
a real thread, rather than both producing well-formed reports of work that
left no trace. If what you find is fluent and empty, the problem is usually
the standing prompt or the seeded question, and both are yours to rewrite:
`prompts/analyst.md` and `prompts/reflector.md` are starting points, not
fixtures.

---

Next: `docs/onboarding/04-seeded-fault-drill.md` — where you break one on
purpose.
