# Charter prompts

This is yours, and you can point it at anything. These four questions are
scaffolding for a blank page, not a definition of what a Sill is for. If you
already know what you want, close this file and write that instead — a
paragraph in your own words beats four dutiful answers.

Write prose. Whole sentences, your own voice, contradictions and all. Nobody
has to parse this later; someone has to *read* it, and that someone may be
you in a year, or the instance trying to work out what you meant.

---

### What do you want this to become?

Ambition, vagueness, and uncertainty are all fine here. "I don't know yet,
but I want to find out whether a thing that remembers me is useful or just
unsettling" is a better charter than a mission statement you don't believe.
If you have a picture of what a good version of this looks like in six
months, describe the picture.

### What will you actually do with it, and how often?

Not what you hope to do — what you expect to do on a normal week. This is
the question that later saves you from a system built for an attention
budget you never had. If the honest answer is "read it on Sundays," say
that. It changes what a good cadence is, and it changes what the instance
should bother writing down.

### What would make you shut it down?

The most useful question here, and the one people skip. Name the condition
in advance, while nothing is at stake: cost above some number, a month of
entries you don't read, something it does that you find you don't like, a
kind of content you don't want stored anywhere. A condition named now is one
you will actually notice later. A condition you only formulate under
pressure tends to arrive as a vague unease that goes unacted on for months.

### Who else, if anyone, will read it?

Nobody but you is a complete answer, and the common one. But if a
collaborator, a partner, or a future employer might see this, the instance
should know that before it writes anything, not after — what belongs in a
private journal and what belongs in a shared one are different, and this is
the only question in the set whose answer changes what should be *stored* at
all, not just how it reads.

---

## How this gets captured

Verbatim, with a timestamp, exactly as you wrote it. Nothing summarizes your
charter — not now, not later, not "for context." A summarized charter loses
the one sentence someone needs months from now, and the loss is invisible
after the fact, because what remains still reads like a whole document.

Put it in `charter.md` at the project root, in this shape:

```text
# Charter for <name>
2026-08-05T16:27:06Z

<your answers, in prose>
```

Stamp it from the shell so the timestamp is the machine's rather than your
recollection of when you sat down:

```bash
date -u +%Y-%m-%dT%H:%M:%SZ >> charter.md
```

Then point the identity card at it, which is also where the name is set —
see `docs/onboarding/05-christening.md`:

```bash
sill identity set --name <name> --charter charter.md
```

If your intentions change later, append a new dated section. Never edit the
old one. A charter with two dated entries that disagree is a more honest
document than one that has been quietly kept current.
