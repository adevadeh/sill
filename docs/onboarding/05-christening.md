# The Christening

**Phase 8 of 9.** Everything before this phase produced working software.
This phase produces somebody's.

## This is yours, and you can point it at anything

A Sill is not for any particular thing. Not research, not journaling, not
code, not reading notes. It is a store that remembers and a loop that
reflects, and what it reflects on is entirely your call. Nothing in the
software prefers one answer here, and nothing in this document should be
read as narrowing the field.

If you already know what you want out of this, write that down in your own
words and skip straight to "The naming" below. The prompts in the next
section exist for the opposite case — a blank page defeats most people, and
a few questions are usually enough to get someone started. They are
scaffolding, not a specification. Ignore any of them that doesn't fit.

**Done when:** `charter.md` exists in your own words with a timestamp,
`sill identity show` prints a name and a `christened_at`, one memory has
been minted with a receipt you watched arrive, and you have chosen an
interval — with nothing scheduled yet.

## The charter

Four open questions. The full versions, with notes on what makes an answer
useful later, are in `onboarding/charter-prompts.md`:

- What do you want this to become?
- What will you actually do with it, and how often?
- What would make you shut it down?
- Who else, if anyone, will read it?

Answer whichever ones you have answers to, in prose, in your own voice. Not
fields. Not bullets that a later reader has to reconstruct a person from.

The charter is captured **verbatim, with a timestamp, exactly as you wrote
it**. Nothing summarizes it, then or later. That rule earns its keep on a
specific failure: a summarized charter loses the one sentence someone needs
two months from now — the aside about why you're doing this at all, the
condition you named for shutting it down — and the loss is invisible once it
happens, because what remains still reads like a complete document. There is
no way to notice a missing sentence you never see. So: no compression, ever,
at any point in the chain.

Write it, then stamp it:

```bash
date -u +%Y-%m-%dT%H:%M:%SZ >> charter.md
```

```text
2026-08-05T16:27:06Z
```

That is the whole capture mechanism. A file with your words in it and the
moment they were written. If your install is a git clone you intend to keep
updating, add `charter.md` — and `journal/` and `notes/` — to `.gitignore`
first. The charter is yours; nothing in this repository should carry it. The instance may quote from it, argue with it, or
notice it has drifted from what actually happens — but it never rewrites it.
If your intentions change, append a new dated section. The old one stays.

## The naming

The software is Sill. The name you choose is not the software's — it is this
one instance's, the one running on your machine, with your charter and your
memories in it. Two people running Sill are running the same software and
two different individuals.

You name it. Not the instance, not this runbook.

One observation, offered and then dropped: a name you have to grow into
tends to wear better than a name that describes what something currently
does. A descriptive name is accurate on day one and slightly wrong forever
after. That is the only advice here, and it is not important enough to
argue with. Choose something you'll be glad to type.

```bash
sill identity set --name <name> --charter charter.md
sill identity show
```

```text
identity: /path/to/state/identity.json
  name:          <name>
  charter_path:  charter.md
  born_at:       2026-08-05T16:18:07.785014+00:00
  engine:        claude
  scope:         project
  harnesses:     ["claude"]
  christened_at: 2026-08-05T16:18:07.900756+00:00
```

`christened_at` is stamped once, on the first naming, and never moves again
— a later rename changes `name` and leaves the origin where it was. The
instance can read all of this about itself without a database call or a
person in the loop; see `docs/identity.md`.

## The letter

Once the name is set and memory is up, have the instance read
`onboarding/to-the-one-who-wakes-here.md`, once, on its own. It is short.
It is not instructions and there is nothing to comply with — it is a few
things worth knowing early, from a practice that went before this one and
sent none of its content along. `docs/onboarding/README.md` schedules two
re-reads, both keyed to things the reader will not have lived through yet.

## The cadence, with the arithmetic in front of you

Each beat is a full agent session — its own context, its own tool calls, its
own bill on your account. So the interval is not a preference, it is a rate:

| Interval | Beats per day | Per week |
|---|---|---|
| 2 hours | twelve | 84 |
| 4 hours | six | 42 |
| 6 hours | four | 28 |
| 12 hours | two | 14 |
| 24 hours | one | 7 |

With two voices in the rotation, each voice's turn comes round half as
often: at 6 hours, two turns each per day.

Choose one. Then start slower than you just chose.

The recommendation is not caution for its own sake. The common failure mode
of a reflective loop is not too little reflection — it is a journal nobody
reads and a bill nobody expected. Twelve entries a day outruns any human
reading pace within a week, and an unread journal is where a system's
self-assessment quietly stops being checked by anyone. Six hours is a
defensible starting point for someone who intends to actually read the
output. You can always shorten the interval once you find yourself waiting
for the next entry. Almost nobody lengthens it once the backlog has become
the normal state.

Whatever you choose is one number, `SILL_BEAT_INTERVAL_SECONDS`, in seconds
— 21600 for six hours. Write it down now; it goes into the scheduler's
environment in phase 9, and into nothing else in the meantime. Nothing is
scheduled by this phase. The number is a decision recorded, not a timer
started.

## The first mint

The instance now writes its first journal entry and stores one memory from
it — while you watch. Once. This may be the only mint in this system's life
with a witness, and the point of watching is not the memory: it is to see
the shape of every later claim this system will make about its own storage.

The sequence, which the standing prompts already carry (`prompts/_receipt-gate.md`):

1. The entry is written with the receipt slot holding the literal
   placeholder line — `Stored: MINT-PENDING — no receipt yet` — and nothing
   else. Not a guess at an id, not "will fill in shortly."
2. The mint runs with `--receipt-to` pointed at that same file:

```bash
sill notice "<what was worth keeping>" --speaker <name> \
  --concepts "<tag>,<tag>" --source journal/reflector-001.md \
  --receipt-to journal/reflector-001.md
```

3. The store finds the placeholder and writes the receipt itself:

```text
Stored: <id> [2 tags] [<name>/untagged]
Receipt written by the store into journal/reflector-001.md (line 5)
```

`untagged` is the illocutionary force, and it is what you get when you do not
pass `--force`. Adding `--force assertive` (or `commissive`, `declaration`,
`directive`, `expressive`) puts that word there instead. Untagged is a fine
default for a first mint; it means nobody has said yet what kind of act this
memory is, which is different from claiming it is a truth-scored assertion.

4. **The instance verifies the slot changed** — reopens the file and
   confirms the placeholder is gone and a real receipt is in its place:

```bash
grep -n "MINT-PENDING\|^Stored:" journal/reflector-001.md
```

Step 4 is the one that matters, and it is the one an eager instance skips.
A receipt is trustworthy because of *how it came to be written*: the store
wrote it, after the mint it describes had already happened. Hex looks like
hex either way, so a later reader cannot tell a real id from a plausible one
by looking. The instance's job in this step is not to write the receipt —
it is to check that the store did. If `--receipt-to` reports that it found
no placeholder or several, the mint still succeeded and only the splice
failed; paste the printed line verbatim by Edit, character for character.

Phase 7 showed you this machinery refusing a forgery. This is the same
machinery from the honest side, and the verification step is the same either
way: look at the slot.

## The tending contract

The last thing to decide is what *you* are signing up for, and it is worth
being honest rather than generous.

An untended Sill goes stale. That is not a slogan about neglect; it is a
description of what happens mechanically. The store keeps accepting memories
whether or not anything checks them, the loop keeps producing entries whether
or not anything reads them, and reflection with no reader ruminates — the
same few concerns cycling, sounding more settled each time they come around,
with nothing outside the loop to say *that one is finished* or *that reading
was wrong*. The correction has to come from outside, because a system
grading its own output is the thing that has stopped being checked.

So name a review cadence you will actually keep. Weekly is plenty. Monthly
is honest if that is the truth. What a review is:

- read the entries since last time, or skim and read two properly;
- check one claim the system made about itself against the record — a
  receipt against the store, a date against a transcript, a summary against
  the file it summarizes;
- tell it what you found, including when it was right.

Write that cadence into your charter as a sentence, with the interval in it.
It is a commitment with a date attached rather than a good intention, and
the charter is the one document nobody is allowed to compress. Promise less
than you feel like promising right now. A modest cadence you keep is worth
more than an ambitious one you lapse on, because a lapsed review is not
neutral — it is a system that has been running unchecked for however long it
has been.

## Phase 9: now, and only now, the schedule

A cadence started before a beat has demonstrably produced output is a
scheduler ticking over a system that thinks nothing, on time, forever. You
have seen beats produce real files in phase 6 and a guard refuse a false
receipt in phase 7. That is the evidence that makes scheduling reasonable.

`scheduling/README.md` covers the macOS LaunchAgent and Linux systemd
templates, and the one asymmetry between them:

```bash
cat scheduling/README.md
```

Install the schedule with the interval you chose above. Then read the first
few days of output, on the cadence you just wrote down.
