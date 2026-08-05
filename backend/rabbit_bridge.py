#!/usr/bin/env python3
"""RabbitMQ outbox/inbox bridge for the maintenance worker.

Extracted from the former ``HeartbeatWorker`` class in ``worker.py`` (Sill
v0.2.0, Task 6). That class carried ~1,800 lines of heartbeat-only LLM
decision machinery (system prompt, provider clients, goal-matching helpers)
that the maintenance worker never used — it only ever reused three methods
for its RabbitMQ integration:

    ensure_rabbitmq_ready()     — declare the outbox/inbox queues if missing
    publish_outbox_messages()   — drain `outbox_messages` rows to the queue
    poll_inbox_messages()       — pull inbox messages into working memory

Those three methods (plus their genuine dependencies — the connection
settings below and the `_last_rabbit_inbox_poll` throttle) now live here as
a small class over an asyncpg pool. No LLM client, no decision loop, no
heartbeat state.

The maintenance_worker Compose service ships with RABBITMQ_ENABLED=1 by
default, so this bridge is live on every default install, not optional
integration plumbing that happens to be unused.
"""

import asyncio
import json
import logging
import os
import time
from typing import Any

import requests

logger = logging.getLogger("rabbit_bridge")

# RabbitMQ (optional outbox/inbox bridge; uses management HTTP API).
RABBITMQ_ENABLED = os.getenv("RABBITMQ_ENABLED", "0").lower() in {"1", "true", "yes", "on"}
RABBITMQ_MANAGEMENT_URL = os.getenv("RABBITMQ_MANAGEMENT_URL", "http://rabbitmq:15672").rstrip("/")
RABBITMQ_USER = os.getenv("RABBITMQ_USER", "sill")
RABBITMQ_PASSWORD = os.getenv("RABBITMQ_PASSWORD", "sill_password")
RABBITMQ_VHOST = os.getenv("RABBITMQ_VHOST", "/")
RABBITMQ_OUTBOX_QUEUE = os.getenv("RABBITMQ_OUTBOX_QUEUE", "sill.outbox")
RABBITMQ_INBOX_QUEUE = os.getenv("RABBITMQ_INBOX_QUEUE", "sill.inbox")
RABBITMQ_POLL_INBOX_EVERY = float(os.getenv("RABBITMQ_POLL_INBOX_EVERY", 1.0))


class RabbitBridge:
    """Bridges `outbox_messages`/inbox rows to RabbitMQ over its management HTTP API.

    Takes an asyncpg pool directly (no `connect()`/`disconnect()` of its
    own — the caller owns the pool's lifecycle).
    """

    def __init__(self, pool):
        self.pool = pool
        self._last_rabbit_inbox_poll = 0.0

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
