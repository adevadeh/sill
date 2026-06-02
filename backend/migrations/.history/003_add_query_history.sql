-- Add query history tracking to prevent repetitive exploration
-- This extracts recent unique queries from heartbeat_log and adds them to context

CREATE OR REPLACE FUNCTION get_recent_queries(p_limit INT DEFAULT 20)
RETURNS jsonb
LANGUAGE plpgsql
AS $function$
DECLARE
    result jsonb;
BEGIN
    SELECT COALESCE(
        jsonb_agg(DISTINCT query_text ORDER BY query_text),
        '[]'::jsonb
    ) INTO result
    FROM (
        SELECT
            action_elem->'params'->>'query' as query_text
        FROM heartbeat_log,
             jsonb_array_elements(COALESCE(actions_taken, '[]'::jsonb)) as action_elem
        WHERE actions_taken IS NOT NULL
          AND action_elem->>'action' IN ('recall', 'inquire_shallow', 'inquire_deep', 'reflect')
          AND action_elem->'params'->>'query' IS NOT NULL
          AND LENGTH(action_elem->'params'->>'query') > 10
        ORDER BY heartbeat_number DESC
        LIMIT p_limit * 5  -- Get more rows to find unique ones
    ) sub
    LIMIT p_limit;

    RETURN result;
END;
$function$;

-- Update gather_turn_context to include recent queries
CREATE OR REPLACE FUNCTION public.gather_turn_context()
 RETURNS jsonb
 LANGUAGE plpgsql
AS $function$
DECLARE
    state_record RECORD;
    action_costs JSONB;
BEGIN
    SELECT * INTO state_record FROM heartbeat_state WHERE id = 1;

    SELECT jsonb_object_agg(
        regexp_replace(key, '^cost_', ''),
        value
    ) INTO action_costs
    FROM heartbeat_config
    WHERE key LIKE 'cost_%';

    RETURN jsonb_build_object(
        'agent', get_agent_profile_context(),
        'environment', get_environment_snapshot(),
        'goals', get_goals_snapshot(),
        'recent_memories', get_recent_context(5),
        'recent_queries', get_recent_queries(15),
        'identity', get_identity_context(),
        'worldview', get_worldview_context(),
        'self_model', get_self_model_context(25),
        'narrative', get_narrative_context(),
        'energy', jsonb_build_object(
            'current', state_record.current_energy,
            'max', (SELECT value FROM heartbeat_config WHERE key = 'max_energy')
        ),
        'action_costs', action_costs,
        'heartbeat_number', state_record.heartbeat_count,
        'urgent_drives', (
            SELECT COALESCE(
                jsonb_agg(
                    jsonb_build_object(
                        'name', name,
                        'level', current_level,
                        'urgency_ratio', current_level / NULLIF(urgency_threshold, 0)
                    )
                    ORDER BY current_level DESC
                ),
                '[]'::jsonb
            )
            FROM drives
            WHERE current_level >= urgency_threshold * 0.8
        ),
        'emotional_state', get_current_affective_state()
    );
END;
$function$;
