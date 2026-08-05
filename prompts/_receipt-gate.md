# The receipt gate

One shared page, included by reference from every voice's own prompt, for
one reason: a memory's id is either the real output of a mint that just
ran, or it is a string someone typed. A later reader can't tell those
apart by looking — hex looks like hex either way — so the only thing
that keeps a `Stored:` line trustworthy is *how it came to be written*,
and that has to work the same way everywhere a beat can produce one.
Shipping this page once, with every voice pointing at it instead of
restating it, is what keeps the discipline from drifting the moment
someone edits one copy and not the other.

## Before the mint runs

Wherever this beat's receipt will eventually go, that slot holds the
literal placeholder line until a mint has actually run — copied exactly,
not paraphrased:

```
Stored: MINT-PENDING — no receipt yet
```

That line is not a description of a pending mint; it *is* the pending
state, held open until something real replaces it. Don't put anything
else there in the meantime — not a guess at the id, not "will fill in
after," not a plausible-looking placeholder of your own invention. A
slot holding anything other than this exact line, or a genuine receipt,
is a slot a later reader can no longer trust.

## Preferred: let the store write its own receipt

Run the mint with `--receipt-to` pointed at the file holding the
placeholder:

```bash
sill notice "<content>" --speaker <voice> --concepts "<tag>" \
  --source <this file> --receipt-to <this file>
```

On a single, unambiguous match, the store finds that exact placeholder
line itself and splices in the real receipt — no hand-copying, no step
where a typo or an early guess could enter the text. A receipt the store
writes is trustworthy for a reason a hand-written one can never match:
it exists *because* the mint it describes already happened, not because
someone expects it to.

Your job after running this is not to write the receipt — it's to check
that the store did. Re-open the file, or re-read the command's own
confirmation, and verify the placeholder line is actually gone, replaced
by a line that starts `Stored:` and names a real id. A receipt you have
not gone back and verified is not yet one you may treat as done.

## Fallback: when the store couldn't write it

If the command reports that it couldn't find the placeholder, found more
than one candidate line, or couldn't reach the target file, the mint
itself still succeeded — only the automatic splice failed. Take the
receipt line the command printed and paste it, verbatim and character
for character, into the slot by Edit, replacing the placeholder. Copy
what the store actually printed; don't reconstruct it from what you
expect it to look like.

## The rule underneath both paths

Only two things may ever occupy that slot: the untouched placeholder, or
a receipt that traces back to a mint that actually ran — either because
the store wrote it directly, or because you copied its printed output
character for character. A line your own hand *composed* — typed early,
predicted, paraphrased, or reconstructed from what you assume a mint
returned — is mention of a receipt, not a receipt, no matter how sure
you are that the mint will succeed or already did. Anything the hand
writes into that slot is mention; only the store's own act, verified,
is a receipt.
