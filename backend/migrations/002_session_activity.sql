-- 002_session_activity.sql
-- Ported from agi-memory migration 021 (2026-07-09).
--
-- Time-awareness for the UserPromptSubmit hook (spontaneous-recall).
-- One row per PROJECT (a "thing we work on", keyed by cwd). Each exchange
-- upserts it. The hook reads the PRIOR row to compute two deltas, then
-- overwrites:
--   * within-session gap  — now - last_prompt_at, when session_id is unchanged.
--   * across-session gap  — now - last_prompt_at, when session_id changed (a
--     new session = a "waking"): how long since we last worked on this.
-- Deliberately NOT a per-session roster: only current-session vs
-- any-older-session matters. prompt_count is the endogenous sequence counter.
--
-- ROLLBACK: DROP TABLE IF EXISTS session_activity;

BEGIN;

CREATE TABLE IF NOT EXISTS session_activity (
    project             text PRIMARY KEY,               -- cwd of the working session
    session_id          text NOT NULL,                  -- the session that last touched this project
    last_prompt_at      timestamptz NOT NULL DEFAULT now(),   -- most recent exchange (any session)
    session_started_at  timestamptz NOT NULL DEFAULT now(),   -- first exchange of the current session
    prompt_count        integer NOT NULL DEFAULT 1       -- # exchanges in the current session
);

COMMENT ON TABLE session_activity IS
    'Per-project last-activity clock for the spontaneous-recall time header. Upserted every exchange; the hook diffs the prior row for within-/across-session gaps. Migration 002.';

COMMIT;
