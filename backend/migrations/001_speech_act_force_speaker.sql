-- 001_speech_act_force_speaker.sql
-- Ported from agi-memory migration 020 (2026-07-09); backfills adapted for
-- extraction corpora (the original aborted when its house-specific backfill
-- matched nothing, which is the normal case on a fresh-extraction corpus).
--
-- Speech-act memory model: a stored memory records an ACT with a force, not
-- only a fact. Two nullable columns on `memories`:
--   force   — illocutionary force (Searle): assertive / directive /
--             commissive / expressive / declaration. NULL = untagged
--             (semantically the legacy assertive default). Only assertive
--             success = truth; the others succeed by being complied-with /
--             kept / sincere / felicitous.
--   speaker — whose act this records (the perspective axis). NULL = no
--             particular mouth.
--
-- ROLLBACK (fully reversible, no data loss — these are new columns):
--   ALTER TABLE memories DROP COLUMN IF EXISTS force;
--   ALTER TABLE memories DROP COLUMN IF EXISTS speaker;

BEGIN;

ALTER TABLE memories ADD COLUMN IF NOT EXISTS force text
    CONSTRAINT memories_force_chk
    CHECK (force IS NULL OR force IN
        ('assertive','directive','commissive','expressive','declaration'));

ALTER TABLE memories ADD COLUMN IF NOT EXISTS speaker text;

COMMENT ON COLUMN memories.force IS
    'Illocutionary force of the recorded act (Searle): assertive/directive/commissive/expressive/declaration. NULL = untagged, defaults to assertive. Only assertive success = truth; others = complied/kept/sincere/felicitous.';
COMMENT ON COLUMN memories.speaker IS
    'Whose act this records (perspective axis) — a person''s name, the instance''s christened name, or a source author. NULL = consensus/no-particular-mouth.';

-- Harness-generic backfill: rows written by the response-patterns hook are,
-- definitionally, the instance's own assertive claims. Tag force only — no
-- christened speaker name exists at migration time.
UPDATE memories SET force = 'assertive'
    WHERE force IS NULL AND content LIKE '[AUTO-STORED BY HOOK%';

DO $$
DECLARE n int;
BEGIN
    SELECT COUNT(*) INTO n FROM memories WHERE force IS NOT NULL;
    RAISE NOTICE 'force tagged on % rows (0 is normal for a clean corpus)', n;
END $$;

COMMIT;
