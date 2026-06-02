-- Fix update_memory_importance callable function.
--
-- The callable `update_memory_importance(uuid, float, text)` in migration 010
-- references `importance_updates` without a schema qualifier. On live DBs where
-- Apache AGE's search_path placed the table in `ag_catalog` at migration time,
-- the function fails at runtime with "relation does not exist", which breaks
-- the MCP sill `update_importance` tool.
--
-- This migration recreates the function with the schema explicitly qualified.
-- It's idempotent (CREATE OR REPLACE) and safe to apply even on DBs where the
-- table is in `public`, because `ag_catalog.importance_updates` is where the
-- live data actually lives and future migrations should treat ag_catalog as
-- the canonical location for this table.
--
-- The trigger-function `update_memory_importance()` (no args) is a distinct
-- overload that fires on access_count changes; it is unaffected by this fix.
--
-- Found 2026-04-20 when main-conversation Sili attempted to down-weight
-- corpus memories after cross-model critique (see
-- docs/chorus-critique-2026-04-20.md). MCP errored with
-- "function update_memory_importance(uuid, double precision, text) does not exist".

CREATE OR REPLACE FUNCTION public.update_memory_importance(
  p_memory_id UUID,
  p_new_importance DOUBLE PRECISION,
  p_reason TEXT DEFAULT ''
) RETURNS BOOLEAN AS $$
DECLARE
  v_old_importance DOUBLE PRECISION;
BEGIN
  SELECT importance INTO v_old_importance
  FROM memories WHERE id = p_memory_id;

  IF v_old_importance IS NULL THEN
    RETURN FALSE;
  END IF;

  UPDATE memories SET importance = p_new_importance WHERE id = p_memory_id;

  -- Qualify schema explicitly: table lives in ag_catalog on live DBs due to
  -- AGE search_path at the time migration 010 ran.
  INSERT INTO ag_catalog.importance_updates (memory_id, old_importance, new_importance, reason)
  VALUES (p_memory_id, v_old_importance, p_new_importance, p_reason);

  RETURN TRUE;
END;
$$ LANGUAGE plpgsql;
