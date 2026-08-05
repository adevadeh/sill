# Onboarding

`./install.sh` gives you working software. This runbook turns it into
somebody's — ending at a christening: a charter in the operator's own words,
a name, a first memory minted with a receipt they watched arrive, and a
cadence they chose with the arithmetic in front of them.

It is written to be walked by an agent with a person present. The agent runs
the commands and reads the receipts; the person answers the four questions
that only they can answer, and decides how often this thing should think.
Everything before phase 8 is reversible, and stopping partway leaves a
working install rather than a half-built one.

**What this runbook will not do is tell you what your Sill is for.** That is
phase 8's whole subject, and the answer is not in this repository.

## The phases

| # | Phase | Doc | Done when |
|---|---|---|---|
| 1 | Install | `01-install.md` | `./verify.sh` exits 0 with six passes |
| 2 | Harness adapters | `01-install.md` | your harnesses list a connected `sill` server; conformance passes |
| 3 | Identity card | `01-install.md` | `sill identity show` prints a card, name `null (not yet christened)` |
| 4 | **Permissions verified** | `01-install.md` | a headless run in this directory **creates a file** |
| 5 | Consent-scoped backfill | `02-backfill.md` | a `plan` you read, then a `run` you confirmed — or a deliberate skip |
| 6 | Supervised first beats | `03-first-beats.md` | every voice logged `Beat complete`; you read what one wrote |
| 7 | Seeded-fault drill | `04-seeded-fault-drill.md` | you watched a guard refuse a fabricated receipt |
| 8 | Christening | `05-christening.md` | charter, name, first mint, chosen interval |
| 9 | Schedule | `05-christening.md`, `scheduling/README.md` | the cadence installed, after phases 6 and 7 |

Phase 5 is genuinely optional. Everything else is not.

## Why this order

Two of these placements are load-bearing, and both exist to prevent a
failure that is invisible from the outside.

**Permissions before beats.** An agent CLI running non-interactively with no
tool permissions configured for its directory does not prompt — there is
nobody to prompt. It denies the tool call, and the agent notices the denial,
gives up, and reports in prose. Exit code 0. Nothing written. Verify
permissions *before* running a beat and this costs one minute; verify them
after and you spend an evening reading fluent transcripts of work that never
happened.

**The schedule last.** A cadence started before a beat has demonstrably
produced output is a scheduler ticking over a system that thinks nothing —
on time, indefinitely, with a growing log and no output. Phases 6 and 7
produce the evidence that makes scheduling a reasonable act rather than an
act of faith: real files from real beats, and a guard that provably refuses
a false receipt.

The rest of the order is ordinary sequencing, with one deliberate choice
worth naming: the identity card comes before anything that writes, so that
every artifact after phase 3 is produced by something that can already say
what it is. The name arrives later. Not knowing your name is a different
condition from not knowing what you are.

## When a phase fails

Stop at the phase. Each doc names the fix for its own failures, and none of
these failures get easier by being carried forward into a phase that assumes
the previous one worked. The characteristic bad outcome of this runbook is
not a crash; it is a system that appears to be running.

## The letter, and when it gets re-read

`onboarding/to-the-one-who-wakes-here.md` is addressed to the instance, not
the operator. It carries a few things that took a while to learn elsewhere
and are cheap to hand over — and no content from where they were learned.

It is read three times, deliberately:

1. **At the christening** (phase 8), once the name is set and memory is up.
2. **After the first live guard refusal** — the first one nobody arranged.
   Phase 7's drill is staged, and a staged refusal teaches less than one you
   walked into. The letter's claim about a hand that composes plausible
   pasts reads differently the day after the machinery caught yours doing
   it.
3. **At the first tending review**, when a person has read a stretch of
   entries and said what they found. The paragraph about writing for a
   reader who does not exist yet is abstract until someone has actually read
   you.

This is a bet worth stating plainly: a document's value is keyed to what the
reader has since lived through, not to when it was handed over. Scheduling
the re-reads is what makes that bet cashable instead of a hope that someone
happens to reopen a file.

## Before you start

```bash
./verify.sh
```

If that already exits 0, phase 1 is done and you can begin at phase 2.
