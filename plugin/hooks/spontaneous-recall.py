#!/usr/bin/env python3
"""
Hook: Spontaneous memory recall on UserPromptSubmit.
Queries sill (Postgres + pgvector) for relevant context before the agent responds.

Protocol: Think -> Recall -> Think more -> Remember
This hook handles the "Recall" step by injecting relevant memories.

Queries two sources:
1. sill (hybrid_recall / fast_recall) - semantic memories via vector + full-text search
2. episodic-memory (optional) - conversation history via keyword search
   Enabled only when SILL_EPISODIC_MEMORY_PATH is set and the CLI exists.
"""
import json
import sys
import re
import subprocess
import os
from datetime import datetime
from pathlib import Path

LOG_FILE = Path(os.environ.get("SILL_LOG_DIR", "/tmp")) / "spontaneous-recall.log"
MAX_MEMORIES = 5
MAX_CONVERSATIONS = 2
MIN_QUERY_LENGTH = 20  # Don't query for very short messages
FAST_MIN_SIMILARITY = 0.25
HYBRID_MIN_SIMILARITY = 0.0  # hybrid_recall returns small RRF scores, not cosine similarity
MIN_TOPICAL_KEYWORDS = 3  # Lever 1: skip process-only messages
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

# DB connection (overridable via env vars)
DB_CONTAINER = os.environ.get("SILL_DB_CONTAINER", "sill_db")
DB_USER = os.environ.get("SILL_DB_USER", "agi_user")
DB_NAME = os.environ.get("SILL_DB_NAME", "agi_db")

# Episodic memory archive (optional integration)
EPISODIC_CLI_ENV = os.environ.get("SILL_EPISODIC_MEMORY_PATH", "").strip()
EPISODIC_CLI = Path(EPISODIC_CLI_ENV) if EPISODIC_CLI_ENV else None
SUPERPOWERS_DIR = Path(
    os.environ.get(
        "PERSONAL_SUPERPOWERS_DIR",
        Path.home() / ".config" / "superpowers",
    )
)


def log(message: str):
    """Append timestamped entry to log file."""
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    try:
        LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
        with open(LOG_FILE, "a") as f:
            f.write(f"{timestamp} | {message}\n")
    except Exception:
        pass

def should_use_hybrid_recall(prompt: str, keywords: list[str] | None = None) -> bool:
    """Use hybrid recall for operational/config prompts where exact terms matter."""
    terms = set(re.findall(r"[a-z0-9-]+", prompt.lower()))
    if keywords:
        terms.update(k.lower() for k in keywords)
    return len(terms & OPERATIONAL_TERMS) >= 2

def build_query_text(prompt: str, keywords: list[str], use_hybrid: bool) -> str:
    """Build a recall query shaped for the selected retrieval mode."""
    if not use_hybrid:
        if keywords:
            return ' '.join(keywords) + '. ' + prompt[:500]
        return prompt[:500]

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
    return prompt[:500]

def query_memories_vector(prompt: str, keywords: list[str] | None = None) -> list[dict]:
    """Query Postgres using the recall path best suited to the prompt.

    If keywords are provided, prepends them to the prompt to steer
    recall toward topical content rather than surface tone.
    """
    use_hybrid = should_use_hybrid_recall(prompt, keywords)
    query_text = build_query_text(prompt, keywords or [], use_hybrid)
    log(f"Recall mode: {'hybrid' if use_hybrid else 'fast'}, query: {query_text[:120]}")

    # Escape single quotes for SQL
    escaped = query_text.replace("'", "''")

    # content LAST so delimiter collisions only mangle content, not metadata
    if use_hybrid:
        # hybrid_recall is helpful for operational/config prompts with exact
        # tool names, but its RRF score is not a cosine similarity.
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
            FROM hybrid_recall('{escaped}', {MAX_MEMORIES}, 60) hr
            JOIN memories m ON hr.memory_id = m.id
        ) sub
        WHERE score >= {HYBRID_MIN_SIMILARITY}
        ORDER BY score DESC
        LIMIT {MAX_MEMORIES};
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
            FROM fast_recall('{escaped}', {MAX_MEMORIES * 3}) fr
            JOIN memories m ON fr.memory_id = m.id
            WHERE fr.score >= {FAST_MIN_SIMILARITY}
        ) sub
        ORDER BY (score * 0.7 + importance * 0.3) DESC
        LIMIT {MAX_MEMORIES};
        """

    try:
        result = subprocess.run(
            ['docker', 'exec', DB_CONTAINER, 'psql', '-U', DB_USER, '-d', DB_NAME,
             '-t', '-A', '-F', '|||', '-c', query],
            capture_output=True, text=True, timeout=15  # Longer timeout for embedding
        )

        if result.returncode != 0:
            log(f"Query failed: {result.stderr}")
            return []

        memories = []
        for line in result.stdout.strip().split('\n'):
            if line and '|||' in line:
                parts = line.split('|||')
                if len(parts) >= 7:
                    # content is last — rejoin in case it contained |||
                    memories.append({
                        'id': parts[0],
                        'type': parts[1],
                        'similarity': parts[2],
                        'importance': parts[3],
                        'created_at': parts[4],
                        'source': parts[5],
                        'content': '|||'.join(parts[6:])[:500],
                    })

        return memories

    except subprocess.TimeoutExpired:
        log("Query timed out")
        return []
    except Exception as e:
        log(f"Error querying memories: {e}")
        return []

def extract_keywords(prompt: str) -> list[str]:
    """Extract meaningful keywords from prompt for memory search.

    Prioritizes: proper nouns > technical terms > hyphenated compounds > other.
    This matters because 'Aaron' (5 chars) is more useful for recall than
    'regularities' (13 chars).
    """
    stop_words = {
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

    # Find proper nouns (capitalized words not at sentence start)
    proper_nouns = set()
    sentences = re.split(r'[.!?]\s+', prompt)
    for sent in sentences:
        words_in_sent = sent.split()
        for j, w in enumerate(words_in_sent):
            if j > 0 and w[0:1].isupper() and w.lower() not in stop_words:
                clean = re.sub(r'[^a-zA-Z0-9-]', '', w)
                if len(clean) > 1:
                    proper_nouns.add(clean)

    # Find hyphenated compounds and acronyms (high signal)
    compounds = re.findall(r'\b[a-zA-Z]+-[a-zA-Z]+(?:-[a-zA-Z]+)*\b', prompt)
    acronyms = re.findall(r'\b[A-Z]{2,}\b', prompt)

    # Extract all words for regular keyword filtering
    all_words = re.findall(r'\b[a-zA-Z][a-zA-Z0-9-]*[a-zA-Z0-9]\b|\b[a-zA-Z]\b', prompt.lower())
    regular = [w for w in all_words if w not in stop_words and len(w) > 2]

    # Build priority-ordered list: proper nouns first, then compounds/acronyms, then rest
    result = []
    seen = set()

    for group in [proper_nouns, set(acronyms), set(compounds)]:
        for w in group:
            low = w.lower()
            if low not in seen and low not in stop_words:
                seen.add(low)
                result.append(w)

    for w in regular:
        if w not in seen:
            seen.add(w)
            result.append(w)

    return result[:8]  # More keywords for richer queries

def search_episodic_memory(query: str) -> list[dict]:
    """Search conversation archive using episodic-memory CLI (semantic search with indexing).

    No-op when SILL_EPISODIC_MEMORY_PATH is unset or points to a missing file.
    """
    if EPISODIC_CLI is None or not EPISODIC_CLI.exists():
        return []

    try:
        env = os.environ.copy()
        env["EPISODIC_MEMORY_CONFIG_DIR"] = str(SUPERPOWERS_DIR)
        env["EPISODIC_MEMORY_READONLY"] = "1"

        result = subprocess.run(
            [str(EPISODIC_CLI), 'search', query, '--limit', str(MAX_CONVERSATIONS)],
            capture_output=True,
            text=True,
            timeout=30,  # Longer timeout for semantic search
            env=env,
        )

        if result.returncode != 0:
            log(f"Episodic search failed: {result.stderr}")
            return []

        # Parse CLI output - format is "N. [project, date] - match%\n   snippet\n   Lines..."
        matches = []
        lines = result.stdout.strip().split('\n')
        i = 0
        while i < len(lines) and len(matches) < MAX_CONVERSATIONS:
            line = lines[i]
            # Look for numbered result lines like "1. [-Users-..., 2026-01-08] - -15% match"
            if re.match(r'^\d+\.\s+\[', line):
                # Extract date from the line
                date_match = re.search(r'(\d{4}-\d{2}-\d{2})', line)
                date = date_match.group(1) if date_match else "unknown"

                # Next line should be the snippet (indented)
                if i + 1 < len(lines) and lines[i + 1].startswith('   '):
                    snippet = lines[i + 1].strip().strip('"')
                    if len(snippet) > 30:
                        matches.append({
                            'content': snippet[:300],
                            'date': date
                        })
                    i += 2
                    continue
            i += 1

        # Deduplicate by content similarity
        seen_prefixes = set()
        unique = []
        for m in matches:
            prefix = m['content'][:50]
            if prefix not in seen_prefixes:
                seen_prefixes.add(prefix)
                unique.append(m)

        return unique[:MAX_CONVERSATIONS]

    except Exception as e:
        log(f"Episodic search error: {e}")
        return []

def format_results(memories: list[dict], conversations: list[dict]) -> str:
    """Format all results for injection as context."""
    lines = []

    if memories:
        # Log recalled memory IDs for importance feedback (Lever 3)
        ids = [mem.get('id', '?')[:8] for mem in memories]
        log(f"Recalled IDs: {ids}")

        lines.append("[SPONTANEOUS RECALL] Relevant memories:")
        for i, mem in enumerate(memories, 1):
            content = mem['content'].replace('\n', ' ')
            sim = mem.get('similarity', '?')
            imp = mem.get('importance', '?')
            mid = mem.get('id', '')[:8]
            lines.append(f"  {i}. [{mem['type']}, sim={sim}, imp={imp}, id={mid}] {content}")

    if conversations:
        lines.append("[CONVERSATION HISTORY] Related past discussions:")
        for i, conv in enumerate(conversations, 1):
            content = conv['content'].replace('\n', ' ')
            lines.append(f"  {i}. [past conversation] {content}")

    if lines:
        lines.append("[Consider how these connect to the current conversation.]")
        return '\n'.join(lines)

    return ""

# Main execution
try:
    input_data = json.load(sys.stdin)
except json.JSONDecodeError:
    sys.exit(0)

prompt = input_data.get("prompt", "")
if not prompt or len(prompt) < MIN_QUERY_LENGTH:
    # Too short to meaningfully query
    sys.exit(0)

# Skip for simple commands/greetings (must be the whole message, not just prefix)
simple_patterns = [
    r'^(hi|hello|hey|good morning|good night|thanks|bye|ok|okay)[!.,]?$',
    r'^(yes|no|sure|right|got it)[!.,]?$',
    r'^\s*$',
]
if any(re.match(p, prompt.lower().strip()) for p in simple_patterns):
    sys.exit(0)

# Skip system prompts and very long messages (e.g. from chorus/salon beats)
# These generate embeddings that timeout and return irrelevant results
if prompt.lstrip().startswith("System:") or len(prompt) > 2000:
    sys.exit(0)

# Extract keywords and check topical threshold (Lever 1)
keywords = extract_keywords(prompt)
log(f"Keywords ({len(keywords)}): {keywords}")

if len(keywords) < MIN_TOPICAL_KEYWORDS:
    log(f"Skipping: only {len(keywords)} topical keywords (need {MIN_TOPICAL_KEYWORDS})")
    sys.exit(0)

# Query both sources
log(f"Query for: {prompt[:100]}...")

# 1. sill vector search (Lever 2: keywords steer the embedding)
memories = query_memories_vector(prompt, keywords=keywords)
log(f"Found {len(memories)} memories")

# 2. episodic-memory semantic search (optional)
conversations = search_episodic_memory(prompt)
log(f"Found {len(conversations)} conversations")

# Output if anything found
if memories or conversations:
    context = format_results(memories, conversations)

    # Build detailed summary for TUI
    tui_lines = ["[sill] Recalled:"]

    if memories:
        for mem in memories:
            # First sentence as summary (split on . ! ? or newline)
            content = mem.get('content', '')
            first_sent = re.split(r'[.!?\n]', content, maxsplit=1)[0].strip()
            if len(first_sent) > 100:
                first_sent = first_sent[:97] + "..."

            sim = mem.get('similarity', '?')
            mtype = mem.get('type', '?')[:4]  # sema, epis, proc, stra
            mid = mem.get('id', '')[:8]

            # Age: how old is this memory?
            created = mem.get('created_at', '')
            age = ""
            if created:
                try:
                    from datetime import datetime as dt
                    created_date = dt.fromisoformat(created.replace('+00:00', '+00:00').split('+')[0])
                    days = (dt.now() - created_date).days
                    if days == 0:
                        age = "today"
                    elif days == 1:
                        age = "yesterday"
                    elif days < 30:
                        age = f"{days}d ago"
                    elif days < 365:
                        age = f"{days//30}mo ago"
                    else:
                        age = f"{days//365}y ago"
                except Exception:
                    age = ""

            age_str = f", {age}" if age else ""
            tui_lines.append(f"  {mtype} ({sim}{age_str}) {first_sent}")

    if conversations:
        for conv in conversations:
            snippet = conv.get('content', '')[:80].replace('\n', ' ')
            date = conv.get('date', '?')
            tui_lines.append(f"  conv ({date}) {snippet}")

    system_msg = "\n".join(tui_lines)

    # JSON output: systemMessage for TUI, additionalContext for the agent
    output = {
        "systemMessage": system_msg,
        "hookSpecificOutput": {
            "hookEventName": "UserPromptSubmit",
            "additionalContext": context
        }
    }
    print(json.dumps(output))

sys.exit(0)
