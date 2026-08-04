---
name: authorship-attribution
enabled: true
# Ported from agi-memory .claude/response-patterns/authorship-attribution.md (2026-08-04).
patterns:
  - "\bsili (wrote|said|claimed|concluded|authored|stated|observed)\b"
  - "\bwilliam (wrote|said|claimed|concluded|authored|stated|mentioned|noted|asked)\b"
  - "\byou wrote\b"
  - "\byou said\b"
  - "\byour (quote|essay|story|book|draft|piece)\b"
  - "\bi concluded\b"
  - "\bi wrote\b"
  - "\bquoted william\b"
---

Authorship/attribution claim in outgoing prose: "{matched}"

Claims of authorship or quotation are checkable against the store and the
filesystem — verify before asserting who wrote what. This mirrors a
PreToolUse attribution check (an F2-style pattern set), but at Stop time on
the response text itself: plain prose makes no memory-write tool call, so a
misattribution that only ever appears in prose has no other hook surface to
catch it on.

Logging-only and high-recall by design: conversational "you said" / "so-and-
so said" are usually legitimate and will fire too. The diagnostic core is
the authorship verbs (wrote / authored / stated) and possessive-on-artifact
("your essay/story/quote"); the wrote/said ratio in the logs is itself the
signal worth watching.

**Corrective action:** If the prose pins an artifact or a quote to the wrong
author, correct it this turn — check the store or the filesystem before
letting the claim stand.
