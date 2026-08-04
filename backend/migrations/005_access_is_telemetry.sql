-- 005_access_is_telemetry.sql
-- Ported from agi-memory migration 024 (2026-07-09).
--
-- Access tracking must not mutate memory importance. The legacy
-- `trg_importance_on_access` multiplied importance on every access-count
-- update; reconnecting access telemetry without removing it would activate an
-- unbounded, compounding importance feedback loop. Importance changes stay
-- with the explicit update_memory_importance(uuid, float, text) used by
-- deliberate curation.

BEGIN;

DROP TRIGGER IF EXISTS trg_importance_on_access ON memories;
DROP FUNCTION IF EXISTS public.update_memory_importance();

CREATE OR REPLACE FUNCTION touch_memory_access(p_memory_id UUID)
RETURNS VOID AS $$
BEGIN
    UPDATE memories SET
        access_count = access_count + 1,
        last_accessed = CURRENT_TIMESTAMP
    WHERE id = p_memory_id;
END;
$$ LANGUAGE plpgsql;

COMMENT ON COLUMN memories.access_count IS
    'Telemetry: number of times memory content was exposed to a model-visible context; does not alter importance.';

COMMIT;
