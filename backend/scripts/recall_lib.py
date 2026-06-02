#!/usr/bin/env python3
"""
Factored recall library — extracted from .claude/hooks/spontaneous-recall.py.

Lets the spontaneous-recall logic be called against arbitrary text (e.g.,
transcript messages from past conversations), not just stdin in a hook context.

Use case: R-cycle retrieval-testing. Given a sill memory M, we want to ask
"would M get retrieved when this past conversation message was the prompt?"
That requires running the same recall function against past message text.

Two functions exposed:
- extract_keywords(text) → list of priority-ordered keywords
- query_memories(text, keywords=None, limit=5, min_similarity=None) → list of memory dicts

Container note: the database container is named by $SILL_DB_CONTAINER (default "sill_db").
"""
import os
import re
import subprocess
from typing import Optional

CONTAINER = os.environ.get("SILL_DB_CONTAINER", "sill_db")
DB_USER = os.environ.get("SILL_DB_USER", "sill")
DB_NAME = os.environ.get("SILL_DB_NAME", "sill")
DEFAULT_LIMIT = 5
FAST_MIN_SIMILARITY = 0.25
HYBRID_MIN_SIMILARITY = 0.0
OPERATIONAL_TERMS = {
    "cli",
    "codex",
    "config",
    "episodic-memory",
    "hook",
    "hooks",
    "json",
    "mcp",
    "pretooluse",
    "server",
    "spontaneous",
    "sync",
    "toml",
    "userpromptsubmit",
}

# Stop words: copied verbatim from spontaneous-recall.py
_STOP_WORDS = {
    'the', 'a', 'an', 'is', 'are', 'was', 'were', 'be', 'been', 'being',
    'have', 'has', 'had', 'do', 'does', 'did', 'will', 'would', 'could',
    'should', 'may', 'might', 'must', 'shall', 'can', 'need', 'dare',
    'to', 'of', 'in', 'for', 'on', 'with', 'at', 'by', 'from', 'as',
    'into', 'through', 'during', 'before', 'after', 'above', 'below',
    'between', 'under', 'again', 'further', 'then', 'once', 'here',
    'there', 'when', 'where', 'why', 'how', 'all', 'each', 'few',
    'more', 'most', 'other', 'some', 'such', 'no', 'nor', 'not',
    'only', 'own', 'same', 'so', 'than', 'too', 'very', 'just',
    'and', 'but', 'if', 'or', 'because', 'until', 'while', 'about',
    'what', 'which', 'who', 'whom', 'this', 'that', 'these', 'those',
    'am', 'it', 'its', 'they', 'them', 'their', 'we', 'us', 'our',
    'you', 'your', 'he', 'him', 'his', 'she', 'her', 'i', 'me', 'my',
    'also', 'like', 'think', 'know', 'want', 'see', 'look', 'make',
    'get', 'go', 'come', 'take', 'use', 'find', 'give', 'tell', 'say',
    'try', 'let', 'keep', 'put', 'seem', 'help', 'show', 'hear', 'play',
    'run', 'move', 'live', 'believe', 'hold', 'bring', 'happen', 'write',
    'provide', 'sit', 'stand', 'lose', 'pay', 'meet', 'include', 'continue',
    'set', 'learn', 'change', 'lead', 'understand', 'watch', 'follow',
    'really', 'actually', 'something', 'anything', 'everything', 'nothing',
    'already', 'still', 'even', 'much', 'well', 'back', 'now', 'going',
    'right', 'sure', 'yeah', 'okay', 'good', 'great', 'first', 'last',
    'things', 'stuff', 'way', 'kind', 'sort', 'bit', 'lot', 'part',
    'thing', 'done', 'start', 'work', 'working', 'check', 'commit',
    'fix', 'update', 'add', 'remove', 'test', 'debug', 'push', 'pull',
    'read', 'don', 'doesn', 'didn', 'won', 'isn', 'aren', 'wasn',
    'haven', 'hasn', 'hadn', 'wouldn', 'couldn', 'shouldn',
    'break', 'breaks', 'broke', 'restart', 'stop', 'file', 'files',
    'better', 'worse', 'maybe', 'probably', 'definitely', 'basically',
    'whole', 'hog', 'order', 'instead', 'rather', 'simply', 'exactly',
    'making', 'says', 'said', 'called', 'need', 'needs', 'needed',
    'looks', 'looking', 'getting', 'doing', 'having', 'being',
    'using', 'trying', 'running', 'going', 'coming', 'taking',
}


def extract_keywords(text: str, max_keywords: int = 8) -> list[str]:
    """Extract priority-ordered keywords: proper nouns > acronyms > compounds > regular.

    Mirrors spontaneous-recall.py's extract_keywords exactly.
    """
    proper_nouns = set()
    sentences = re.split(r'[.!?]\s+', text)
    for sent in sentences:
        words_in_sent = sent.split()
        for j, w in enumerate(words_in_sent):
            if j > 0 and w[0:1].isupper() and w.lower() not in _STOP_WORDS:
                clean = re.sub(r'[^a-zA-Z0-9-]', '', w)
                if len(clean) > 1:
                    proper_nouns.add(clean)

    compounds = re.findall(r'\b[a-zA-Z]+-[a-zA-Z]+(?:-[a-zA-Z]+)*\b', text)
    acronyms = re.findall(r'\b[A-Z]{2,}\b', text)

    all_words = re.findall(
        r'\b[a-zA-Z][a-zA-Z0-9-]*[a-zA-Z0-9]\b|\b[a-zA-Z]\b',
        text.lower()
    )
    regular = [w for w in all_words if w not in _STOP_WORDS and len(w) > 2]

    result = []
    seen = set()
    for group in [proper_nouns, set(acronyms), set(compounds)]:
        for w in group:
            low = w.lower()
            if low not in seen and low not in _STOP_WORDS:
                seen.add(low)
                result.append(w)

    for w in regular:
        if w not in seen:
            seen.add(w)
            result.append(w)

    return result[:max_keywords]


def should_use_hybrid_recall(text: str, keywords: Optional[list[str]] = None) -> bool:
    """Use hybrid recall for operational/config prompts where exact terms matter."""
    terms = set(re.findall(r"[a-z0-9-]+", text.lower()))
    if keywords:
        terms.update(k.lower() for k in keywords)
    return len(terms & OPERATIONAL_TERMS) >= 2


def build_query_text(text: str, keywords: list[str], use_hybrid: bool) -> str:
    """Build a recall query shaped for the selected retrieval mode."""
    if not use_hybrid:
        if keywords:
            return ' '.join(keywords) + '. ' + text[:500]
        return text[:500]

    focus = []
    seen = set()
    for kw in keywords:
        low = kw.lower()
        if low in OPERATIONAL_TERMS and low not in seen:
            seen.add(low)
            focus.append(kw)

    expansions = []
    if "codex" in seen and ("hook" in seen or "hooks" in seen):
        expansions.extend(["hook", "parity", "setup", "UserPromptSubmit", "Stop", "PreToolUse", "config"])
    if "episodic-memory" in seen:
        expansions.extend(["MCP", "sync"])

    for term in expansions:
        low = term.lower()
        if low not in seen:
            seen.add(low)
            focus.append(term)

    if focus:
        return ' '.join(focus)
    return text[:500]


def query_memories(
    text: str,
    keywords: Optional[list[str]] = None,
    limit: int = DEFAULT_LIMIT,
    min_similarity: Optional[float] = None,
) -> list[dict]:
    """Run recall against `text` and return the top ranked memories.

    If keywords is None, extract them automatically from text.
    Returns list of dicts with: id, type, similarity, importance, created_at, source, content.
    Empty list if query fails or nothing matches.
    """
    if keywords is None:
        keywords = extract_keywords(text)

    use_hybrid = should_use_hybrid_recall(text, keywords)
    query_text = build_query_text(text, keywords, use_hybrid)
    escaped = query_text.replace("'", "''")
    if min_similarity is None:
        min_similarity = HYBRID_MIN_SIMILARITY if use_hybrid else FAST_MIN_SIMILARITY

    if use_hybrid:
        query = f"""
        SELECT memory_id::text, memory_type::text,
               round(score::numeric, 4) as similarity,
               round(importance::numeric, 2) as imp,
               created_at::text,
               source,
               LEFT(content, 500)
        FROM (
            SELECT hr.memory_id, hr.content, hr.memory_type,
                   hr.score, hr.source, m.importance, m.created_at
            FROM hybrid_recall('{escaped}', {limit}, 60) hr
            JOIN memories m ON hr.memory_id = m.id
        ) sub
        WHERE score >= {min_similarity}
        ORDER BY score DESC
        LIMIT {limit};
        """
    else:
        query = f"""
        SELECT memory_id::text, memory_type::text,
               round((score * 0.7 + importance * 0.3)::numeric, 3) as similarity,
               round(importance::numeric, 2) as imp,
               created_at::text,
               source,
               LEFT(content, 500)
        FROM (
            SELECT fr.memory_id, m.content, m.type as memory_type,
                   fr.score, fr.source, m.importance, m.created_at
            FROM fast_recall('{escaped}', {limit * 3}) fr
            JOIN memories m ON fr.memory_id = m.id
            WHERE fr.score >= {min_similarity}
        ) sub
        ORDER BY (score * 0.7 + importance * 0.3) DESC
        LIMIT {limit};
        """

    try:
        result = subprocess.run(
            ['docker', 'exec', CONTAINER, 'psql', '-U', DB_USER, '-d', DB_NAME,
             '-t', '-A', '-F', '|||', '-c', query],
            capture_output=True, text=True, timeout=15
        )
        if result.returncode != 0:
            return []

        memories = []
        for line in result.stdout.strip().split('\n'):
            if line and '|||' in line:
                parts = line.split('|||')
                if len(parts) >= 7:
                    memories.append({
                        'id': parts[0],
                        'type': parts[1],
                        'similarity': float(parts[2]) if parts[2] else 0.0,
                        'importance': float(parts[3]) if parts[3] else 0.0,
                        'created_at': parts[4],
                        'source': parts[5],
                        'content': '|||'.join(parts[6:])[:500],
                    })
        return memories

    except (subprocess.TimeoutExpired, Exception):
        return []


def memory_in_recall(memory_id: str, text: str, limit: int = DEFAULT_LIMIT) -> tuple[bool, Optional[int], list[dict]]:
    """Convenience: check if memory_id appears in top-`limit` recall results for `text`.

    Returns (found, rank_1_indexed, all_results).
    rank_1_indexed is None if not found.
    """
    results = query_memories(text, limit=limit)
    for i, m in enumerate(results, 1):
        if m['id'] == memory_id:
            return True, i, results
    return False, None, results


if __name__ == "__main__":
    # CLI entrypoint for direct testing.
    # Usage:
    #   python recall_lib.py "some text to query"
    #   python recall_lib.py --memory-id <id> "transcript message text"
    import sys

    args = sys.argv[1:]
    if not args:
        print("Usage:")
        print("  python recall_lib.py 'query text'")
        print("  python recall_lib.py --memory-id <id> 'transcript text'")
        sys.exit(1)

    if args[0] == "--memory-id":
        if len(args) < 3:
            print("Need memory id and text")
            sys.exit(1)
        mid = args[1]
        text = args[2]
        found, rank, results = memory_in_recall(mid, text)
        print(f"Memory {mid[:8]} found: {found}, rank: {rank}")
        print(f"Top {len(results)} results:")
        for i, m in enumerate(results, 1):
            print(f"  {i}. {m['id'][:8]} sim={m['similarity']:.3f} imp={m['importance']:.2f} | {m['content'][:100]}")
    else:
        text = ' '.join(args)
        keywords = extract_keywords(text)
        print(f"Keywords ({len(keywords)}): {keywords}")
        results = query_memories(text, keywords=keywords)
        print(f"Top {len(results)} results:")
        for i, m in enumerate(results, 1):
            print(f"  {i}. {m['id'][:8]} sim={m['similarity']:.3f} imp={m['importance']:.2f} | {m['content'][:100]}")
