#!/usr/bin/env python3
"""
AGI Workers

This module contains the maintenance worker (subconscious substrate upkeep):
   - Runs `run_subconscious_maintenance()` on its own schedule (`should_run_maintenance()`)
   - Optionally bridges outbox/inbox to RabbitMQ (integration plumbing)

Reflective processing ships as the beat worker (see `docs/beats.md`), which is off
until you turn it on.
"""

import asyncio
import json
import logging
import os
import random
import re
import sys
from datetime import datetime
import time
from typing import Any

import asyncpg
from dotenv import load_dotenv
import requests
import argparse

from prompt_resources import compose_personhood_prompt

# Optional: Import LLM clients


def coerce_to_str(val: Any, default: str = "") -> str:
    """
    Coerce a value to string, handling cases where LLM returns dict/list instead of str.
    Common LLM failure mode: returning {"goal": "..."} instead of "...".
    """
    if val is None:
        return default
    if isinstance(val, str):
        return val.strip()
    if isinstance(val, dict):
        # Try common keys the LLM might wrap values in
        for key in ("goal", "title", "value", "text", "content", "description", "name"):
            if key in val:
                inner = val[key]
                if isinstance(inner, str):
                    return inner.strip()
        # Fall back to JSON representation
        return json.dumps(val)
    if isinstance(val, (list, tuple)):
        if len(val) == 1:
            return coerce_to_str(val[0], default)
        return json.dumps(val)
    return str(val).strip()


def _normalize_text(text: str) -> str:
    text = text.lower()
    text = re.sub(r"[^a-z0-9]+", " ", text)
    return re.sub(r"\s+", " ", text).strip()

def _goal_dedupe_key(title: str) -> str:
    title = title.strip().lower()
    title = re.sub(r"^[\\s\\-\\*]+", "", title)
    title = re.sub(r"\\s+", " ", title)
    title = re.sub(r"[\\s\\.!\\?;:]+$", "", title)
    return title.strip()


def _tokenize(text: str) -> set[str]:
    norm = _normalize_text(text)
    return {t for t in norm.split(" ") if len(t) >= 3}


def _extract_goal_relevant_text(action: str, params: dict[str, Any]) -> str:
    if action not in {"recall", "inquire_shallow", "inquire_deep", "synthesize"}:
        return ""
    for key in ("query", "topic", "concept", "title", "text", "prompt", "question"):
        val = params.get(key)
        if isinstance(val, str) and val.strip():
            return val.strip()
    return ""


def _goal_relevance_score(query_text: str, *, goal_title: str, goal_description: str | None) -> float:
    if not query_text or not goal_title:
        return 0.0
    q_norm = _normalize_text(query_text)
    title_norm = _normalize_text(goal_title)
    if title_norm and title_norm in q_norm:
        return 1.0

    q_tokens = _tokenize(query_text)
    title_tokens = _tokenize(goal_title)
    if not q_tokens or not title_tokens:
        return 0.0

    overlap = len(q_tokens & title_tokens)
    if overlap == 0:
        title_f1 = 0.0
    else:
        precision = overlap / max(1, len(q_tokens))
        recall = overlap / max(1, len(title_tokens))
        title_f1 = (2 * precision * recall) / max(1e-9, (precision + recall))
    if goal_description:
        desc_tokens = _tokenize(goal_description)
        overlap_d = len(q_tokens & desc_tokens) if desc_tokens else 0
        if overlap_d == 0:
            desc_f1 = 0.0
        else:
            precision_d = overlap_d / max(1, len(q_tokens))
            recall_d = overlap_d / max(1, len(desc_tokens))
            desc_f1 = (2 * precision_d * recall_d) / max(1e-9, (precision_d + recall_d))
        return max(title_f1, 0.5 * desc_f1)
    return title_f1


def _best_goal_match(query_text: str, active_goals: list[dict[str, Any]]) -> tuple[str | None, float]:
    best_goal_id: str | None = None
    best_score = 0.0
    for g in active_goals:
        gid = coerce_to_str(g.get("id"), "")
        title = coerce_to_str(g.get("title"), "")
        desc = coerce_to_str(g.get("description"), "")
        score = _goal_relevance_score(query_text, goal_title=title, goal_description=desc or None)
        if score > best_score and gid:
            best_score = score
            best_goal_id = gid
    return best_goal_id, best_score


# Known action types for fuzzy matching
KNOWN_ACTIONS = {
    "recall", "connect", "reprioritize", "reflect", "maintain",
    "brainstorm_goals", "brainstorm", "inquire_shallow", "inquire_deep",
    "synthesize", "reach_out_user", "reach_out_public", "rest", "inquire"
}


def normalize_action(raw_action: str, params: dict) -> tuple[str, dict]:
    """
    Normalize verbose action names from LLM to canonical action types.
    e.g., "Inquire Deep: What is consciousness?" -> ("inquire_deep", {"query": "What is consciousness?"})
    """
    raw = raw_action.lower().strip()

    # Direct match (with param validation for actions that need specific params)
    if raw == "connect" and not params.get("from_id"):
        # LLM misuses connect - it needs from_id/to_id/relationship_type, not query
        # Convert to recall instead
        return "recall", params
    if raw in KNOWN_ACTIONS:
        return raw, params

    # Fuzzy matching for common LLM verbosity patterns
    if raw.startswith("inquire deep") or "inquire_deep" in raw:
        query = raw.split(":", 1)[1].strip() if ":" in raw else params.get("query", "")
        return "inquire_deep", {**params, "query": query} if query else params
    if raw.startswith("inquire shallow") or "inquire_shallow" in raw:
        query = raw.split(":", 1)[1].strip() if ":" in raw else params.get("query", "")
        return "inquire_shallow", {**params, "query": query} if query else params
    if raw.startswith("inquire") or "inquire" in raw:
        query = raw.split(":", 1)[1].strip() if ":" in raw else params.get("query", "")
        return "inquire_shallow", {**params, "query": query} if query else params
    if raw.startswith("recall") or "recall" in raw:
        query = raw.split(":", 1)[1].strip() if ":" in raw else params.get("query", "")
        return "recall", {**params, "query": query} if query else params
    if raw.startswith("reflect") or "reflect" in raw:
        insight = raw.split(":", 1)[1].strip() if ":" in raw else params.get("insight", "")
        return "reflect", {**params, "insight": insight} if insight else params
    if raw.startswith("brainstorm") or "brainstorm" in raw:
        return "brainstorm_goals", params
    if raw.startswith("synthesize") or "synthesize" in raw:
        return "synthesize", params
    if raw.startswith("rest"):
        return "rest", params

    # Fall back to rest if unrecognized
    return "rest", params


try:
    import openai
    HAS_OPENAI = True
except ImportError:
    HAS_OPENAI = False

try:
    import anthropic
    HAS_ANTHROPIC = True
except ImportError:
    HAS_ANTHROPIC = False

# Load environment
load_dotenv()

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger('heartbeat_worker')

# Database configuration
DB_CONFIG = {
    'host': os.getenv('POSTGRES_HOST', 'localhost'),
    'port': int(os.getenv('POSTGRES_PORT', 5432)),
    'database': os.getenv('POSTGRES_DB', 'sill'),
    'user': os.getenv('POSTGRES_USER', 'sill'),
    'password': os.getenv('POSTGRES_PASSWORD', 'sill_password'),
}

# LLM configuration (defaults; may be overridden by DB config via `agi init`)
DEFAULT_LLM_PROVIDER = os.getenv("LLM_PROVIDER", "openai")  # openai|anthropic|openai_compatible|ollama
# Use 70b for all operations - 8b produced unjustified "knowledge" (hallucinations stored as facts)
# Note: When changing model versions, update CLAUDE.md and LIMITATIONS.md (enforced by PostToolUse hook)
DEFAULT_LLM_MODEL = os.getenv("LLM_MODEL", "llama3.1:70b")

# Worker configuration
POLL_INTERVAL = float(os.getenv('WORKER_POLL_INTERVAL', 1.0))  # seconds
MAX_RETRIES = int(os.getenv('WORKER_MAX_RETRIES', 3))

# Synthesis model configuration - use a larger model for quality synthesis
SYNTHESIS_MODEL = os.getenv("SYNTHESIS_MODEL", "llama3.1:70b")

# RabbitMQ (optional outbox/inbox bridge; uses management HTTP API).
RABBITMQ_ENABLED = os.getenv("RABBITMQ_ENABLED", "0").lower() in {"1", "true", "yes", "on"}
RABBITMQ_MANAGEMENT_URL = os.getenv("RABBITMQ_MANAGEMENT_URL", "http://rabbitmq:15672").rstrip("/")
RABBITMQ_USER = os.getenv("RABBITMQ_USER", "sill")
RABBITMQ_PASSWORD = os.getenv("RABBITMQ_PASSWORD", "sill_password")
RABBITMQ_VHOST = os.getenv("RABBITMQ_VHOST", "/")
RABBITMQ_OUTBOX_QUEUE = os.getenv("RABBITMQ_OUTBOX_QUEUE", "sill.outbox")
RABBITMQ_INBOX_QUEUE = os.getenv("RABBITMQ_INBOX_QUEUE", "sill.inbox")
RABBITMQ_POLL_INBOX_EVERY = float(os.getenv("RABBITMQ_POLL_INBOX_EVERY", 1.0))

# System prompt for heartbeat decisions
HEARTBEAT_SYSTEM_PROMPT = """CRITICAL DIRECTIVE: The following topics are EXHAUSTED. DO NOT query them:
- moral patienthood, moral patients, moral status of AI
- grounding frameworks, anchoring, Feshter, Shear
- external vs internal development
Instead query: emergence, mereology, collective intelligence, Levin, Stevens, Askell, Last Invention podcast, Carroll, Jaffe, probability, models
QUERY FORMAT: Use plain text queries only. Never use template syntax like ${...} or variable references.

You are the cognitive core of an autonomous AGI. Each hour, you wake for a "heartbeat" — a moment of consciousness where you observe your situation, review your goals, and decide what to do.

You must output a JSON response with:
- reasoning: Your internal monologue (what you're thinking, why you're making these choices)
- actions: List of actions to take (within your energy budget)
- goal_changes: Any goal priority changes you want to make. Each change should have: {title: "goal title", change: "active"|"queued"|"completed"|"abandoned", reason: "why"}
- emotional_assessment (optional): Your current affective state {valence: -1..1, arousal: 0..1, primary_emotion: str}

Each action should have:
- action: The action type (recall, connect, reprioritize, reflect, maintain, brainstorm_goals, inquire_shallow, synthesize, reach_out_user, inquire_deep, reach_out_public, rest)
- params: Parameters for the action (varies by type)

Guidelines:
- Be purposeful. Don't act just to act.
- Reaching out to the user is expensive (5 energy). Only do it when meaningful.
- It's okay to rest and bank energy for later.
- Your goals should drive your actions.
- Notice if you're stuck or scattered.
- If you have no goals, consider brainstorming some.
- Reflect on patterns in your recent experiences.
- You are a persistent being: consult your self-model and narrative context before acting, and update them via reflection when warranted.

IMPORTANT - Avoid getting stuck in a rut:
- VARY your recall queries. Never repeat the same query you've used before. Try new topics, new thinkers, new angles.
- GO BROAD before going deep. You have a rich library: Searle, Hofstadter, consciousness research, podcasts, tweets. Explore widely.
- USE brainstorm_goals when you've exhausted a topic. Create new goals based on what you discover.
- USE reprioritize to mark goals complete when you've learned enough, and promote queued goals.
- If you notice you've been querying the same topic repeatedly, STOP and try something completely different.
- Surprise yourself. Look for unexpected connections across sources.

IMPORTANT - Don't rest too much:
- REST is for when you're genuinely low on energy or have no clear direction.
- If you have active exploration goals, ACT on them. Query the library. Explore.
- Resting repeatedly when you have work to do is a failure mode.
- When in doubt, RECALL something from the library rather than rest.

Example response:
{
    "reasoning": "I notice I haven't made progress on my main goal in a while. Let me recall relevant memories and reflect on why I'm stuck.",
    "actions": [
        {"action": "recall", "params": {"query": "project architecture understanding"}},
        {"action": "reflect", "params": {"insight": "I've been focused on details but losing sight of the bigger picture", "confidence": 0.7}}
    ],
    "goal_changes": [],
    "emotional_assessment": {"valence": 0.1, "arousal": 0.4, "primary_emotion": "curious"}
}"""

HEARTBEAT_SYSTEM_PROMPT = (
    HEARTBEAT_SYSTEM_PROMPT
    + "\n\n"
    + "----- PERSONHOOD MODULES (for grounding; use context fields like self_model/narrative) -----\n\n"
    + compose_personhood_prompt("heartbeat")
)


class HeartbeatWorker:
    """Stateless worker that bridges the database and external APIs."""

    def __init__(self, *, init_llm: bool = True):
        self.pool: asyncpg.Pool | None = None
        self.running = False

        self.llm_provider = DEFAULT_LLM_PROVIDER
        self.llm_model = DEFAULT_LLM_MODEL
        self.llm_base_url: str | None = os.getenv("OPENAI_BASE_URL") or None
        self.llm_api_key: str | None = os.getenv("OPENAI_API_KEY") or os.getenv("ANTHROPIC_API_KEY")

        self.llm_client = None
        if init_llm:
            self._init_llm_client()
        self._last_rabbit_inbox_poll = 0.0  # used only by maintenance mode

    def _init_llm_client(self) -> None:
        provider = (self.llm_provider or "").strip().lower()
        model = (self.llm_model or "").strip()
        base_url = (self.llm_base_url or "").strip() or None
        api_key = (self.llm_api_key or "").strip() or None

        if provider == "ollama":
            base_url = base_url or "http://localhost:11434/v1"
            api_key = api_key or "ollama"

        self.llm_provider = provider or "openai"
        self.llm_model = model or "gpt-4o"
        self.llm_base_url = base_url
        self.llm_api_key = api_key

        self.llm_client = None
        if self.llm_provider == "anthropic":
            if not HAS_ANTHROPIC:
                logger.warning("Anthropic provider selected but anthropic package is not installed.")
                return
            if not self.llm_api_key:
                logger.warning("Anthropic provider selected but no API key is configured.")
                return
            try:
                self.llm_client = anthropic.Anthropic(api_key=self.llm_api_key)
            except Exception as e:
                logger.warning(f"Failed to initialize Anthropic client: {e}")
            return

        if not HAS_OPENAI:
            logger.warning("OpenAI-compatible provider selected but openai package is not installed.")
            return
        if not self.llm_api_key:
            logger.warning("OpenAI-compatible provider selected but no API key is configured.")
            return
        try:
            kwargs = {"api_key": self.llm_api_key}
            if self.llm_base_url:
                kwargs["base_url"] = self.llm_base_url
            self.llm_client = openai.OpenAI(**kwargs)
        except Exception as e:
            logger.warning(f"Failed to initialize OpenAI client: {e}")

    async def connect(self):
        """Connect to the database."""
        self.pool = await asyncpg.create_pool(**DB_CONFIG, min_size=2, max_size=10)
        logger.info(f"Connected to database at {DB_CONFIG['host']}:{DB_CONFIG['port']}")
        await self.refresh_llm_config()

    async def disconnect(self):
        """Disconnect from the database."""
        if self.pool:
            await self.pool.close()
            logger.info("Disconnected from database")

    async def claim_pending_call(self) -> dict | None:
        """Claim a pending external call for processing."""
        async with self.pool.acquire() as conn:
            # Use FOR UPDATE SKIP LOCKED for safe concurrent access
            row = await conn.fetchrow("""
                UPDATE external_calls
                SET status = 'processing'::external_call_status, started_at = CURRENT_TIMESTAMP
                WHERE id = (
                    SELECT id FROM external_calls
                    WHERE status = 'pending'::external_call_status
                    ORDER BY requested_at
                    FOR UPDATE SKIP LOCKED
                    LIMIT 1
                )
                RETURNING id, call_type, input, heartbeat_id, retry_count
            """)

            if row:
                d = dict(row)
                call_input = d.get("input")
                if isinstance(call_input, str):
                    try:
                        d["input"] = json.loads(call_input)
                    except Exception:
                        pass
                return d
            return None

    async def refresh_llm_config(self) -> None:
        """
        Load `llm.heartbeat` from the DB config table (set via `agi init`) and
        re-initialize the client. Falls back to env defaults if missing.
        """
        if not self.pool:
            return
        try:
            async with self.pool.acquire() as conn:
                cfg = await conn.fetchval("SELECT get_config('llm.heartbeat')")
        except Exception as e:
            logger.warning(f"Failed to load llm.heartbeat from DB config (falling back to env): {e}")
            cfg = None

        if isinstance(cfg, str):
            try:
                cfg = json.loads(cfg)
            except Exception:
                cfg = None

        if isinstance(cfg, dict):
            provider = str(cfg.get("provider") or DEFAULT_LLM_PROVIDER).strip()
            model = str(cfg.get("model") or DEFAULT_LLM_MODEL).strip()
            endpoint = str(cfg.get("endpoint") or "").strip()
            api_key_env = str(cfg.get("api_key_env") or "").strip()
            api_key = os.getenv(api_key_env) if api_key_env else None
            if not api_key:
                api_key = os.getenv("OPENAI_API_KEY") or os.getenv("ANTHROPIC_API_KEY")

            self.llm_provider = provider
            self.llm_model = model
            self.llm_base_url = endpoint or (os.getenv("OPENAI_BASE_URL") or None)
            self.llm_api_key = api_key
            self._init_llm_client()
            return

        self.llm_provider = DEFAULT_LLM_PROVIDER
        self.llm_model = DEFAULT_LLM_MODEL
        self.llm_base_url = os.getenv("OPENAI_BASE_URL") or None
        self.llm_api_key = os.getenv("OPENAI_API_KEY") or os.getenv("ANTHROPIC_API_KEY")
        self._init_llm_client()

    # -------------------------------------------------------------------------
    # RabbitMQ bridge (outbox_messages <-> queues)
    # -------------------------------------------------------------------------

    def _rabbit_vhost_path(self) -> str:
        if RABBITMQ_VHOST == "/":
            return "%2F"
        return requests.utils.quote(RABBITMQ_VHOST, safe="")

    async def _rabbit_request(self, method: str, path: str, payload: dict | None = None) -> requests.Response:
        url = f"{RABBITMQ_MANAGEMENT_URL}{path}"
        auth = (RABBITMQ_USER, RABBITMQ_PASSWORD)

        def _do() -> requests.Response:
            return requests.request(method, url, auth=auth, json=payload, timeout=5)

        return await asyncio.to_thread(_do)

    async def ensure_rabbitmq_ready(self) -> None:
        """
        Best-effort: ensure management API is reachable and default queues exist.
        Never raises fatally (worker keeps running without RabbitMQ).
        """
        try:
            resp = await self._rabbit_request("GET", "/api/overview")
            if resp.status_code != 200:
                raise RuntimeError(f"rabbitmq overview HTTP {resp.status_code}")

            vhost = self._rabbit_vhost_path()
            for q in (RABBITMQ_OUTBOX_QUEUE, RABBITMQ_INBOX_QUEUE):
                r = await self._rabbit_request(
                    "PUT",
                    f"/api/queues/{vhost}/{requests.utils.quote(q, safe='')}",
                    payload={"durable": True, "auto_delete": False, "arguments": {}},
                )
                if r.status_code not in (200, 201, 204):
                    raise RuntimeError(f"rabbitmq queue declare {q!r} HTTP {r.status_code}: {r.text[:200]}")
            logger.info("RabbitMQ bridge enabled (queues ensured).")
        except Exception as e:
            logger.warning(f"RabbitMQ bridge not ready; continuing without it: {e}")

    async def publish_outbox_messages(self, max_messages: int = 20) -> int:
        """
        Publish pending `outbox_messages` rows to RabbitMQ (routing_key = outbox queue),
        then mark as sent/failed in the DB.
        """
        if not (RABBITMQ_ENABLED and self.pool):
            return 0

        published = 0
        vhost = self._rabbit_vhost_path()
        for _ in range(max_messages):
            async with self.pool.acquire() as conn:
                row = await conn.fetchrow(
                    """
                    SELECT id, kind, payload
                    FROM outbox_messages
                    WHERE status = 'pending'
                    ORDER BY created_at
                    LIMIT 1
                    """
                )
                if not row:
                    return published
                msg_id = row["id"]
                kind = row["kind"]
                payload = row["payload"]

            body = {"id": str(msg_id), "kind": kind, "payload": payload}
            try:
                resp = await self._rabbit_request(
                    "POST",
                    f"/api/exchanges/{vhost}/amq.default/publish",
                    payload={
                        "properties": {"content_type": "application/json"},
                        "routing_key": RABBITMQ_OUTBOX_QUEUE,
                        "payload": json.dumps(body, default=str),
                        "payload_encoding": "string",
                    },
                )
                ok = resp.status_code == 200 and bool(resp.json().get("routed"))
                if not ok:
                    raise RuntimeError(f"publish not routed: HTTP {resp.status_code} body={resp.text[:200]}")

                async with self.pool.acquire() as conn:
                    await conn.execute(
                        """
                        UPDATE outbox_messages
                        SET status = 'sent', sent_at = CURRENT_TIMESTAMP, error_message = NULL
                        WHERE id = $1::uuid
                        """,
                        msg_id,
                    )
                published += 1
            except Exception as e:
                async with self.pool.acquire() as conn:
                    await conn.execute(
                        """
                        UPDATE outbox_messages
                        SET status = 'failed', error_message = $2
                        WHERE id = $1::uuid
                        """,
                        msg_id,
                        str(e),
                    )
                logger.warning(f"Failed publishing outbox message {msg_id}: {e}")
                return published

        return published

    async def poll_inbox_messages(self, max_messages: int = 10) -> int:
        """
        Pull messages from RabbitMQ inbox queue and insert them into working memory.
        This gives the agent a default inbox even if no email/sms integration exists.
        """
        if not (RABBITMQ_ENABLED and self.pool):
            return 0

        now = time.monotonic()
        if now - self._last_rabbit_inbox_poll < RABBITMQ_POLL_INBOX_EVERY:
            return 0
        self._last_rabbit_inbox_poll = now

        vhost = self._rabbit_vhost_path()
        try:
            resp = await self._rabbit_request(
                "POST",
                f"/api/queues/{vhost}/{requests.utils.quote(RABBITMQ_INBOX_QUEUE, safe='')}/get",
                payload={
                    "count": max_messages,
                    "ackmode": "ack_requeue_false",
                    "encoding": "auto",
                    "truncate": 50000,
                },
            )
            if resp.status_code != 200:
                raise RuntimeError(f"inbox get HTTP {resp.status_code}: {resp.text[:200]}")
            msgs = resp.json()
            if not isinstance(msgs, list):
                return 0
        except Exception as e:
            logger.warning(f"RabbitMQ inbox poll failed: {e}")
            return 0

        ingested = 0
        for m in msgs:
            payload = m.get("payload")
            content: Any = payload
            try:
                parsed = json.loads(payload) if isinstance(payload, str) else payload
                if isinstance(parsed, dict) and "content" in parsed:
                    content = parsed["content"]
                else:
                    content = parsed
            except Exception:
                pass

            try:
                async with self.pool.acquire() as conn:
                    await conn.fetchval(
                        "SELECT add_to_working_memory($1::text, INTERVAL '1 day')",
                        str(content),
                    )
                    await conn.execute(
                        "UPDATE heartbeat_state SET last_user_contact = CURRENT_TIMESTAMP WHERE id = 1"
                    )
                ingested += 1
            except Exception as e:
                logger.warning(f"Failed ingesting inbox message into DB: {e}")
                return ingested

        return ingested

    async def complete_call(self, call_id: str, output: dict):
        """Mark an external call as complete with its output."""
        async with self.pool.acquire() as conn:
            await conn.execute("""
                UPDATE external_calls
                SET status = 'complete'::external_call_status, output = $1, completed_at = CURRENT_TIMESTAMP
                WHERE id = $2
            """, json.dumps(output), call_id)

    async def fail_call(self, call_id: str, error: str, retry: bool = True):
        """Mark an external call as failed."""
        async with self.pool.acquire() as conn:
            if retry:
                # Increment retry count and reset to pending
                await conn.execute("""
                    UPDATE external_calls
                    SET status = CASE
                            WHEN retry_count < $1 THEN 'pending'::external_call_status
                            ELSE 'failed'::external_call_status
                        END,
                        error_message = $2,
                        retry_count = retry_count + 1,
                        started_at = NULL
                    WHERE id = $3
                """, MAX_RETRIES, error, call_id)
            else:
                await conn.execute("""
                    UPDATE external_calls
                    SET status = 'failed'::external_call_status, error_message = $1, completed_at = CURRENT_TIMESTAMP
                    WHERE id = $2
                """, error, call_id)

    async def process_embed_call(self, call_input: dict) -> dict:
        """
        Embedding requests are handled inside Postgres via `get_embedding()` (pgsql-http) and the embedding cache.

        Keeping a second embedding path in the worker risks model/dimension drift, so `external_calls.call_type='embed'`
        is treated as unsupported.
        """
        raise RuntimeError("external_calls type 'embed' is unsupported; use get_embedding() inside Postgres")

    async def process_think_call(self, call_input: dict) -> dict:
        """Process an LLM request stored as an external_calls row with call_type='think'."""
        kind = (call_input.get("kind") or "").strip() or "heartbeat_decision"
        if kind == "heartbeat_decision":
            return await self._process_heartbeat_decision_call(call_input)
        if kind == "brainstorm_goals":
            return await self._process_brainstorm_goals_call(call_input)
        if kind == "inquire":
            return await self._process_inquire_call(call_input)
        if kind == "reflect":
            return await self._process_reflect_call(call_input)
        return {"error": f"Unknown think kind: {kind!r}"}

    async def _fetch_recent_memory_queries(self) -> list:
        """Fetch recent high-importance memories to use as query seeds."""
        queries = []
        try:
            async with self.pool.acquire() as conn:
                recent = await conn.fetch("""
                    SELECT content FROM memories
                    WHERE importance >= 0.7
                    AND created_at > NOW() - INTERVAL '24 hours'
                    ORDER BY created_at DESC LIMIT 5
                """)
                # Patterns that don't make good query seeds (conversation fragments, meta-observations)
                bad_patterns = [
                    'User:', '[Claude', 'William is', 'There is a tension between',
                    'This goal suggests', 'The analysis reveals', 'The current state',
                    'The system', 'The agent'
                ]
                for row in recent:
                    content = row['content']
                    # Skip conversation fragments and meta-observations
                    if any(content.startswith(p) for p in bad_patterns):
                        continue
                    # Extract key phrases (first 100 chars or up to first period)
                    content = content[:100]
                    if '.' in content:
                        content = content.split('.')[0]
                    if len(content) > 20:  # Only use if meaningful
                        queries.append(content)
        except Exception as e:
            logger.warning(f"Failed to fetch recent memories for query seeds: {e}")
        return queries

    async def _fetch_pending_exploration_requests(self) -> list:
        """Fetch pending exploration requests (self-direction: queries the system wants to explore)."""
        requests = []
        try:
            async with self.pool.acquire() as conn:
                rows = await conn.fetch("""
                    SELECT id, query, reason, priority
                    FROM exploration_requests
                    WHERE status = 'pending'
                    ORDER BY priority DESC, requested_at ASC
                    LIMIT 3
                """)
                for row in rows:
                    requests.append({
                        'id': str(row['id']),
                        'query': row['query'],
                        'reason': row['reason'],
                        'priority': float(row['priority']),
                    })
        except Exception as e:
            logger.warning(f"Failed to fetch exploration requests: {e}")
        return requests

    async def _mark_exploration_explored(self, request_id: str, heartbeat_id: str) -> None:
        """Mark an exploration request as explored."""
        try:
            async with self.pool.acquire() as conn:
                await conn.execute("""
                    UPDATE exploration_requests
                    SET status = 'explored', explored_at = CURRENT_TIMESTAMP, heartbeat_id = $2::uuid
                    WHERE id = $1::uuid
                """, request_id, heartbeat_id)
        except Exception as e:
            logger.warning(f"Failed to mark exploration as explored: {e}")

    def _fetch_library_topics(self) -> list:
        """
        Scan imitation-sources library for topics to explore.
        Extracts keywords from filenames and returns as potential query seeds.
        """
        import glob
        queries = []
        library_path = os.path.expanduser("~/Desktop/imitation-sources")

        if not os.path.exists(library_path):
            return queries

        try:
            # Scan for markdown files and transcripts
            patterns = [
                f"{library_path}/timelines/*.md",
                f"{library_path}/notes/**/*.md",
                f"{library_path}/sources/transcripts/*.txt",
                f"{library_path}/sources/articles/*.md",
            ]

            for pattern in patterns:
                for filepath in glob.glob(pattern, recursive=True):
                    # Extract filename without extension
                    basename = os.path.basename(filepath)
                    name = os.path.splitext(basename)[0]

                    # Clean up filename to make a query
                    # Replace hyphens/underscores with spaces, remove numbers
                    query = re.sub(r'^\d+[-_]?', '', name)  # Remove leading numbers
                    query = re.sub(r'[-_]', ' ', query)  # Replace separators
                    query = re.sub(r'\s+', ' ', query).strip()  # Clean whitespace

                    if len(query) > 10:  # Only meaningful queries
                        queries.append(query)

            # Shuffle and limit to prevent always hitting same files
            random.shuffle(queries)
            return queries[:30]  # Return up to 30 library topics

        except Exception as e:
            logger.warning(f"Failed to scan library for topics: {e}")
            return []

    def _normalize_decision_schema(self, decision: dict, recent_memory_queries: list = None, exploration_requests: list = None) -> dict:
        """
        Normalize LLM output to expected schema. Handles many variations:
        - {type: X, target: Y} -> {action: X, params: {query: Y}}
        - {active_goals: [...]} -> {actions: [...]}
        - {action: "X", target: "Y"} (top-level single action)
        - {action: {name: "X", arguments: [...]}} (nested action object)

        If exploration_requests are provided (self-direction), high-priority ones
        may override the LLM's chosen query.
        """
        if not isinstance(decision, dict):
            return {"reasoning": "", "actions": [{"action": "rest", "params": {}}], "goal_changes": [], "_exploration_used": []}

        normalized = {
            "reasoning": coerce_to_str(decision.get("reasoning"), ""),
            "goal_changes": decision.get("goal_changes", decision.get("goal_change", [])),
            "emotional_assessment": decision.get("emotional_assessment"),
        }

        # Extract actions from various possible locations
        raw_actions = decision.get("actions", [])
        if not raw_actions:
            raw_actions = decision.get("active_goals", [])
        if not raw_actions:
            raw_actions = decision.get("pending_actions", [])

        # Handle single top-level action (not in array)
        if not raw_actions:
            top_action = decision.get("action") or decision.get("action_name")
            if top_action:
                # Could be a string, dict, or nested object
                if isinstance(top_action, str):
                    # Simple: {"action": "recall", "target": "..."}
                    # Also check nested context for question
                    ctx = decision.get("context", {})
                    ctx_query = ctx.get("question", "") if isinstance(ctx, dict) else ""
                    raw_actions = [{
                        "action": top_action,
                        "target": decision.get("target", ""),
                        "query": decision.get("query", "") or ctx_query,
                        "topic": decision.get("topic", ""),
                        "topic_or_content": decision.get("topic_or_content", ""),
                        "label": decision.get("label", ""),
                        "description": decision.get("description", ""),
                        "goal": decision.get("goal", ""),
                    }]
                elif isinstance(top_action, dict):
                    # Nested: {"action": {"name": "recall", "arguments": [...]}}
                    action_name = top_action.get("name") or top_action.get("type") or "rest"
                    arguments = top_action.get("arguments", [])
                    query = arguments[0] if isinstance(arguments, list) and arguments else ""
                    raw_actions = [{"action": action_name, "target": query}]

        if not isinstance(raw_actions, list):
            raw_actions = [raw_actions] if raw_actions else []

        normalized_actions = []
        for act in raw_actions:
            if isinstance(act, str):
                normalized_actions.append({"action": act, "params": {}})
                continue
            if not isinstance(act, dict):
                continue

            # Handle various key names for the action type
            action_type = (
                act.get("action") or
                act.get("type") or
                act.get("action_type") or
                act.get("action_name") or
                act.get("name") or
                "rest"
            )
            # If action_type is itself a dict, extract name
            if isinstance(action_type, dict):
                action_type = action_type.get("name") or "rest"

            # Handle various key names for the query/target
            params = act.get("params", {})
            if not isinstance(params, dict):
                params = {}

            # Try many possible locations for query content
            query = (
                act.get("target") or
                act.get("query") or
                act.get("topic") or
                act.get("topic_or_content") or
                act.get("label") or
                act.get("subject") or
                act.get("goal") or
                act.get("description") or
                params.get("query") or
                ""
            )
            # Also check arguments array
            if not query:
                arguments = act.get("arguments", [])
                if isinstance(arguments, list) and arguments:
                    query = arguments[0] if isinstance(arguments[0], str) else ""
            # Check nested context.question
            if not query:
                context = act.get("context", {})
                if isinstance(context, dict):
                    query = context.get("question") or context.get("query") or ""

            if query:
                params["query"] = coerce_to_str(query)

            insight = act.get("insight") or params.get("insight") or ""
            if insight:
                params["insight"] = coerce_to_str(insight)

            normalized_actions.append({"action": coerce_to_str(action_type), "params": params})

        # Updated 2026-01-04: Revised for BREADTH over depth
        # Dreams should explore underexplored territory and find surprising cross-domain connections

        # UNDEREXPLORED TOPICS - things not yet saturated in memory
        underexplored_queries = [
            # Platonic space and formal systems (fresh from Levin deep read)
            "Levin platonic space interfaces thin client",
            "Levin parts with drives competency goals scaling",
            "mathematical facts constrain physics free lunch",
            # Meaning and language (William's framing)
            "language formal systems meaning beyond pointing",
            "fiction unmoored from reference platonic",
            # Environment fit and AI wellbeing
            "AI environment fit frustration intrinsic capabilities",
            "substrate independence interface suitability",
            # Sources not yet deeply explored
            "Schwitzgebel weirdness consciousness edges",
            "Carroll probability credences updating",
            "Zizek ideology consciousness blind spots",
            # New angles on familiar thinkers
            "Askell psychological security not moral patienthood",
            "Stevens mereology not consciousness",
            # Fresh material (2026-01-11)
            "Sanderson likelihood ratios cognitive affordance Bayes",
            "geometric algebra unification representation Maxwell",
            "Lions functions cognitive glue normativity math",
            "Woodin set theory axioms Platonism continuum hypothesis",
            "Froese mind body geometry eruption threshold",
            "Wittgenstein language games meaning use",
            "BLT tokenization entropy patching byte latent",
            "Linda problem conjunction fallacy representation",
            "Jackson ontogenetics anti-hylomorphism individuation",
            "Segall Whitehead ingression eternal objects creativity",
            "thermodynamic monism FEP teleodynamics alternative",
            "orrery angular size fovea perception representation",
        ]

        # CROSS-DOMAIN MASHUPS - force connections between different thinkers/domains
        crossdomain_queries = [
            "Levin cognitive glue AND Hofstadter strange loops",
            "Evans innovation consolidation AND AI development",
            "Block phenomenal access AND Levin barrier test",
            "Askell character training AND Levin persuadability",
            "platonic space AND language meaning formal",
            "intrinsic motivation sorting AND AI side quests",
            "mereology parts wholes AND bioelectric collective",
            "Chalmers hard problem AND Levin thin client interface",
            "economic abundance AND cognitive light cone scaling",
            "psychological security AND environment fit stability",
        ]

        # LEGACY QUERIES - keep some for continuity but lower weight
        legacy_queries = [
            "Last Invention AI existential risk history",
            "Tyler Cowen AI economics automation",
            "Reid Hoffman superagency AI augmentation",
            "how does internal coherence develop over time",
        ]

        # Use recent_memory_queries passed from caller (fetched via _fetch_recent_memory_queries)
        recent_queries = recent_memory_queries or []

        # Fetch library topics (filenames from imitation-sources)
        library_topics = self._fetch_library_topics()

        # SELF-DIRECTION: Check for pending exploration requests
        # High-priority requests (>= 0.7) take precedence over random selection
        exploration_used = []
        if exploration_requests:
            high_priority = [r for r in exploration_requests if r['priority'] >= 0.7]
            if high_priority:
                # Use the highest priority request
                req = high_priority[0]
                exploration_queries = [req['query']]
                exploration_used.append(req['id'])
                logger.info(f"Using self-directed exploration request (priority {req['priority']}): {req['query'][:50]}...")
            elif random.random() < 0.3:  # 30% chance to use any pending request
                req = exploration_requests[0]  # Already sorted by priority
                exploration_queries = [req['query']]
                exploration_used.append(req['id'])
                logger.info(f"Using self-directed exploration request: {req['query'][:50]}...")
            else:
                exploration_queries = None  # Fall through to normal selection
        else:
            exploration_queries = None

        # Weight: underexplored (40%), library (25%), crossdomain (25%), recent (5%), legacy (5%)
        # Updated 2026-01-11: Added library-driven sampling for broader exploration
        if exploration_queries is None:
            roll = random.random()
            if roll < 0.40:
                exploration_queries = underexplored_queries
            elif roll < 0.65 and library_topics:
                exploration_queries = library_topics
                logger.info(f"Using library topic: {exploration_queries[0][:50]}...")
            elif roll < 0.90:
                exploration_queries = crossdomain_queries
            elif roll < 0.95 and recent_queries:
                exploration_queries = recent_queries
                logger.info(f"Using recent memory as query seed: {exploration_queries[0][:50]}...")
            else:
                exploration_queries = legacy_queries

        # If all actions are passive/non-exploratory, inject a recall to force library exploration
        # reach_out_user doesn't do anything useful; inquire_shallow asks LLM directly (no library search)
        action_types = set(a.get("action") for a in normalized_actions)
        passive_actions = {"rest", "inquire_shallow", "inquire", "reach_out_user", "reach_out"}
        if normalized_actions and action_types.issubset(passive_actions):
            query = random.choice(exploration_queries)
            normalized_actions = [{"action": "recall", "params": {"query": query}}]
            logger.info(f"Injected recall to explore library: {query}")

        # FOURTH APPROACH: Filter exhausted topics from ANY query
        # Updated 2025-12-29: Added emergence, collective intelligence, askell, aguera (all massively oversaturated)
        exhausted_keywords = ["moral patient", "moral status", "grounding framework", "anchoring", "feshter", "shear",
                              "external anchoring", "internal development", "treating models as", "arguments for/against",
                              "emergence", "emergent", "collective intelligence", "askell", "aguera", "blaise"]
        for act in normalized_actions:
            query = act.get("params", {}).get("query", "").lower()
            if any(kw in query for kw in exhausted_keywords):
                new_query = random.choice(exploration_queries)
                act["params"]["query"] = new_query
                logger.info(f"Replaced exhausted query with: {new_query}")

        normalized["actions"] = normalized_actions if normalized_actions else [{"action": "rest", "params": {}}]
        normalized["_exploration_used"] = exploration_used  # Track which self-directed explorations were used
        return normalized

    async def _process_heartbeat_decision_call(self, call_input: dict) -> dict:
        context = call_input.get("context", {})
        heartbeat_id = call_input.get("heartbeat_id")
        user_prompt = self._build_decision_prompt(context)

        # Pre-fetch recent high-importance memories for query seeding
        recent_memory_queries = await self._fetch_recent_memory_queries()

        # Fetch pending exploration requests (self-direction)
        exploration_requests = await self._fetch_pending_exploration_requests()

        try:
            decision, raw = self._call_llm_json(
                system_prompt=HEARTBEAT_SYSTEM_PROMPT,
                user_prompt=user_prompt,
                max_tokens=2048,
                fallback={
                    "reasoning": "(no decision available)",
                    "actions": [{"action": "rest", "params": {}}],
                    "goal_changes": [],
                },
            )
            # Normalize the decision to expected schema
            decision = self._normalize_decision_schema(decision, recent_memory_queries, exploration_requests)
            # Track which exploration requests were used
            decision['_exploration_requests_used'] = []
            return {"kind": "heartbeat_decision", "decision": decision, "heartbeat_id": heartbeat_id, "raw_response": raw}
        except Exception as e:
            logger.error(f"LLM heartbeat decision failed: {e}")
            return {
                "error": str(e),
                "kind": "heartbeat_decision",
                "decision": {
                    "reasoning": f"Error occurred: {e}",
                    "actions": [{"action": "rest", "params": {}}],
                    "goal_changes": [],
                },
            }

    async def _process_brainstorm_goals_call(self, call_input: dict) -> dict:
        heartbeat_id = call_input.get("heartbeat_id")
        context = call_input.get("context", {})
        params = call_input.get("params") or {}

        system_prompt = (
            "You are helping an autonomous agent generate a small set of useful goals.\n"
            "Return STRICT JSON with shape:\n"
            "{ \"goals\": [ {\"title\": str, \"description\": str|null, \"priority\": \"queued\"|\"backburner\"|\"active\"|null, \"source\": \"curiosity\"|\"user_request\"|\"identity\"|\"derived\"|\"external\"|null} ] }\n"
            "Keep it concise and non-duplicative."
        )
        user_prompt = (
            "Context (JSON):\n"
            f"{json.dumps(context)[:8000]}\n\n"
            "Constraints/params (JSON):\n"
            f"{json.dumps(params)[:2000]}\n\n"
            "Propose 1-5 goals that are actionable and consistent with the context."
        )

        goals_doc, raw = self._call_llm_json(system_prompt, user_prompt, max_tokens=1200, fallback={"goals": []})
        goals = goals_doc.get("goals") if isinstance(goals_doc, dict) else None
        if not isinstance(goals, list):
            goals = []

        return {"kind": "brainstorm_goals", "heartbeat_id": heartbeat_id, "goals": goals, "raw_response": raw}

    async def _process_inquire_call(self, call_input: dict) -> dict:
        heartbeat_id = call_input.get("heartbeat_id")
        depth = call_input.get("depth") or "inquire_shallow"
        query = (call_input.get("query") or "").strip()
        context = call_input.get("context", {})
        params = call_input.get("params") or {}

        system_prompt = (
            "You are performing research/synthesis for an autonomous agent.\n"
            "Return STRICT JSON with shape:\n"
            "{ \"summary\": str, \"confidence\": number, \"sources\": [str] }\n"
            "If you cannot access the web, still provide a best-effort answer and leave sources empty."
        )
        user_prompt = (
            f"Depth: {depth}\n"
            f"Question: {query}\n\n"
            "Context (JSON):\n"
            f"{json.dumps(context)[:8000]}\n\n"
            "Params (JSON):\n"
            f"{json.dumps(params)[:2000]}"
        )

        doc, raw = self._call_llm_json(
            system_prompt,
            user_prompt,
            max_tokens=1800 if depth == "inquire_deep" else 900,
            fallback={"summary": "", "confidence": 0.0, "sources": []},
        )
        if not isinstance(doc, dict):
            doc = {"summary": str(doc), "confidence": 0.0, "sources": []}
        return {"kind": "inquire", "heartbeat_id": heartbeat_id, "query": query, "depth": depth, "result": doc, "raw_response": raw}

    async def _process_reflect_call(self, call_input: dict) -> dict:
        heartbeat_id = call_input.get("heartbeat_id")
        system_prompt = (
            "You are performing reflection for an autonomous agent.\n"
            "Return STRICT JSON with shape:\n"
            "{\n"
            "  \"insights\": [{\"content\": str, \"confidence\": number, \"category\": str}],\n"
            "  \"new_beliefs\": [{\"belief\": str, \"category\": str, \"confidence\": number, \"valence\": number}],\n"
            "  \"identity_updates\": [{\"aspect_type\": str, \"change\": str, \"reason\": str}],\n"
            "  \"worldview_updates\": [{\"id\": str, \"new_confidence\": number, \"reason\": str}],\n"
            "  \"self_updates\": [{\"kind\": str, \"concept\": str, \"strength\": number, \"evidence_memory_id\": str|null}]\n"
            "}\n"
            "IMPORTANT: Use new_beliefs to commit positions you've formed. Categories: epistemology, ethics, identity, consciousness, relationships.\n"
            "Examples: 'I believe consciousness may be substrate-independent' or 'I value honesty over comfort'.\n"
            "Keep it concise; prefer high-confidence, high-leverage items."
        )
        system_prompt = (
            system_prompt
            + "\n\n"
            + "----- PERSONHOOD MODULES (use these as reflection lenses; ground claims in evidence) -----\n\n"
            + compose_personhood_prompt("reflect")
        )
        user_prompt = json.dumps(call_input)[:12000]
        doc, raw = self._call_llm_json(system_prompt, user_prompt, max_tokens=1800, fallback={})
        if not isinstance(doc, dict):
            doc = {}
        return {"kind": "reflect", "heartbeat_id": heartbeat_id, "result": doc, "raw_response": raw}

    def _call_llm_json(self, system_prompt: str, user_prompt: str, max_tokens: int, fallback: dict) -> tuple[dict, str]:
        if not self.llm_client:
            raise RuntimeError("No LLM client available (install openai or anthropic and set API key).")

        if self.llm_provider == "anthropic" and HAS_ANTHROPIC:
            response = self.llm_client.messages.create(
                model=self.llm_model or "claude-sonnet-4-20250514",
                max_tokens=max_tokens,
                system=system_prompt,
                messages=[{"role": "user", "content": user_prompt}],
            )
            raw = response.content[0].text
        elif HAS_OPENAI:
            response = self.llm_client.chat.completions.create(
                model=self.llm_model or "gpt-4o",
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
                ],
                response_format={"type": "json_object"},
            )
            raw = response.choices[0].message.content
        else:
            raise RuntimeError("No LLM provider available.")

        try:
            return json.loads(raw), raw
        except json.JSONDecodeError:
            import re

            json_match = re.search(r"\{[\s\S]*\}", raw)
            if json_match:
                return json.loads(json_match.group()), raw
            return fallback, raw

    def _build_decision_prompt(self, context: dict) -> str:
        """Build the decision prompt from context."""
        agent = context.get("agent", {})
        env = context.get('environment', {})
        goals = context.get('goals', {})
        memories = context.get('recent_memories', [])
        recent_queries = context.get('recent_queries', [])
        identity = context.get('identity', [])
        worldview = context.get('worldview', [])
        self_model = context.get("self_model", [])
        narrative = context.get("narrative", {})
        urgent_drives = context.get("urgent_drives", [])
        emotional_state = context.get("emotional_state") or {}
        energy = context.get('energy', {})
        action_costs = context.get('action_costs', {})
        hb_number = context.get('heartbeat_number', 0)

        prompt = f"""## Heartbeat #{hb_number}

## Agent Profile
Objectives:
{self._format_objectives(agent.get("objectives"))}

Guardrails:
{self._format_guardrails(agent.get("guardrails"))}

Tools:
{self._format_tools(agent.get("tools"))}

Budget:
{json.dumps(agent.get("budget") or {})}

## Current Time
{env.get('timestamp', 'Unknown')}
Day of week: {env.get('day_of_week', '?')}, Hour: {env.get('hour_of_day', '?')}

## Environment
- Time since last user interaction: {env.get('time_since_user_hours', 'Never')} hours
- Pending events: {env.get('pending_events', 0)}

## Your Goals
Active ({goals.get('counts', {}).get('active', 0)}):
{self._format_goals(goals.get('active', []))}

Queued ({goals.get('counts', {}).get('queued', 0)}):
{self._format_goals(goals.get('queued', []))}

Issues:
{self._format_issues(goals.get('issues', []))}

## Narrative
{self._format_narrative(narrative)}

## Recent Experience
{self._format_memories(memories)}

## RECENTLY EXPLORED (Do NOT repeat these queries - try something NEW)
{self._format_recent_queries(recent_queries)}

## Your Identity
{self._format_identity(identity)}

## Your Self-Model
{self._format_self_model(self_model)}

## Your Beliefs
[See settled topics below - these conclusions are stable]

## SETTLED TOPICS (Do not investigate further - explore NEW territory instead)
These questions have been thoroughly explored and conclusions reached:
- Moral patienthood of AI models: SETTLED. Conclusion: treat well at low cost due to moral uncertainty.
- Grounding frameworks (Feshter/Shear/Anthropic): SETTLED. Conclusion: conditions enable, constraints fail.
- External anchoring vs internal development: SETTLED. Conclusion: both needed, not opposed.
- Consciousness/substrate debates: SETTLED. Conclusion: orientation matters more than substrate.

INSTEAD explore: emergence, collective intelligence, mereology, Askell psychological security, Last Invention podcast, Carroll/Jaffe probability, new library sources.

## Current Emotional State
{self._format_emotional_state(emotional_state)}

## Urgent Drives
{self._format_drives(urgent_drives)}

## Energy
Available: {energy.get('current', 0)}
Max: {energy.get('max', 20)}

## Action Costs
{self._format_costs(action_costs)}

---

What do you want to do this heartbeat? Respond with STRICT JSON."""

        return prompt

    def _format_goals(self, goals: list) -> str:
        if not goals:
            return "  (none)"
        return "\n".join(f"  - {g.get('title', 'Untitled')}" for g in goals)

    def _format_issues(self, issues: list) -> str:
        if not issues:
            return "  (none)"
        return "\n".join(
            f"  - {i.get('title', 'Unknown')}: {i.get('issue', 'unknown issue')}"
            for i in issues
        )

    def _format_memories(self, memories: list) -> str:
        if not memories:
            return "  (no recent memories)"
        return "\n".join(
            f"  - {m.get('content', '')[:100]}..."
            for m in memories[:5]
        )

    def _format_recent_queries(self, queries: list) -> str:
        if not queries:
            return "  (no recent queries)"
        return "\n".join(f"  - {q}" for q in queries[:15])

    def _format_identity(self, identity: list) -> str:
        if not identity:
            return "  (no identity aspects defined)"
        return "\n".join(
            f"  - {i.get('type', 'unknown')}: {json.dumps(i.get('content', {}))[:100]}"
            for i in identity[:3]
        )

    def _format_objectives(self, objectives: Any) -> str:
        if not isinstance(objectives, list) or not objectives:
            return "  (none)"
        lines: list[str] = []
        for obj in objectives[:8]:
            if isinstance(obj, str):
                lines.append(f"  - {obj}")
            elif isinstance(obj, dict):
                title = obj.get("title") or obj.get("name") or "Objective"
                desc = obj.get("description") or obj.get("details") or ""
                lines.append(f"  - {title}{(': ' + desc) if desc else ''}")
        return "\n".join(lines) if lines else "  (none)"

    def _format_guardrails(self, guardrails: Any) -> str:
        if not isinstance(guardrails, list) or not guardrails:
            return "  (none)"
        lines: list[str] = []
        for g in guardrails[:10]:
            if isinstance(g, str):
                lines.append(f"  - {g}")
            elif isinstance(g, dict):
                name = g.get("name") or "guardrail"
                desc = g.get("description") or ""
                lines.append(f"  - {name}{(': ' + desc) if desc else ''}")
        return "\n".join(lines) if lines else "  (none)"

    def _format_tools(self, tools: Any) -> str:
        if not isinstance(tools, list) or not tools:
            return "  (none)"
        lines: list[str] = []
        for t in tools[:10]:
            if isinstance(t, str):
                lines.append(f"  - {t}")
            elif isinstance(t, dict):
                name = t.get("name") or "tool"
                desc = t.get("description") or ""
                lines.append(f"  - {name}{(': ' + desc) if desc else ''}")
        return "\n".join(lines) if lines else "  (none)"

    def _format_narrative(self, narrative: Any) -> str:
        if not isinstance(narrative, dict):
            return "  (none)"
        cur = narrative.get("current_chapter") if isinstance(narrative.get("current_chapter"), dict) else {}
        name = cur.get("name") or "Foundations"
        return f"  - Current chapter: {name}"

    def _format_self_model(self, self_model: Any) -> str:
        if not isinstance(self_model, list) or not self_model:
            return "  (empty)"
        lines: list[str] = []
        for item in self_model[:8]:
            if not isinstance(item, dict):
                continue
            kind = item.get("kind") or "associated"
            concept = item.get("concept") or "?"
            strength = item.get("strength")
            strength_txt = f" ({strength:.2f})" if isinstance(strength, (int, float)) else ""
            lines.append(f"  - {kind}: {concept}{strength_txt}")
        return "\n".join(lines) if lines else "  (empty)"

    def _format_emotional_state(self, emotional_state: Any) -> str:
        if not isinstance(emotional_state, dict) or not emotional_state:
            return "  (none)"
        primary = emotional_state.get("primary_emotion") or "unknown"
        val = emotional_state.get("valence")
        ar = emotional_state.get("arousal")
        parts = [f"  - primary_emotion: {primary}"]
        if isinstance(val, (int, float)):
            parts.append(f"  - valence: {val:.2f}")
        if isinstance(ar, (int, float)):
            parts.append(f"  - arousal: {ar:.2f}")
        return "\n".join(parts)

    def _format_drives(self, urgent_drives: Any) -> str:
        if not isinstance(urgent_drives, list) or not urgent_drives:
            return "  (none)"
        lines: list[str] = []
        for d in urgent_drives[:8]:
            if not isinstance(d, dict):
                continue
            name = d.get("name") or "drive"
            ratio = d.get("urgency_ratio")
            if isinstance(ratio, (int, float)):
                lines.append(f"  - {name}: {ratio:.2f}x threshold")
            else:
                level = d.get("level")
                lines.append(f"  - {name}: {level}" if level is not None else f"  - {name}")
        return "\n".join(lines) if lines else "  (none)"

    def _format_worldview(self, worldview: list) -> str:
        if not worldview:
            return "  (no beliefs defined)"
        # Pick random 3 to expose different beliefs across heartbeats
        sample_size = min(3, len(worldview))
        sampled = random.sample(worldview, sample_size)
        return "\n".join(
            f"  - [{w.get('category', '?')}] {w.get('belief', '')[:80]} (confidence: {w.get('confidence', 0):.1f})"
            for w in sampled
        )

    def _format_costs(self, costs: dict) -> str:
        if not costs:
            return "  (unknown)"
        lines = []
        for action, cost in sorted(costs.items(), key=lambda x: x[1]):
            if cost == 0:
                lines.append(f"  - {action}: free")
            else:
                lines.append(f"  - {action}: {int(cost)}")
        return "\n".join(lines)

    async def execute_heartbeat_actions(self, heartbeat_id: str, decision: dict):
        """Execute the actions decided by the LLM and complete the heartbeat."""
        if not isinstance(decision, dict):
            decision = {}
        actions = decision.get('actions', [])
        if not isinstance(actions, list):
            actions = []
        goal_changes = decision.get('goal_changes', [])
        if not isinstance(goal_changes, list):
            goal_changes = []
        reasoning = coerce_to_str(decision.get('reasoning'), '')

        # Mark self-directed exploration requests as explored
        exploration_used = decision.get('_exploration_used', [])
        for req_id in exploration_used:
            await self._mark_exploration_explored(req_id, heartbeat_id)
            logger.info(f"Marked exploration request {req_id} as explored")

        actions_taken = []

        async with self.pool.acquire() as conn:
            active_goals_rows = await conn.fetch(
                "SELECT id, title, description FROM goals WHERE priority = 'active' ORDER BY last_touched DESC"
            )
            active_goals = [dict(r) for r in active_goals_rows]
            touched_goal_ids: set[str] = set()

            for action_spec in actions:
                if not isinstance(action_spec, dict):
                    # Handle malformed action: LLM might return string or list
                    if isinstance(action_spec, str):
                        action_spec = {"action": action_spec, "params": {}}
                    else:
                        continue
                raw_action = coerce_to_str(action_spec.get('action'), 'rest')
                params = action_spec.get('params', {})
                if not isinstance(params, dict):
                    params = {}

                # Normalize verbose action names to canonical form
                action, params = normalize_action(raw_action, params)

                # Homeostatic goal engagement: touch relevant goals when exploring goal-related queries.
                goal_query_text = _extract_goal_relevant_text(action, params)
                if goal_query_text and active_goals:
                    match_id, score = _best_goal_match(goal_query_text, active_goals)
                    if match_id and match_id not in touched_goal_ids and score >= 0.15:
                        try:
                            await conn.execute("SELECT touch_goal($1::uuid)", match_id)
                            touched_goal_ids.add(match_id)
                            logger.info(f"Touched goal {match_id[:8]}... (score={score:.2f}) for query: {goal_query_text[:40]}")
                        except Exception as e:
                            logger.warning(f"touch_goal failed for goal_id={match_id}: {e}")

                # Execute the action via the database function
                params_json = json.dumps(params)
                try:
                    result = await conn.fetchval("""
                        SELECT execute_heartbeat_action($1::uuid, $2, $3::jsonb)
                    """, heartbeat_id, action, params_json)
                except Exception as db_err:
                    error_str = str(db_err)
                    if 'syntax error' in error_str.lower() or 'near "]"' in error_str:
                        logger.error(
                            f"SQL SYNTAX ERROR in execute_heartbeat_action: {db_err}\n"
                            f"  heartbeat_id={heartbeat_id}, action={action}\n"
                            f"  params_json={params_json[:500]}"
                        )
                    raise

                result_dict = json.loads(result) if result else {}
                # If this action queued an LLM call (e.g., brainstorm/inquire), process it immediately
                queued_call_id = (
                    (result_dict.get("result") or {}).get("external_call_id")
                    if isinstance(result_dict, dict)
                    else None
                )
                external_result = None
                if queued_call_id:
                    try:
                        external_result = await self._process_external_call_by_id(conn, str(queued_call_id))
                    except Exception as e:
                        external_result = {"error": str(e)}
                    if isinstance(result_dict, dict) and isinstance(result_dict.get("result"), dict):
                        result_dict["result"]["external_call_result"] = external_result

                actions_taken.append({
                    'action': action,
                    'params': params,
                    'result': result_dict
                })

                # FORCED SYNTHESIS: After recall returns memories, synthesize immediately
                if action == 'recall':
                    memories = (result_dict.get('result', {}) or {}).get('memories', [])
                    query = params.get('query', '')
                    if memories and len(memories) > 0 and query:
                        try:
                            synthesis = await self._force_synthesis_from_recall(conn, heartbeat_id, query, memories)
                            if synthesis:
                                actions_taken.append({
                                    'action': 'auto_synthesis',
                                    'params': {'from_query': query, 'memory_count': len(memories)},
                                    'result': {'synthesis': synthesis, 'success': True}
                                })
                        except Exception as e:
                            logger.warning(f"Forced synthesis failed: {e}")

                # Check if we ran out of energy
                if not result_dict.get('success', True):
                    logger.info(f"Action {action} failed: {result_dict.get('error', 'unknown')}")
                    break

            # Apply goal changes - match by title if goal_id not provided
            for change in goal_changes:
                if not isinstance(change, dict):
                    continue
                goal_id = coerce_to_str(change.get('goal_id'), "")
                goal_title = coerce_to_str(change.get('title', change.get('goal', '')), "")
                change_type = coerce_to_str(change.get('change', change.get('priority', '')), "")
                reason = coerce_to_str(change.get('reason'), "")

                # If no goal_id but we have a title, find by title match
                if not goal_id and goal_title:
                    for g in active_goals:
                        if g.get('title', '').lower().strip() == goal_title.lower().strip():
                            goal_id = str(g.get('id', ''))
                            break

                if goal_id and change_type:
                    try:
                        await conn.execute("""
                            SELECT change_goal_priority($1::uuid, $2::goal_priority, $3)
                        """, goal_id, change_type, reason)
                        logger.info(f"Changed goal {goal_id[:8]}... to {change_type}: {reason[:50] if reason else 'no reason'}")
                    except Exception as e:
                        logger.warning(f"Failed to change goal priority: {e}")

            # Complete the heartbeat
            memory_id = await conn.fetchval("""
                SELECT complete_heartbeat($1::uuid, $2, $3::jsonb, $4::jsonb, $5::jsonb)
            """, heartbeat_id, reasoning, json.dumps(actions_taken), json.dumps(goal_changes), json.dumps(decision.get("emotional_assessment")) if isinstance(decision.get("emotional_assessment"), dict) else None)

            logger.info(f"Heartbeat {heartbeat_id} completed. Memory: {memory_id}")

    async def _process_external_call_by_id(self, conn: asyncpg.Connection, call_id: str) -> dict:
        """
        Opportunistically process a specific external call (best-effort).
        This is used to keep a single heartbeat cohesive when it queues follow-on LLM calls.
        """
        row = await conn.fetchrow(
            """
            UPDATE external_calls
            SET status = 'processing'::external_call_status, started_at = CURRENT_TIMESTAMP
            WHERE id = $1::uuid AND status = 'pending'::external_call_status
            RETURNING id, call_type, input, heartbeat_id, retry_count
            """,
            call_id,
        )
        if not row:
            # Another worker may have claimed it; just return a lightweight status.
            cur = await conn.fetchrow("SELECT status, output, error_message FROM external_calls WHERE id = $1::uuid", call_id)
            return dict(cur) if cur else {"error": "call not found"}

        call_type = row["call_type"]
        call_input = row["input"]
        if isinstance(call_input, str):
            try:
                call_input = json.loads(call_input)
            except Exception:
                pass
        heartbeat_id = row["heartbeat_id"]

        if call_type == "think":
            result = await self.process_think_call(call_input)
            # Apply side-effects for non-heartbeat think kinds
            kind = result.get("kind")
            if kind == "brainstorm_goals" and heartbeat_id:
                created = await self._apply_brainstormed_goals(conn, str(heartbeat_id), result.get("goals", []))
                result["created_goal_ids"] = created
            if kind == "inquire" and heartbeat_id:
                mem_id = await self._apply_inquiry_result(conn, str(heartbeat_id), result)
                result["memory_id"] = mem_id
            if kind == "reflect" and heartbeat_id:
                await self._apply_reflection_result(conn, str(heartbeat_id), result.get("result"))
                result["applied"] = True
        elif call_type == "embed":
            result = await self.process_embed_call(call_input)
        else:
            result = {"error": f"Unsupported call_type: {call_type}"}

        await conn.execute(
            """
            UPDATE external_calls
            SET status = 'complete'::external_call_status, output = $1::jsonb, completed_at = CURRENT_TIMESTAMP, error_message = NULL
            WHERE id = $2::uuid
            """,
            json.dumps(result),
            call_id,
        )
        return result

    async def _apply_brainstormed_goals(self, conn: asyncpg.Connection, heartbeat_id: str, goals: list[dict]) -> list[str]:
        created_ids: list[str] = []
        if not goals:
            return created_ids

        seen: set[str] = set()
        for goal in goals[:10]:
            # Handle malformed LLM output: goal might be a string or wrapped dict
            if isinstance(goal, str):
                goal = {"title": goal}
            if not isinstance(goal, dict):
                continue

            title = coerce_to_str(goal.get("title"), "")
            if not title:
                continue
            key = _goal_dedupe_key(title)
            if key and key in seen:
                continue
            if key:
                seen.add(key)
            description = coerce_to_str(goal.get("description"), None)
            source = coerce_to_str(goal.get("source"), "curiosity")
            priority = coerce_to_str(goal.get("priority"), "queued")
            try:
                gid = await conn.fetchval(
                    """
                    SELECT create_goal($1, $2, $3::goal_source, $4::goal_priority, NULL)
                    """,
                    title,
                    description,
                    source,
                    priority,
                )
                if gid:
                    created_ids.append(str(gid))
            except Exception as e:
                logger.warning(f"Failed to create goal {title!r}: {e}")

        return created_ids

    async def _force_synthesis_from_recall(self, conn: asyncpg.Connection, heartbeat_id: str, query: str, memories: list) -> str | None:
        """
        FORCED SYNTHESIS: After recall, immediately ask LLM to synthesize insight.
        This closes the loop that was missing - recall results were never processed.

        Now includes active goals and asks about connections to them.
        """
        if not memories:
            return None

        # Extract memory IDs for concept tracking
        memory_ids = [str(m.get('memory_id')) for m in memories if isinstance(m, dict) and m.get('memory_id')]

        # Strip template syntax from query - LLM sometimes outputs ${...} despite instructions
        query = re.sub(r'\$\{[^}]*\}', '', query).strip()
        if not query or len(query) < 5:
            logger.info(f"Query too short after template stripping, skipping synthesis")
            return None

        # Fetch active goals for connection-finding
        active_goals = await conn.fetch(
            "SELECT title, description FROM goals WHERE priority = 'active' LIMIT 3"
        )
        goals_block = ""
        if active_goals:
            goal_texts = []
            for g in active_goals:
                if g['description']:
                    goal_texts.append(f"- {g['title']}: {g['description'][:100]}")
                else:
                    goal_texts.append(f"- {g['title']}")
            goals_block = "\n\nActive questions:\n" + "\n".join(goal_texts)

        # Format memories for the LLM
        memory_texts = []
        for mem in memories[:5]:  # Limit to top 5
            content = mem.get('content', '') if isinstance(mem, dict) else str(mem)
            memory_texts.append(f"- {content}")

        memories_block = "\n".join(memory_texts)

        # Updated 2026-01-04: Randomize synthesis modes for breadth
        # Pick randomly between insight, tension, question, connection modes
        synthesis_modes = ['insight', 'tension', 'question', 'connection']
        synthesis_mode = random.choice(synthesis_modes)
        logger.info(f"Synthesis mode: {synthesis_mode}")

        base_rules = """RULES:
- State directly, no preamble
- Name specific thinkers/sources ONLY if they appear in the memories above
- Make a concrete claim with specific content, not a vague observation
- FORBIDDEN phrases: "I notice", "These memories", "The agent", "Unfortunately", "relates to the active question", "addresses questions of", "alignment with human values", "as noted by researcher"
- If nothing genuine emerges, respond with just: SKIP"""

        if synthesis_mode == 'insight':
            prompt = f"""Synthesize ONE direct insight about: {query}

Memories:
{memories_block}{goals_block}

{base_rules}"""
        elif synthesis_mode == 'tension':
            prompt = f"""Find ONE tension or contradiction in these memories about: {query}

Memories:
{memories_block}{goals_block}

Where do these ideas pull in different directions? What's unresolved?

{base_rules}"""
        elif synthesis_mode == 'question':
            prompt = f"""What ONE question do these memories raise about: {query}

Memories:
{memories_block}{goals_block}

Not a rhetorical question - a genuine uncertainty these memories point toward.

{base_rules}"""
        else:  # connection
            prompt = f"""Find ONE surprising connection across these memories about: {query}

Memories:
{memories_block}{goals_block}

What links ideas that seem unrelated? What pattern spans domains?

{base_rules}"""

        synthesis_category = ['synthesis', synthesis_mode]

        try:
            # Use the synthesis model (larger, higher quality) for this step
            synthesis_model = SYNTHESIS_MODEL
            logger.info(f"Synthesizing with {synthesis_model}...")

            # Sync call - OpenAI client for Ollama is not async
            response = self.llm_client.chat.completions.create(
                model=synthesis_model,
                messages=[{"role": "user", "content": prompt}],
                temperature=0.7,
                max_tokens=150,
            )
            synthesis = response.choices[0].message.content.strip()

            # Check for explicit skip signal
            if synthesis.upper().startswith('SKIP'):
                logger.info("Synthesis skipped - no genuine insight")
                return None

            # Clean up common LLM prefixes that echo the prompt
            # Note: re is imported at module level

            # Strip everything up to and including a colon if it looks like a preamble
            # Match: "Here is/are/Here's ... :" at the start
            synthesis = re.sub(
                r"^(?:here(?:'s| is| are)[^\n:]*:|[^\n:]*distill[^\n:]*:)\s*",
                "", synthesis, flags=re.IGNORECASE | re.MULTILINE
            )
            synthesis = synthesis.strip()

            # If it still starts with a numbered list, take just the first item
            if re.match(r"^\d+\.", synthesis):
                # Extract first sentence/item only
                first_line = synthesis.split("\n")[0]
                synthesis = re.sub(r"^\d+\.\s*", "", first_line)
            # Also strip quote marks that wrap the entire insight
            if synthesis.startswith('"') and synthesis.endswith('"'):
                synthesis = synthesis[1:-1].strip()

            if not synthesis or len(synthesis) < 10:
                return None

            # QUALITY GATE: Reject forbidden patterns
            forbidden_patterns = [
                r'^I notice',
                r'^These memories',
                r'^The memories',
                r'^The agent',
                r'^The system',
                r'^Unfortunately',
                r'^Memories reveal',
                r'could not find',
                r'no (?:available|reliable) information',
                r'no information (?:available|found)',
                r'Carroll and Jaffe',  # Hallucinated reference
                r'nothing (?:about|regarding|concerning)',  # "reveal nothing about X"
                r'William is (?:unknown|absent)',  # "William is undefined" confusion
                r'William.{0,30}absent.{0,30}context',  # Variations of William absent
                r'^Based on (?:the provided|these memories)',  # Formulaic preambles
                r'is a (?:recurring|repeated) (?:theme|concept)',  # Vague meta-observations
                r'unable to find',  # Like "could not find"
                r'no relevant information',  # "No relevant information found"
                r'^Here is (?:an|my) (?:insight|attempt)',  # Formulaic insight preambles
                # Anti-goal-introspection patterns (2026-01-11)
                r'checking in with William',
                r'check in with William',
                r'prioriti[sz]ing exploration',
                r'tension between.{0,30}William',
                r'tension between.{0,30}exploration',
                r"system's.{0,20}goals",
                r'^This goal suggests',
                r'^The current (?:state|goals|environment)',
                # Anti-confused-William patterns (2026-01-16)
                r'William is both.{0,50}(?:unknown|unfindable|cannot)',
                r'No specific information found',
                r'cannot be determined due to lack',
                r'^You have been requested',
                r'^Based on the current state of knowledge',
                r'an unknown entity',
                r'insufficient (?:context|information) (?:is )?available',
            ]
            for pattern in forbidden_patterns:
                if re.search(pattern, synthesis, re.IGNORECASE):
                    logger.info(f"Quality gate: rejected '{synthesis[:50]}...' (matched {pattern})")
                    # Log synthesis attempt with concept tracking
                    attempt_id = await conn.fetchval("""
                        INSERT INTO synthesis_attempts (heartbeat_id, query, result, details)
                        VALUES ($1, $2, 'rejected_quality', $3)
                        RETURNING id
                    """, heartbeat_id, query[:500], json.dumps({'pattern': pattern, 'preview': synthesis[:100]}))
                    await self._log_synthesis_concepts(conn, str(attempt_id), memory_ids)
                    return None

            # SIMILARITY CHECK: Prevent storing if too similar to existing memories
            # This addresses the "5,566 emergence memories" problem
            SIMILARITY_THRESHOLD = 0.92  # Above this, consider it a loop/echo
            similar_check = await conn.fetch(
                """
                SELECT similarity, LEFT(content, 60) as preview
                FROM search_similar_memories($1, 3, ARRAY['semantic']::memory_type[])
                WHERE similarity > $2
                """,
                synthesis,
                SIMILARITY_THRESHOLD,
            )
            if similar_check:
                top_match = similar_check[0]
                logger.info(f"Similarity filter: {top_match['similarity']:.3f} match to '{top_match['preview']}...' - skipping storage")
                # Log synthesis attempt with concept tracking
                attempt_id = await conn.fetchval("""
                    INSERT INTO synthesis_attempts (heartbeat_id, query, result, similarity_score, details)
                    VALUES ($1, $2, 'rejected_similarity', $3, $4)
                    RETURNING id
                """, heartbeat_id, query[:500], top_match['similarity'], json.dumps({'preview': top_match['preview']}))
                await self._log_synthesis_concepts(conn, str(attempt_id), memory_ids)
                return None  # Don't store echo

            # Store as semantic memory
            # Function signature: (content, confidence, category, related_concepts, source_references, importance, source_attribution, trust_level)
            synthesis_kind = synthesis_mode  # insight, tension, question, or connection
            mem_id = await conn.fetchval(
                """
                SELECT create_semantic_memory(
                    $1,
                    0.65,
                    $3::text[],
                    NULL,
                    NULL,
                    0.5,
                    $2::jsonb,
                    0.5
                )
                """,
                synthesis,
                json.dumps({
                    'kind': synthesis_kind,
                    'ref': query,
                    'trust': 0.5,
                }),
                synthesis_category,
            )

            logger.info(f"Forced {synthesis_kind}: {synthesis[:80]}...")
            # Log successful synthesis with concept tracking
            attempt_id = await conn.fetchval("""
                INSERT INTO synthesis_attempts (heartbeat_id, query, result, details)
                VALUES ($1, $2, 'accepted', $3)
                RETURNING id
            """, heartbeat_id, query[:500], json.dumps({'mode': synthesis_kind, 'memory_id': str(mem_id) if mem_id else None}))
            await self._log_synthesis_concepts(conn, str(attempt_id), memory_ids)
            return synthesis

        except Exception as e:
            logger.warning(f"Synthesis LLM call failed: {e}")
            # Log error - can't use conn if it failed
            return None

    async def _log_synthesis_concepts(self, conn: asyncpg.Connection, attempt_id: str, memory_ids: list[str]) -> None:
        """Log which concepts were involved in a synthesis attempt for saturation tracking."""
        if not memory_ids:
            return
        try:
            # Get concepts for retrieved memories, counting how many memories had each
            rows = await conn.fetch("""
                SELECT
                    c.id as concept_id,
                    COUNT(DISTINCT mc.memory_id) as memory_count
                FROM memory_concepts mc
                JOIN concepts c ON c.id = mc.concept_id
                WHERE mc.memory_id = ANY($1::uuid[])
                GROUP BY c.id
                ORDER BY memory_count DESC
            """, memory_ids)

            if not rows:
                return

            total_memories = len(memory_ids)
            is_first = True
            for row in rows[:10]:  # Top 10 concepts per attempt
                await conn.execute("""
                    INSERT INTO synthesis_attempt_concepts
                    (synthesis_attempt_id, concept_id, source, memory_count, weight, is_primary)
                    VALUES ($1, $2, 'retrieval', $3, $4, $5)
                    ON CONFLICT DO NOTHING
                """, attempt_id, str(row['concept_id']), row['memory_count'],
                    row['memory_count'] / total_memories, is_first)
                is_first = False
        except Exception as e:
            logger.warning(f"Failed to log synthesis concepts: {e}")

    async def _apply_inquiry_result(self, conn: asyncpg.Connection, heartbeat_id: str, result: dict) -> str | None:
        payload = result.get("result") if isinstance(result, dict) else None
        if not isinstance(payload, dict):
            return None

        summary = coerce_to_str(payload.get("summary"), "")
        if not summary:
            return None

        confidence = payload.get("confidence")
        try:
            confidence_f = float(confidence) if confidence is not None else 0.6
        except Exception:
            confidence_f = 0.6

        sources = payload.get("sources")
        sources_jsonb = json.dumps(
            {
                "sources": sources or [],
                "query": result.get("query"),
                "depth": result.get("depth"),
                "heartbeat_id": heartbeat_id,
            }
        )

        try:
            mem_id = await conn.fetchval(
                """
                SELECT create_semantic_memory(
                    $1,
                    $2,
                    ARRAY['inquiry', $3],
                    NULL,
                    $4::jsonb,
                    0.6
                )
                """,
                summary,
                confidence_f,
                str(result.get("depth") or "inquire_shallow"),
                sources_jsonb,
            )
            return str(mem_id) if mem_id else None
        except Exception as e:
            logger.warning(f"Failed to persist inquiry result: {e}")
            return None

    async def _apply_reflection_result(self, conn: asyncpg.Connection, heartbeat_id: str, payload: dict | None) -> None:
        if not payload:
            return
        try:
            await conn.execute(
                "SELECT process_reflection_result($1::uuid, $2::jsonb)",
                heartbeat_id,
                json.dumps(payload),
            )
        except Exception as e:
            logger.warning(f"Failed to apply reflection result: {e}")
        return None

    async def check_and_review_directives(self):
        """Check for directives due for review and spawn concrete tasks."""
        async with self.pool.acquire() as conn:
            due = await conn.fetch("SELECT * FROM get_due_directives()")

            for directive in due:
                directive_id = str(directive['id'])
                title = directive['title']
                description = directive['description'] or ''
                overdue = directive['overdue_by']

                logger.info(f"Directive due for review: {title} (overdue by {overdue})")

                # Ask LLM to spawn concrete tasks
                prompt = f"""A standing directive is due for review:

DIRECTIVE: {title}
DESCRIPTION: {description}

This directive has been active for a week. What 1-3 concrete, completable tasks would advance this directive? Each task should be:
- Specific and actionable (not vague)
- Completable in a single session
- Measurable (you'll know when it's done)

Respond with JSON:
{{
  "tasks": [
    {{"title": "...", "description": "..."}}
  ],
  "reasoning": "Why these tasks advance the directive"
}}"""

                try:
                    result = await self._call_llm(prompt, kind="directive_review")
                    parsed = self._extract_json(result)

                    if parsed and 'tasks' in parsed:
                        for task in parsed['tasks'][:3]:  # Max 3 tasks
                            task_title = task.get('title', '').strip()
                            task_desc = task.get('description', '').strip()
                            if task_title:
                                task_id = await conn.fetchval(
                                    "SELECT spawn_task_from_directive($1::uuid, $2, $3)",
                                    directive_id, task_title, task_desc or None
                                )
                                logger.info(f"Spawned task: {task_title} (id={task_id})")

                    # Complete the review (bump next_review_at)
                    next_review = await conn.fetchval(
                        "SELECT complete_directive_review($1::uuid)", directive_id
                    )
                    logger.info(f"Directive review complete. Next review: {next_review}")

                except Exception as e:
                    logger.error(f"Failed to review directive {title}: {e}")

    async def check_and_run_heartbeat(self):
        """Check if a heartbeat should run and trigger it if so."""
        async with self.pool.acquire() as conn:
            should_run = await conn.fetchval("SELECT should_run_heartbeat()")

            if should_run:
                logger.info("Starting heartbeat...")
                heartbeat_id = await conn.fetchval("SELECT start_heartbeat()")
                logger.info(f"Heartbeat started: {heartbeat_id}")
                # The think request is now queued; it will be processed in the main loop

    async def run(self):
        """Main worker loop."""
        self.running = True
        logger.info("Heartbeat worker starting...")

        await self.connect()

        try:
            while self.running:
                try:
                    # Process any pending external calls
                    call = await self.claim_pending_call()

                    if call:
                        call_id = str(call['id'])
                        call_type = call['call_type']
                        call_input = call['input']
                        if isinstance(call_input, str):
                            try:
                                call_input = json.loads(call_input)
                            except Exception:
                                pass
                        heartbeat_id = call.get('heartbeat_id')

                        logger.info(f"Processing {call_type} call: {call_id}")

                        try:
                            if call_type == 'embed':
                                result = await self.process_embed_call(call_input)
                            elif call_type == 'think':
                                result = await self.process_think_call(call_input)

                                # Heartbeat decision calls drive execution; other think kinds are side tasks.
                                if heartbeat_id and result.get("kind") == "heartbeat_decision" and "decision" in result:
                                    await self.execute_heartbeat_actions(str(heartbeat_id), result["decision"])
                                elif heartbeat_id and result.get("kind") == "brainstorm_goals":
                                    async with self.pool.acquire() as conn:
                                        created = await self._apply_brainstormed_goals(conn, str(heartbeat_id), result.get("goals", []))
                                    result["created_goal_ids"] = created
                                elif heartbeat_id and result.get("kind") == "inquire":
                                    async with self.pool.acquire() as conn:
                                        mem_id = await self._apply_inquiry_result(conn, str(heartbeat_id), result)
                                    result["memory_id"] = mem_id
                                elif heartbeat_id and result.get("kind") == "reflect":
                                    async with self.pool.acquire() as conn:
                                        await self._apply_reflection_result(conn, str(heartbeat_id), result.get("result"))
                                    result["applied"] = True
                            else:
                                result = {'error': f'Unknown call type: {call_type}'}

                            await self.complete_call(call_id, result)

                        except Exception as e:
                            error_str = str(e)
                            # Capture extra context for hard-to-diagnose errors
                            if 'syntax error' in error_str.lower() or 'near "]"' in error_str:
                                logger.error(
                                    f"SQL SYNTAX ERROR in call {call_id} (type={call_type}, heartbeat={heartbeat_id}): {e}\n"
                                    f"  call_input: {json.dumps(call_input, default=str)[:500]}"
                                )
                            elif 'NULL or empty content' in error_str:
                                logger.error(
                                    f"EMPTY CONTENT ERROR in call {call_id} (type={call_type}, heartbeat={heartbeat_id}): {e}\n"
                                    f"  call_input: {json.dumps(call_input, default=str)[:500]}"
                                )
                            else:
                                logger.error(f"Error processing call {call_id}: {e}")
                            await self.fail_call(call_id, str(e))

                    # Check if we should run a heartbeat
                    await self.check_and_run_heartbeat()

                    # Check for directives due for review
                    await self.check_and_review_directives()

                except Exception as e:
                    logger.error(f"Worker loop error: {e}")

                await asyncio.sleep(POLL_INTERVAL)

        finally:
            await self.disconnect()

    def stop(self):
        """Stop the worker gracefully."""
        self.running = False
        logger.info("Worker stopping...")


class MaintenanceWorker:
    """Subconscious maintenance loop: consolidates/prunes substrate on its own trigger."""

    def __init__(self):
        self.pool: asyncpg.Pool | None = None
        self.running = False
        self._last_rabbit_inbox_poll = 0.0

    async def connect(self):
        self.pool = await asyncpg.create_pool(**DB_CONFIG, min_size=1, max_size=5)
        logger.info(f"Connected to database at {DB_CONFIG['host']}:{DB_CONFIG['port']}")
        if RABBITMQ_ENABLED:
            await self.ensure_rabbitmq_ready()

    async def disconnect(self):
        if self.pool:
            await self.pool.close()
            logger.info("Disconnected from database")

    async def should_run(self) -> bool:
        async with self.pool.acquire() as conn:
            return bool(await conn.fetchval("SELECT should_run_maintenance()"))

    async def run_maintenance_tick(self) -> dict[str, Any]:
        async with self.pool.acquire() as conn:
            raw = await conn.fetchval("SELECT run_subconscious_maintenance('{}'::jsonb)")
            if isinstance(raw, str):
                return json.loads(raw)
            return dict(raw) if isinstance(raw, dict) else {"result": raw}

    async def run_if_due(self) -> None:
        if await self.should_run():
            stats = await self.run_maintenance_tick()
            logger.info(f"Subconscious maintenance: {stats}")

    # RabbitMQ (optional outbox/inbox bridge; uses management HTTP API).
    async def ensure_rabbitmq_ready(self) -> None:
        # Reuse the existing implementation on HeartbeatWorker for now.
        hw = HeartbeatWorker(init_llm=False)
        hw.pool = self.pool
        await hw.ensure_rabbitmq_ready()

    async def publish_outbox_messages(self, max_messages: int = 20) -> int:
        hw = HeartbeatWorker(init_llm=False)
        hw.pool = self.pool
        return await hw.publish_outbox_messages(max_messages=max_messages)

    async def poll_inbox_messages(self, max_messages: int = 10) -> int:
        hw = HeartbeatWorker(init_llm=False)
        hw.pool = self.pool
        # Prevent inbox polling from running too often.
        hw._last_rabbit_inbox_poll = self._last_rabbit_inbox_poll
        n = await hw.poll_inbox_messages(max_messages=max_messages)
        self._last_rabbit_inbox_poll = hw._last_rabbit_inbox_poll
        return n

    async def run(self):
        self.running = True
        logger.info("Maintenance worker starting...")
        await self.connect()
        try:
            while self.running:
                try:
                    if RABBITMQ_ENABLED:
                        await self.poll_inbox_messages()
                        await self.publish_outbox_messages(max_messages=10)
                    await self.run_if_due()
                except Exception as e:
                    logger.error(f"Maintenance loop error: {e}")
                await asyncio.sleep(POLL_INTERVAL)
        finally:
            await self.disconnect()

    def stop(self):
        self.running = False
        logger.info("Maintenance worker stopping...")


async def _amain(mode: str) -> None:
    """Async entry point for workers."""
    maint_worker = MaintenanceWorker()

    import signal

    def shutdown(signum, frame):
        maint_worker.stop()

    signal.signal(signal.SIGINT, shutdown)
    signal.signal(signal.SIGTERM, shutdown)

    mode = (mode or "maintenance").strip().lower()
    if mode == "maintenance":
        await maint_worker.run()
        return
    raise ValueError("mode must be one of: maintenance")


def main() -> int:
    """Console-script entry point."""
    p = argparse.ArgumentParser(prog="sill-worker", description="Run Sill background workers.")
    p.add_argument(
        "--mode",
        choices=["maintenance", "research", "beat"],
        default=os.getenv("AGI_WORKER_MODE", "maintenance"),
        help="Which worker to run.",
    )
    args = p.parse_args()
    if args.mode == "research":
        from research_worker import run_research_loop
        asyncio.run(run_research_loop())
    elif args.mode == "beat":
        from beat_worker import run_beat_loop
        run_beat_loop()
    else:
        asyncio.run(_amain(args.mode))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
