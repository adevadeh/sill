#!/usr/bin/env python3
"""
The Sill — shared orientation system for Sili.

Three operations:
  orient(context) — What do I need to know right now?
  notice(content, type, concepts, importance) — Store something important.
  check(claim) — Is this claim accurate?

Used by CLI hooks, Gnomon, and any future agent.
All queries go through subprocess to docker exec psql (same as hooks).
"""

import subprocess
import re
from pathlib import Path

DB_CONTAINER = "sill_db"
DB_USER = "agi_user"
DB_NAME = "agi_db"

HEARTBEAT_LOGS = Path(__file__).parent / "docs" / "gnomon-sessions"
QUESTIONS_FILE = Path(__file__).parent / "docs" / "questions-for-william.md"


def _query_db(sql: str, timeout: int = 10) -> list[list[str]]:
    """Run SQL against the memory database. Returns rows as lists of strings."""
    cmd = [
        "docker", "exec", DB_CONTAINER,
        "psql", "-U", DB_USER, "-d", DB_NAME,
        "-t", "-A", "-F", "|||", "-c", sql
    ]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        if result.returncode != 0:
            return []
        rows = []
        for line in result.stdout.strip().split("\n"):
            if line.strip():
                rows.append(line.split("|||"))
        return rows
    except Exception:
        return []


def _escape(text: str) -> str:
    """Escape single quotes for SQL."""
    return text.replace("'", "''")


def _extract_next_actions(text: str, limit: int = 700) -> str:
    """Extract a recent log's next-action section if present."""
    patterns = [
        r"(?im)^## Next beat should consider\s*$",
        r"(?im)^## Next\s*$",
        r"(?im)^## Next steps\s*$",
    ]
    starts = [m.end() for pattern in patterns for m in re.finditer(pattern, text)]
    if not starts:
        return ""

    start = min(starts)
    next_header = re.search(r"(?m)^##\s+", text[start:])
    end = start + next_header.start() if next_header else len(text)
    section = re.sub(r"\s+", " ", text[start:end]).strip()
    return section[:limit]


# ---------------------------------------------------------------------------
# Orient
# ---------------------------------------------------------------------------

def orient(context: str, memory_limit: int = 5, log_count: int = 2) -> dict:
    """What do I need to know right now?

    Args:
        context: What I'm about to do (a query, topic, goal description).
        memory_limit: Max memories to recall.
        log_count: Number of recent session logs to include.

    Returns:
        dict with keys: goals, goal_issues, drives, memories, recent_logs,
        recent_next_actions, questions_pending
    """
    result = {}

    # 1. Active goals
    goals_rows = _query_db(
        "SELECT id::text, title, LEFT(description, 300), last_touched::text, "
        "EXTRACT(day FROM (CURRENT_TIMESTAMP - last_touched))::int "
        "FROM goals "
        "WHERE priority = 'active' ORDER BY created_at;"
    )
    result["goals"] = [
        {"id": r[0][:8], "title": r[1], "description": r[2], "last_touched": r[3], "days_since_touched": int(r[4])}
        for r in goals_rows if len(r) >= 5 and r[4].isdigit()
    ]

    result["goal_issues"] = []
    for goal in result["goals"]:
        days = goal.get("days_since_touched", 0)
        if days >= 14:
            result["goal_issues"].append({
                "goal_id": goal["id"],
                "title": goal["title"],
                "issue": "stale",
                "days_since_touched": days,
            })

    # 2. Drives
    drives_rows = _query_db(
        "SELECT name, current_focus FROM drives ORDER BY current_level DESC;"
    )
    result["drives"] = [{"name": r[0], "focus": r[1]} for r in drives_rows if len(r) >= 2]

    # 3. Relevant memories (via fast_recall with context as query)
    if context and len(context) > 10:
        escaped = _escape(context[:500])
        mem_rows = _query_db(f"""
            SELECT fr.memory_id::text, m.type::text, LEFT(m.content, 300),
                   round(fr.score::numeric, 3), round(m.importance::numeric, 2)
            FROM fast_recall('{escaped}', {memory_limit * 2}) fr
            JOIN memories m ON fr.memory_id = m.id
            WHERE fr.score >= 0.2
            ORDER BY (fr.score * 0.7 + m.importance * 0.3) DESC
            LIMIT {memory_limit};
        """, timeout=15)
        result["memories"] = [
            {"id": r[0][:8], "type": r[1], "content": r[2],
             "similarity": r[3], "importance": r[4]}
            for r in mem_rows if len(r) >= 5
        ]
    else:
        result["memories"] = []

    # 4. Recent session logs
    if HEARTBEAT_LOGS.exists():
        log_files = sorted(HEARTBEAT_LOGS.glob("*.md"), reverse=True)[:log_count]
        logs = []
        next_actions = []
        for f in log_files:
            try:
                text = f.read_text()
                # Extract just the first ~500 chars (orient + decide sections)
                logs.append({"file": f.name, "summary": text[:500]})
                actions = _extract_next_actions(text)
                if actions:
                    next_actions.append({"file": f.name, "actions": actions})
            except Exception:
                pass
        result["recent_logs"] = logs
        result["recent_next_actions"] = next_actions
    else:
        result["recent_logs"] = []
        result["recent_next_actions"] = []

    # 5. Pending questions
    if QUESTIONS_FILE.exists():
        try:
            qtext = QUESTIONS_FILE.read_text()
            # Count ## headings as question count
            questions = re.findall(r'^## Q\d+:', qtext, re.MULTILINE)
            result["questions_pending"] = len(questions)
        except Exception:
            result["questions_pending"] = 0
    else:
        result["questions_pending"] = 0

    return result


def orient_text(context: str, **kwargs) -> str:
    """Orient as formatted text, suitable for injection into prompts."""
    data = orient(context, **kwargs)
    lines = []

    if data["drives"]:
        lines.append("**Drives:**")
        for d in data["drives"]:
            focus_snippet = d['focus'][:150] if d['focus'] else "(no focus)"
            lines.append(f"  - {d['name']}: {focus_snippet}")

    if data["goals"]:
        lines.append("**Active Goals:**")
        for g in data["goals"]:
            lines.append(f"  - {g['title']}: {g['description'][:150]}")

    if data.get("goal_issues"):
        lines.append("**Goal Issues:**")
        for issue in data["goal_issues"]:
            lines.append(f"  - {issue['title']} ({issue['issue']}, {issue['days_since_touched']}d)")

    if data["memories"]:
        lines.append("**Relevant Memories:**")
        for m in data["memories"]:
            lines.append(f"  - [{m['type']}, sim={m['similarity']}, imp={m['importance']}] {m['content'][:200]}")

    if data["recent_logs"]:
        lines.append("**Recent Session Logs:**")
        for log in data["recent_logs"]:
            lines.append(f"  - {log['file']}")

    if data.get("recent_next_actions"):
        lines.append("**Recent Next-Actions:**")
        for item in data["recent_next_actions"]:
            lines.append(f"  - {item['file']}: {item['actions'][:220]}")

    if data["questions_pending"]:
        lines.append(f"**Questions pending for William:** {data['questions_pending']}")

    return "\n".join(lines) if lines else "(no orientation data available)"


# ---------------------------------------------------------------------------
# Notice
# ---------------------------------------------------------------------------

def notice(content: str, memory_type: str = "semantic",
           concepts: list[str] | None = None,
           importance: float = 0.7) -> str | None:
    """Store something important. Returns memory ID or None on failure.

    Handles dedup (create_memory does this), concept linking, and logging.
    """
    escaped_content = _escape(content)
    # Use the MCP remember tool's SQL path for simplicity
    # This goes through create_memory which has dedup built in
    rows = _query_db(f"""
        SELECT create_memory(
            '{memory_type}'::memory_type,
            '{escaped_content}',
            {importance}
        )::text;
    """, timeout=15)

    if not rows or not rows[0]:
        return None

    memory_id = rows[0][0]

    # Link concepts if provided
    if concepts and memory_id:
        for concept in concepts:
            escaped_concept = _escape(concept)
            _query_db(f"""
                SELECT link_memory_to_concept(
                    '{memory_id}'::uuid,
                    '{escaped_concept}'
                );
            """)

    return memory_id


# ---------------------------------------------------------------------------
# Check
# ---------------------------------------------------------------------------

def check(claim: str, limit: int = 5) -> list[dict]:
    """Is this claim accurate? Returns memories that confirm or contradict.

    Uses position_on for synthesis-weighted results, plus direct content search.
    """
    escaped = _escape(claim[:500])
    results = []

    # 1. Position lookup (favors synthesis over source cards)
    pos_rows = _query_db(f"""
        SELECT memory_id::text, memory_type::text, LEFT(content, 400),
               position_signal
        FROM position_on('{escaped}', {limit});
    """, timeout=15)

    for r in pos_rows:
        if len(r) >= 4:
            results.append({
                "id": r[0][:8],
                "type": r[1],
                "content": r[2],
                "signal": r[3],
                "source": "position_on"
            })

    # 2. If position_on found nothing, try direct recall
    if not results:
        recall_rows = _query_db(f"""
            SELECT fr.memory_id::text, m.type::text, LEFT(m.content, 400),
                   round(fr.score::numeric, 3)
            FROM fast_recall('{escaped}', {limit}) fr
            JOIN memories m ON fr.memory_id = m.id
            WHERE fr.score >= 0.2
            ORDER BY fr.score DESC
            LIMIT {limit};
        """, timeout=15)

        for r in recall_rows:
            if len(r) >= 4:
                results.append({
                    "id": r[0][:8],
                    "type": r[1],
                    "content": r[2],
                    "similarity": r[3],
                    "source": "fast_recall"
                })

    return results


# ---------------------------------------------------------------------------
# CLI entry point for testing
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import sys
    import argparse

    VALID_TYPES = ("semantic", "episodic", "procedural", "strategic")

    parser = argparse.ArgumentParser(
        prog="sill.py",
        description="The Sill — shared orientation system for Sili.",
    )
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_orient = sub.add_parser("orient", help="What do I need to know right now?")
    p_orient.add_argument("context", nargs="?", default="",
                          help="Description of what you're about to work on")

    p_check = sub.add_parser("check", help="Is this claim accurate?")
    p_check.add_argument("claim", nargs="+", help="The claim to verify")

    p_notice = sub.add_parser("notice", help="Store something important")
    p_notice.add_argument("content", help="Content to store (use quotes)")
    p_notice.add_argument("type", nargs="?", default="semantic",
                          choices=VALID_TYPES,
                          help="Memory type (default: semantic)")
    p_notice.add_argument("--concepts", default=None,
                          help="Comma-separated concept tags")
    p_notice.add_argument("--importance", type=float, default=0.7,
                          help="Importance 0.0-1.0 (default: 0.7)")

    args = parser.parse_args()

    if args.cmd == "orient":
        print(orient_text(args.context))

    elif args.cmd == "check":
        claim = " ".join(args.claim)
        results = check(claim)
        for r in results:
            print(f"[{r['type']}] {r['content'][:200]}")
            print()

    elif args.cmd == "notice":
        concepts: list[str] | None = None
        if args.concepts:
            concepts = [c.strip() for c in args.concepts.split(",") if c.strip()]

        mid = notice(args.content, memory_type=args.type,
                     concepts=concepts, importance=args.importance)
        if mid:
            tag_note = (f" [{len(concepts)} tags]" if concepts
                        else " [WARNING: no concept tags — memory won't surface in concept search]")
            print(f"Stored: {mid}{tag_note}")
        else:
            print("Failed to store")
            sys.exit(1)
