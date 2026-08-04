-- 006_reuse_event_provenance.sql
-- Ported from agi-memory migration 025 (2026-07-18). Body verbatim.
--
-- `reuse_count` / `last_reused` are compatibility aggregates: they overwrite,
-- so they cannot reconstruct individual reuse observations or detector
-- epochs. From this migration forward each detector observation is recorded
-- append-only — detector version, lexical evidence, and the memory's
-- speech-act axes at detection time. These are telemetry observations, not
-- proof of semantic use. Depends on migration 001 (force/speaker columns).

BEGIN;

CREATE TABLE IF NOT EXISTS memory_reuse_events (
    id               BIGSERIAL PRIMARY KEY,
    memory_id        UUID NOT NULL REFERENCES memories(id) ON DELETE CASCADE,
    detected_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    detector_version TEXT NOT NULL,
    evidence_kind    TEXT NOT NULL
        CHECK (evidence_kind IN ('memory_id', 'body_phrase')),
    evidence_text    TEXT,
    session_id       TEXT,
    memory_force     TEXT,
    memory_speaker   TEXT,
    metadata         JSONB NOT NULL DEFAULT '{}'::jsonb
);

CREATE INDEX IF NOT EXISTS idx_memory_reuse_events_memory_time
    ON memory_reuse_events(memory_id, detected_at DESC);

CREATE INDEX IF NOT EXISTS idx_memory_reuse_events_detector_time
    ON memory_reuse_events(detector_version, detected_at DESC);

COMMENT ON TABLE memory_reuse_events IS
    'Append-only lexical reuse-detector observations. Not a semantic-use or value verdict.';

COMMENT ON COLUMN memory_reuse_events.memory_force IS
    'Snapshot of memories.force at detection time, so usage telemetry can be grouped by speech-act force.';

CREATE OR REPLACE FUNCTION record_memory_reuse(
    p_memory_id UUID,
    p_detector_version TEXT,
    p_evidence_kind TEXT,
    p_evidence_text TEXT DEFAULT NULL,
    p_session_id TEXT DEFAULT NULL,
    p_metadata JSONB DEFAULT '{}'::jsonb
) RETURNS VOID AS $$
DECLARE
    v_force TEXT;
    v_speaker TEXT;
BEGIN
    SELECT force, speaker
      INTO v_force, v_speaker
      FROM memories
     WHERE id = p_memory_id;

    IF NOT FOUND THEN
        RAISE EXCEPTION 'Memory % does not exist', p_memory_id;
    END IF;

    UPDATE memories
       SET reuse_count = COALESCE(reuse_count, 0) + 1,
           last_reused = NOW()
     WHERE id = p_memory_id;

    INSERT INTO memory_reuse_events (
        memory_id, detector_version, evidence_kind, evidence_text,
        session_id, memory_force, memory_speaker, metadata
    ) VALUES (
        p_memory_id, p_detector_version, p_evidence_kind, p_evidence_text,
        p_session_id, v_force, v_speaker, COALESCE(p_metadata, '{}'::jsonb)
    );
END;
$$ LANGUAGE plpgsql;

COMMIT;
