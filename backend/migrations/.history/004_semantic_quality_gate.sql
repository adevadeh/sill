-- Add quality gate for semantic memories to reject low-value content
-- Rejects: very short content, "Unknown", "insufficient information", etc.

CREATE OR REPLACE FUNCTION ag_catalog.create_memory(p_type memory_type, p_content text, p_importance double precision DEFAULT 0.5, p_source_attribution jsonb DEFAULT NULL::jsonb, p_trust_level double precision DEFAULT NULL::double precision)
 RETURNS uuid
 LANGUAGE plpgsql
AS $function$
DECLARE
    new_memory_id UUID;
    embedding_vec vector;
    normalized_source JSONB;
    effective_trust FLOAT;
    content_trimmed TEXT;
BEGIN
    content_trimmed := trim(p_content);

    -- Validate content early with context about memory type
    IF p_content IS NULL OR content_trimmed = '' THEN
        RAISE EXCEPTION 'create_memory(%) called with NULL or empty content', p_type;
    END IF;

    -- Quality gate for semantic memories
    IF p_type = 'semantic' THEN
        -- Reject very short content (less than 30 chars)
        IF LENGTH(content_trimmed) < 30 THEN
            RAISE EXCEPTION 'create_memory(semantic) rejected: content too short (% chars)', LENGTH(content_trimmed);
        END IF;

        -- Reject junk patterns
        IF content_trimmed ~* '^(unknown|none|null|\[\]|unanswerable|inquire shallow|insufficient information|no information|not available|not applicable)' THEN
            RAISE EXCEPTION 'create_memory(semantic) rejected: junk pattern detected';
        END IF;
    END IF;

    normalized_source := normalize_source_reference(p_source_attribution);
    IF normalized_source = '{}'::jsonb THEN
        normalized_source := jsonb_build_object(
            'kind',
            CASE
                WHEN p_type = 'semantic' THEN 'unattributed'
                ELSE 'internal'
            END,
            'observed_at', CURRENT_TIMESTAMP
        );
    END IF;

    effective_trust := p_trust_level;
    IF effective_trust IS NULL THEN
        effective_trust := CASE
            WHEN p_type = 'episodic' THEN 0.95
            WHEN p_type = 'semantic' THEN 0.20
            WHEN p_type = 'procedural' THEN 0.70
            WHEN p_type = 'strategic' THEN 0.70
            ELSE 0.50
        END;
    END IF;
    effective_trust := LEAST(1.0, GREATEST(0.0, effective_trust));

    -- Generate embedding
    embedding_vec := get_embedding(p_content);

    INSERT INTO memories (type, content, embedding, importance, source_attribution, trust_level, trust_updated_at)
    VALUES (p_type, p_content, embedding_vec, p_importance, normalized_source, effective_trust, CURRENT_TIMESTAMP)
    RETURNING id INTO new_memory_id;

    RETURN new_memory_id;
END;
$function$;

-- Clean up existing junk semantic memories
DELETE FROM memories
WHERE type = 'semantic'
AND (
    LENGTH(trim(content)) < 30
    OR content ~* '^(unknown|none|null|\[\]|unanswerable|inquire shallow|insufficient information|no information|not available|not applicable)'
);
