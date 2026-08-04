-- 003_archive_status_invariant.sql
-- Ported from agi-memory migration 022 (2026-07-09).
--
-- Two columns encode one fact: whether a memory is retired. Retrieval reads
-- only `status` (recall filters m.status = 'active'), while supersede and
-- review paths historically wrote only `archived_at` + `archive_reason`.
-- Nothing enforced agreement, so a memory could be archived on paper and
-- still surface in recall, sometimes ranking above legitimate results. This trigger makes
-- `archived_at` authoritative: setting it retires the memory, clearing it
-- revives. Writers may keep using either column.
--
-- ROLLBACK:
--   DROP TRIGGER IF EXISTS trg_archive_status_sync ON memories;
--   DROP FUNCTION IF EXISTS sync_archive_status();

CREATE OR REPLACE FUNCTION sync_archive_status() RETURNS trigger AS $$
BEGIN
    IF NEW.archived_at IS NOT NULL AND NEW.status = 'active' THEN
        NEW.status := 'archived';
    ELSIF NEW.archived_at IS NULL AND TG_OP = 'UPDATE'
          AND OLD.archived_at IS NOT NULL AND NEW.status = 'archived' THEN
        -- explicit un-archive: clearing archived_at revives the memory
        NEW.status := 'active';
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_archive_status_sync ON memories;
CREATE TRIGGER trg_archive_status_sync
    BEFORE INSERT OR UPDATE OF archived_at, status ON memories
    FOR EACH ROW EXECUTE FUNCTION sync_archive_status();
