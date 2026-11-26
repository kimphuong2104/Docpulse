"""Outbox trong cùng transaction với lần lưu trang, worker index sau đó.

Khóa chống trùng là page_id + version + content hash. Lỗi tạm thời được hẹn
lại bằng exponential backoff có jitter. Hết số lần thử thì event chuyển sang dead.
"""

import hashlib
import json
import random
import re
import time
import uuid
from typing import Any

from psycopg.types.json import Json

from app.db import connect
from app.security import redact


def text_from_blocks(title: str, blocks: list[dict[str, Any]]) -> str:
    lines = [title.strip()]
    for block in blocks:
        content = block.get("content") if isinstance(block.get("content"), dict) else {}
        if not isinstance(content, dict):
            continue
        if content.get("source") == "tasks":
            continue
        text = str(content.get("text") or "").strip()
        if text:
            lines.append(text)
        filename = str(content.get("filename") or "").strip()
        if filename and filename not in text:
            lines.append(filename)
        for row in content.get("rows") or []:
            if isinstance(row, dict) and row.get("title"):
                lines.append(f"{row.get('title')} {row.get('status') or ''}".strip())
    return "\n".join(line for line in lines if line)[:20000]


def content_hash(plain: str) -> str:
    return hashlib.sha256(plain.encode()).hexdigest()


def enqueue_index(
    cur,
    *,
    page_id: str,
    workspace_id: str | None,
    version: int,
    plain: str,
    trace_id: str,
    actor_id: str | None = None,
) -> None:
    if not workspace_id:
        return
    digest = content_hash(plain)
    key = f"page.index:{page_id}:{version}:{digest[:16]}"
    cur.execute(
        """
        INSERT INTO outbox_events (
            event_type, aggregate_id, idempotency_key, payload, trace_id
        )
        VALUES ('page.index.requested', %s, %s, %s, %s)
        ON CONFLICT (idempotency_key) DO NOTHING
        """,
        (
            page_id,
            key,
            Json({"page_id": page_id, "workspace_id": workspace_id, "version": version, "content_hash": digest}),
            trace_id,
        ),
    )
    cur.execute(
        """
        INSERT INTO page_index_state (page_id, workspace_id, version, content_hash, status, vector_ready, error, chunk_count)
        VALUES (%s::uuid, %s::uuid, %s, %s, 'pending', FALSE, NULL, 0)
        ON CONFLICT (page_id) DO UPDATE SET
            version = EXCLUDED.version,
            content_hash = EXCLUDED.content_hash,
            status = 'pending',
            vector_ready = FALSE,
            error = NULL,
            updated_at = CURRENT_TIMESTAMP
        """,
        (page_id, workspace_id, version, digest),
    )
    cur.execute(
        """
        INSERT INTO activity_events (workspace_id, actor_id, action, target, detail)
        VALUES (%s::uuid, %s::uuid, 'edited', %s, %s)
        """,
        (workspace_id, actor_id, page_id, f"version {version}"),
    )
    cur.execute("SELECT pg_notify('docpulse_page', %s)", (page_id,))


def beat(detail: str) -> None:
    with connect() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO worker_heartbeats (name, seen_at, detail)
                VALUES ('docpulse_worker', CURRENT_TIMESTAMP, %s)
                ON CONFLICT (name) DO UPDATE
                SET seen_at = CURRENT_TIMESTAMP, detail = EXCLUDED.detail
                """,
                (detail[:200],),
            )
        conn.commit()


def recover_stuck_events() -> int:
    with connect() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                UPDATE outbox_events
                SET status = 'failed',
                    last_error = 'Worker dừng giữa chừng, chạy lại.',
                    next_run_at = CURRENT_TIMESTAMP,
                    updated_at = CURRENT_TIMESTAMP
                WHERE status = 'processing'
                  AND updated_at < CURRENT_TIMESTAMP - INTERVAL '10 minutes'
                """
            )
            count = cur.rowcount
        conn.commit()
    return count


def claim_event() -> dict[str, Any] | None:
    with connect() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                WITH next_event AS (
                    SELECT id
                    FROM outbox_events
                    WHERE status IN ('pending', 'failed')
                      AND next_run_at <= CURRENT_TIMESTAMP
                    ORDER BY next_run_at
                    FOR UPDATE SKIP LOCKED
                    LIMIT 1
                )
                UPDATE outbox_events AS event
                SET status = 'processing',
                    attempt = event.attempt + 1,
                    updated_at = CURRENT_TIMESTAMP
                FROM next_event
                WHERE event.id = next_event.id
                RETURNING event.id::text, event.aggregate_id, event.payload, event.attempt,
                          event.max_attempts, event.trace_id
                """
            )
            row = cur.fetchone()
        conn.commit()
    if not row:
        return None
    payload = row[2] if isinstance(row[2], dict) else json.loads(row[2])
    return {
        "id": row[0],
        "page_id": row[1],
        "payload": payload,
        "attempt": int(row[3]),
        "max_attempts": int(row[4]),
        "trace_id": row[5],
    }


def _chunks(text: str) -> list[str]:
    parts = [part.strip() for part in text.split("\n") if part.strip()]
    chunks: list[str] = []
    buffer = ""
    for part in parts:
        if buffer and len(buffer) + len(part) + 1 > 900:
            chunks.append(buffer)
            buffer = part
        else:
            buffer = f"{buffer}\n{part}".strip()
    if buffer:
        chunks.append(buffer)
    return chunks[:80] or ["Trang chưa có nội dung."]


def _load_plain(page_id: str) -> tuple[str, str, str] | None:
    with connect() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT title, workspace_id::text
                FROM workspace_pages
                WHERE id = %s::uuid
                """,
                (page_id,),
            )
            page = cur.fetchone()
            if not page or not page[1]:
                return None
            cur.execute(
                """
                SELECT block_type, content
                FROM workspace_blocks
                WHERE page_id = %s::uuid
                ORDER BY position
                """,
                (page_id,),
            )
            blocks = [{"type": row[0], "content": row[1] if isinstance(row[1], dict) else {}} for row in cur.fetchall()]
            cur.execute(
                """
                SELECT title, status FROM workspace_tasks
                WHERE workspace_id = %s::uuid
                ORDER BY created_at
                """,
                (page[1],),
            )
            tasks = cur.fetchall()
    plain = text_from_blocks(page[0] or "", blocks)
    if any((block["content"] or {}).get("source") == "tasks" for block in blocks):
        task_lines = "\n".join(f"{title} {status}" for title, status in tasks)
        plain = f"{plain}\n{task_lines}".strip()
    return page[1], plain, content_hash(plain)


def _write_chunks(page_id: str, workspace_id: str, version: int, digest: str, pieces: list[str]) -> list[int]:
    with connect() as conn:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM page_chunks WHERE page_id = %s::uuid", (page_id,))
            ids: list[int] = []
            for index, piece in enumerate(pieces):
                cur.execute(
                    """
                    INSERT INTO page_chunks (page_id, workspace_id, version, content_hash, chunk_index, content)
                    VALUES (%s::uuid, %s::uuid, %s, %s, %s, %s)
                    RETURNING id
                    """,
                    (page_id, workspace_id, version, digest, index, piece),
                )
                ids.append(int(cur.fetchone()[0]))
            cur.execute(
                """
                INSERT INTO page_index_state (
                    page_id, workspace_id, version, content_hash, status, vector_ready, error, chunk_count
                )
                VALUES (%s::uuid, %s::uuid, %s, %s, 'indexing', FALSE, NULL, %s)
                ON CONFLICT (page_id) DO UPDATE SET
                    workspace_id = EXCLUDED.workspace_id,
                    version = EXCLUDED.version,
                    content_hash = EXCLUDED.content_hash,
                    status = 'indexing',
                    vector_ready = FALSE,
                    error = NULL,
                    chunk_count = EXCLUDED.chunk_count,
                    updated_at = CURRENT_TIMESTAMP
                """,
                (page_id, workspace_id, version, digest, len(pieces)),
            )
        conn.commit()
    return ids


def _store_vectors(chunk_ids: list[int], vectors: list[list[float]]) -> None:
    from app.ingest import to_pgvector_literal

    with connect() as conn:
        with conn.cursor() as cur:
            for chunk_id, vector in zip(chunk_ids, vectors):
                cur.execute(
                    "UPDATE page_chunks SET embedding = %s::vector WHERE id = %s",
                    (to_pgvector_literal(vector), chunk_id),
                )
        conn.commit()


def _finish_event(event_id: str, *, status: str, error: str | None, delay_s: float, page_id: str, vector_ready: bool) -> None:
    with connect() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                UPDATE outbox_events
                SET status = %s,
                    last_error = %s,
                    next_run_at = CURRENT_TIMESTAMP + (%s * INTERVAL '1 second'),
                    updated_at = CURRENT_TIMESTAMP
                WHERE id = %s::uuid
                """,
                (status, error, delay_s, event_id),
            )
            cur.execute(
                """
                UPDATE page_index_state
                SET status = %s,
                    vector_ready = %s,
                    error = %s,
                    updated_at = CURRENT_TIMESTAMP
                WHERE page_id = %s::uuid
                """,
                ("indexed" if status == "done" else status, vector_ready, error, page_id),
            )
        conn.commit()


def _backoff(attempt: int, message: str) -> float:
    match = re.search(r"(\d+(?:\.\d+)?)\s*giờ", message)
    if match:
        return min(6 * 3600, float(match.group(1)) * 3600)
    base = min(900.0, 5.0 * (2 ** max(attempt - 1, 0)))
    return base * random.uniform(0.8, 1.2)


def _retryable(message: str) -> bool:
    markers = ("429", "503", "hạn mức", "quota", "Timeout", "timed out", "RESOURCE_EXHAUSTED")
    return any(marker in message for marker in markers)


def process_event(event: dict[str, Any]) -> None:
    started = time.perf_counter()
    page_id = event["page_id"]
    loaded = _load_plain(page_id)
    if not loaded:
        _finish_event(event["id"], status="done", error=None, delay_s=0, page_id=page_id, vector_ready=False)
        return
    workspace_id, plain, digest = loaded
    version = int(event["payload"].get("version") or 1)
    pieces = _chunks(plain)
    chunk_ids = _write_chunks(page_id, workspace_id, version, digest, pieces)
    vector_ready = False
    try:
        from app.ingest import embed_texts

        vectors = embed_texts(pieces)
        if len(vectors) != len(chunk_ids):
            raise RuntimeError("Số vector không khớp số đoạn.")
        _store_vectors(chunk_ids, vectors)
        vector_ready = True
    except Exception as exc:
        message = redact(str(exc))
        if _retryable(message) and event["attempt"] < event["max_attempts"]:
            delay = _backoff(event["attempt"], message)
            _finish_event(event["id"], status="failed", error=message, delay_s=delay, page_id=page_id, vector_ready=False)
            _log(event, "retry", message, started)
            return
        final = "dead" if event["attempt"] >= event["max_attempts"] or not _retryable(message) else "failed"
        if not _retryable(message):
            final = "dead"
        _finish_event(event["id"], status=final, error=message, delay_s=0, page_id=page_id, vector_ready=False)
        _log(event, final, message, started)
        return
    _finish_event(event["id"], status="done", error=None, delay_s=0, page_id=page_id, vector_ready=vector_ready)
    _log(event, "indexed", f"chunks={len(pieces)}", started)


def _log(event: dict[str, Any], status: str, detail: str, started: float) -> None:
    print(
        json.dumps(
            {
                "msg": "page.index",
                "status": status,
                "trace_id": event["trace_id"],
                "event_id": event["id"],
                "page_id": event["page_id"],
                "attempt": event["attempt"],
                "ms": round((time.perf_counter() - started) * 1000, 1),
                "detail": detail[:240],
            },
            ensure_ascii=False,
        ),
        flush=True,
    )


def replay_event(workspace_id: str, event_id: str) -> dict[str, Any] | None:
    with connect() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                UPDATE outbox_events
                SET status = 'pending',
                    attempt = 0,
                    last_error = NULL,
                    next_run_at = CURRENT_TIMESTAMP,
                    updated_at = CURRENT_TIMESTAMP
                WHERE id = %s::uuid
                  AND status IN ('failed', 'dead')
                  AND payload->>'workspace_id' = %s
                RETURNING id::text, aggregate_id, status, last_error, attempt
                """,
                (event_id, workspace_id),
            )
            row = cur.fetchone()
        conn.commit()
    if not row:
        return None
    return {"id": row[0], "page_id": row[1], "status": row[2], "error": row[3], "attempt": row[4]}
