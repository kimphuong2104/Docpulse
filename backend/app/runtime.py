"""Tìm kiếm có lọc quyền, câu trả lời AI theo đoạn đã truy xuất, và số liệu vận hành."""

import hashlib
import json
import re
import time
import uuid
from typing import Any, Iterator

import httpx

from app.accounts import PlatformError, consume_rate, require_workspace
from app.config import EMBEDDING_API_KEY, EMBEDDING_DIM, EMBEDDING_MODEL, LLM_API_KEY, LLM_MODEL
from app.db import connect
from app.realtime import room_snapshot
from app.security import redact

_failures = 0
_open_until = 0.0


def _circuit_open() -> bool:
    return time.time() < _open_until


def _circuit_success() -> None:
    global _failures
    _failures = 0


def _circuit_failure() -> None:
    global _failures, _open_until
    _failures += 1
    if _failures >= 3:
        _open_until = time.time() + 60
        _failures = 0


def _extra_ids(values: list[str] | None) -> list[str]:
    parsed: list[str] = []
    for value in values or []:
        try:
            parsed.append(str(uuid.UUID(str(value))))
        except (ValueError, TypeError, AttributeError):
            continue
    return parsed[:8]


def _page_ids(
    cur,
    workspace_id: str,
    scope: str,
    page_id: str | None,
    extra_page_ids: list[str] | None = None,
) -> list[str]:
    if scope == "page" and page_id:
        cur.execute(
            """
            SELECT id::text FROM workspace_pages
            WHERE id = %s::uuid AND workspace_id = %s::uuid AND archived = FALSE
            """,
            (page_id, workspace_id),
        )
    elif scope == "children" and page_id:
        cur.execute(
            """
            WITH RECURSIVE tree AS (
                SELECT id FROM workspace_pages
                WHERE id = %s::uuid AND workspace_id = %s::uuid AND archived = FALSE
                UNION ALL
                SELECT child.id
                FROM workspace_pages child
                JOIN tree ON child.parent_id = tree.id
                WHERE child.archived = FALSE
            )
            SELECT id::text FROM tree
            """,
            (page_id, workspace_id),
        )
    else:
        cur.execute(
            "SELECT id::text FROM workspace_pages WHERE workspace_id = %s::uuid AND archived = FALSE",
            (workspace_id,),
        )
    ids = [row[0] for row in cur.fetchall()]
    extras = [item for item in _extra_ids(extra_page_ids) if item not in ids]
    if not extras:
        return ids
    cur.execute(
        """
        SELECT id::text FROM workspace_pages
        WHERE workspace_id = %s::uuid AND archived = FALSE AND id::text = ANY(%s)
        """,
        (workspace_id, extras),
    )
    ids.extend(row[0] for row in cur.fetchall())
    return ids


def _keyword_hits(cur, workspace_id: str, page_ids: list[str], query: str) -> list[dict[str, Any]]:
    if not page_ids:
        return []
    cur.execute(
        """
        SELECT c.page_id::text, p.title, p.icon,
               ts_headline('simple', c.content, plainto_tsquery('simple', %s),
                           'MaxFragments=2, MinWords=6, MaxWords=28'),
               ts_rank_cd(c.search_vector, plainto_tsquery('simple', %s))
        FROM page_chunks c
        JOIN workspace_pages p ON p.id = c.page_id
        WHERE c.workspace_id = %s::uuid
          AND c.page_id::text = ANY(%s)
          AND c.search_vector @@ plainto_tsquery('simple', %s)
        ORDER BY ts_rank_cd(c.search_vector, plainto_tsquery('simple', %s)) DESC
        LIMIT 8
        """,
        (query, query, workspace_id, page_ids, query, query),
    )
    hits = [
        {
            "page_id": row[0],
            "title": row[1],
            "icon": row[2],
            "snippet": row[3],
            "score": round(float(row[4]), 6),
            "score_kind": "ts_rank",
        }
        for row in cur.fetchall()
    ]
    if hits:
        return hits
    cur.execute(
        """
        SELECT id::text, title, icon, title
        FROM workspace_pages
        WHERE workspace_id = %s::uuid AND archived = FALSE AND id::text = ANY(%s)
          AND title ILIKE %s
        LIMIT 8
        """,
        (workspace_id, page_ids, f"%{query}%"),
    )
    return [
        {
            "page_id": row[0],
            "title": row[1],
            "icon": row[2],
            "snippet": row[3],
            "score": 0.0,
            "score_kind": "title",
        }
        for row in cur.fetchall()
    ]


def to_pgvector_literal(values: list[float]) -> str:
    return "[" + ",".join(f"{value:.8f}" for value in values) + "]"


def _embed_once(text: str) -> list[float] | None:
    try:
        with httpx.Client(timeout=8.0) as client:
            response = client.post(
                f"https://generativelanguage.googleapis.com/v1beta/models/{EMBEDDING_MODEL}:embedContent",
                params={"key": EMBEDDING_API_KEY},
                json={
                    "content": {"parts": [{"text": text[:2000]}]},
                    "outputDimensionality": EMBEDDING_DIM,
                },
            )
        if response.status_code >= 400:
            return None
        values = response.json().get("embedding", {}).get("values")
        if not values:
            return None
        return [float(value) for value in values]
    except Exception:
        return None


def _vector_hits(cur, workspace_id: str, page_ids: list[str], vector: list[float]) -> list[dict[str, Any]]:
    literal = to_pgvector_literal(vector)
    cur.execute(
        """
        SELECT c.page_id::text, p.title, p.icon, left(c.content, 280),
               1 - (c.embedding::halfvec(3072) <=> %s::halfvec) AS similarity
        FROM page_chunks c
        JOIN workspace_pages p ON p.id = c.page_id
        WHERE c.workspace_id = %s::uuid
          AND c.embedding IS NOT NULL
          AND c.page_id::text = ANY(%s)
        ORDER BY c.embedding::halfvec(3072) <=> %s::halfvec
        LIMIT 8
        """,
        (literal, workspace_id, page_ids, literal),
    )
    return [
        {
            "page_id": row[0],
            "title": row[1],
            "icon": row[2],
            "snippet": row[3],
            "score": round(float(row[4]), 6),
            "score_kind": "cosine",
        }
        for row in cur.fetchall()
    ]


def _rrf(keyword: list[dict[str, Any]], dense: list[dict[str, Any]]) -> list[dict[str, Any]]:
    scores: dict[tuple[str, str], float] = {}
    chosen: dict[tuple[str, str], dict[str, Any]] = {}
    for group in (keyword, dense):
        for rank, item in enumerate(group, start=1):
            key = (item["page_id"], item["snippet"])
            scores[key] = scores.get(key, 0.0) + 1.0 / (60.0 + rank)
            chosen[key] = item
    ordered = sorted(scores, key=lambda key: scores[key], reverse=True)
    merged = []
    for key in ordered[:8]:
        item = dict(chosen[key])
        item["score"] = round(scores[key], 6)
        item["score_kind"] = "rrf"
        merged.append(item)
    return merged


def _citation(item: dict[str, Any]) -> dict[str, Any]:
    snippet = item.get("snippet") or ""
    match = re.search(r"PDF page (\d+)", snippet)
    cited = {
        "page_id": item["page_id"],
        "title": item["title"],
        "snippet": snippet,
        "score": item["score"],
        "score_kind": item["score_kind"],
    }
    if match:
        cited["pdf_page"] = int(match.group(1))
    return cited


def search(
    user_id: str,
    slug: str,
    query: str,
    *,
    page_id: str | None,
    scope: str,
    extra_page_ids: list[str] | None = None,
) -> dict[str, Any]:
    workspace = require_workspace(user_id, slug)
    cleaned = " ".join(query.split())
    if len(cleaned) < 2:
        raise PlatformError(400, "Câu tìm kiếm quá ngắn.")
    mode = "keyword"
    matches: list[dict[str, Any]] = []
    with connect() as conn:
        with conn.cursor() as cur:
            page_ids = _page_ids(cur, workspace["id"], scope, page_id, extra_page_ids)
            matches = _keyword_hits(cur, workspace["id"], page_ids, cleaned)
            vector = _embed_once(cleaned)
            if vector is not None:
                try:
                    dense = _vector_hits(cur, workspace["id"], page_ids, vector)
                except Exception:
                    conn.rollback()
                    dense = []
                if dense:
                    matches = _rrf(matches, dense)
                    mode = "hybrid"
    return {"query": cleaned, "mode": mode, "scope": scope if scope in {"page", "children", "workspace"} else "workspace", "matches": matches}


def _quota_left(workspace_id: str, quota: int) -> int:
    with connect() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT COALESCE(SUM(tokens), 0) FROM usage_events WHERE workspace_id = %s::uuid AND ok = TRUE",
                (workspace_id,),
            )
            used = int(cur.fetchone()[0])
    return quota - used


def _passages(
    user_id: str,
    slug: str,
    prompt: str,
    page_id: str | None,
    scope: str,
    extra_page_ids: list[str] | None = None,
) -> dict[str, Any]:
    workspace = require_workspace(user_id, slug)
    if not consume_rate(f"ai:{user_id}", 15):
        raise PlatformError(429, "Quá nhiều yêu cầu AI trong một phút.")
    remaining = _quota_left(workspace["id"], workspace["token_quota"])
    if remaining <= 0:
        raise PlatformError(429, "Workspace đã dùng hết hạn mức token.")
    found = search(user_id, slug, prompt, page_id=page_id, scope=scope, extra_page_ids=extra_page_ids)
    passages = found["matches"][:5]
    cache_key = hashlib.sha256(
        f"{workspace['id']}|{prompt}|{json.dumps([item['snippet'] for item in passages], ensure_ascii=False)}".encode()
    ).hexdigest()
    cached = None
    with connect() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT answer FROM ai_cache
                WHERE cache_key = %s AND created_at > CURRENT_TIMESTAMP - INTERVAL '1 day'
                """,
                (cache_key,),
            )
            row = cur.fetchone()
            cached = row[0] if row else None
    return {
        "user_id": user_id,
        "workspace": workspace,
        "prompt": prompt,
        "mode": found["mode"],
        "passages": passages,
        "cache_key": cache_key,
        "cached": cached,
        "remaining": remaining,
    }


def _record(prepared: dict[str, Any], *, tokens: int, latency_ms: int, cache_hit: bool, ok: bool, answer: str) -> None:
    workspace_id = prepared["workspace"]["id"]
    with connect() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO usage_events (workspace_id, user_id, kind, tokens, latency_ms, cache_hit, ok)
                VALUES (%s::uuid, %s::uuid, 'ai', %s, %s, %s, %s)
                """,
                (workspace_id, prepared["user_id"], tokens, latency_ms, cache_hit, ok),
            )
            if ok and answer and not cache_hit:
                cur.execute(
                    """
                    INSERT INTO ai_cache (cache_key, workspace_id, answer)
                    VALUES (%s, %s::uuid, %s)
                    ON CONFLICT (cache_key) DO UPDATE SET answer = EXCLUDED.answer, created_at = CURRENT_TIMESTAMP
                    """,
                    (prepared["cache_key"], workspace_id, answer[:8000]),
                )
        conn.commit()


def _chunk_text(obj: dict[str, Any]) -> tuple[str, int | None]:
    candidates = obj.get("candidates") or []
    text = ""
    if candidates:
        parts = ((candidates[0].get("content") or {}).get("parts") or [])
        text = "".join(part.get("text") or "" for part in parts if isinstance(part, dict))
    usage = obj.get("usageMetadata") or {}
    total = usage.get("totalTokenCount")
    return text, int(total) if total else None


def iter_ai(
    user_id: str,
    slug: str,
    prompt: str,
    *,
    page_id: str | None,
    scope: str,
    extra_page_ids: list[str] | None = None,
) -> Iterator[dict[str, Any]]:
    prepared = _passages(user_id, slug, prompt.strip(), page_id, scope, extra_page_ids)
    passages = prepared["passages"]
    started = time.perf_counter()
    yield {"event": "status", "data": {"stage": "retrieve", "mode": prepared["mode"], "matches": len(passages)}}
    citations = [_citation(item) for item in passages]
    if prepared["cached"]:
        answer = prepared["cached"]
        latency = int((time.perf_counter() - started) * 1000)
        _record(prepared, tokens=0, latency_ms=latency, cache_hit=True, ok=True, answer=answer)
        yield {"event": "token", "data": {"text": answer}}
        yield {
            "event": "done",
            "data": {
                "answer": answer,
                "cache_hit": True,
                "model": LLM_MODEL,
                "latency_ms": latency,
                "ttft_ms": latency,
                "tokens": 0,
                "citations": citations,
                "grounded": bool(passages),
            },
        }
        return
    if not passages:
        answer = "Không có đoạn nào trong workspace khớp câu này sau khi lọc theo quyền của bạn."
        latency = int((time.perf_counter() - started) * 1000)
        _record(prepared, tokens=0, latency_ms=latency, cache_hit=False, ok=True, answer=answer)
        yield {"event": "token", "data": {"text": answer}}
        yield {
            "event": "done",
            "data": {
                "answer": answer,
                "cache_hit": False,
                "model": LLM_MODEL,
                "latency_ms": latency,
                "ttft_ms": latency,
                "tokens": 0,
                "citations": [],
                "grounded": False,
            },
        }
        return
    if _circuit_open():
        yield {"event": "error", "data": {"detail": "Mạch AI đang mở sau vài lỗi liên tiếp. Thử lại sau một phút."}}
        return
    context = "\n\n".join(f"[{index}] {item['title']}\n{item['snippet']}" for index, item in enumerate(passages, start=1))
    system = (
        "Answer only from the numbered passages. "
        "If they do not contain the answer, say that the indexed pages do not include it. "
        "Do not invent metrics, names, or mechanisms. Use the same language as the question."
    )
    user_prompt = f"PASSAGES:\n{context}\n\nQUESTION:\n{prompt.strip()}"
    pieces: list[str] = []
    tokens = 0
    ttft_ms: int | None = None
    try:
        with httpx.Client(timeout=40.0) as client:
            with client.stream(
                "POST",
                f"https://generativelanguage.googleapis.com/v1beta/models/{LLM_MODEL}:streamGenerateContent",
                params={"alt": "sse", "key": LLM_API_KEY},
                json={
                    "systemInstruction": {"parts": [{"text": system}]},
                    "contents": [{"role": "user", "parts": [{"text": user_prompt}]}],
                    "generationConfig": {"temperature": 0.2},
                },
            ) as response:
                if response.status_code >= 400:
                    body = redact(response.read().decode("utf-8", errors="replace"))
                    raise RuntimeError(f"LLM HTTP {response.status_code}: {body}")
                for line in response.iter_lines():
                    if not line or not line.startswith("data:"):
                        continue
                    raw = line[5:].strip()
                    if not raw or raw == "[DONE]":
                        continue
                    obj = json.loads(raw)
                    text, usage = _chunk_text(obj)
                    if usage:
                        tokens = usage
                    if not text:
                        continue
                    if ttft_ms is None:
                        ttft_ms = int((time.perf_counter() - started) * 1000)
                    pieces.append(text)
                    yield {"event": "token", "data": {"text": text}}
    except Exception as exc:
        _circuit_failure()
        message = redact(str(exc))
        latency = int((time.perf_counter() - started) * 1000)
        _record(prepared, tokens=0, latency_ms=latency, cache_hit=False, ok=False, answer="")
        yield {"event": "error", "data": {"detail": message}}
        return
    answer = "".join(pieces).strip()
    if not answer:
        _circuit_failure()
        yield {"event": "error", "data": {"detail": "Mô hình không trả về nội dung."}}
        return
    _circuit_success()
    if not tokens:
        tokens = max(1, len(answer) // 4)
    latency = int((time.perf_counter() - started) * 1000)
    _record(prepared, tokens=tokens, latency_ms=latency, cache_hit=False, ok=True, answer=answer)
    yield {
        "event": "done",
        "data": {
            "answer": answer,
            "cache_hit": False,
            "model": LLM_MODEL,
            "latency_ms": latency,
            "ttft_ms": ttft_ms if ttft_ms is not None else latency,
            "tokens": tokens,
            "citations": citations,
            "grounded": True,
        },
    }


def telemetry(user_id: str, slug: str) -> dict[str, Any]:
    workspace = require_workspace(user_id, slug)
    workspace_id = workspace["id"]
    with connect() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT status, COUNT(*)
                FROM outbox_events
                WHERE payload->>'workspace_id' = %s
                GROUP BY status
                """,
                (workspace_id,),
            )
            counts = {row[0]: int(row[1]) for row in cur.fetchall()}
            cur.execute(
                """
                SELECT EXTRACT(EPOCH FROM (CURRENT_TIMESTAMP - MIN(created_at)))
                FROM outbox_events
                WHERE payload->>'workspace_id' = %s AND status IN ('pending', 'failed', 'processing')
                """,
                (workspace_id,),
            )
            lag = cur.fetchone()[0]
            cur.execute(
                """
                SELECT id::text, event_type, aggregate_id, last_error, attempt, status, updated_at
                FROM outbox_events
                WHERE payload->>'workspace_id' = %s AND status IN ('failed', 'dead')
                ORDER BY updated_at DESC
                LIMIT 20
                """,
                (workspace_id,),
            )
            letters = [
                {
                    "id": row[0],
                    "event_type": row[1],
                    "page_id": row[2],
                    "error": row[3],
                    "attempt": int(row[4]),
                    "status": row[5],
                    "updated_at": row[6].isoformat() if row[6] else None,
                    "source": "outbox",
                }
                for row in cur.fetchall()
            ]
            cur.execute(
                """
                SELECT id::text, filename, error, attempt, status, updated_at
                FROM ingestion_jobs
                WHERE status = 'failed'
                ORDER BY updated_at DESC
                LIMIT 8
                """
            )
            documents = [
                {
                    "id": row[0],
                    "event_type": "document.ingest",
                    "page_id": None,
                    "filename": row[1],
                    "error": row[2],
                    "attempt": int(row[3]),
                    "status": row[4],
                    "updated_at": row[5].isoformat() if row[5] else None,
                    "source": "ingestion",
                }
                for row in cur.fetchall()
            ]
            cur.execute(
                """
                SELECT p.id::text, p.title, COALESCE(s.status, 'pending'), COALESCE(s.chunk_count, 0),
                       COALESCE(s.vector_ready, FALSE), s.error, s.updated_at
                FROM workspace_pages p
                LEFT JOIN page_index_state s ON s.page_id = p.id
                WHERE p.workspace_id = %s::uuid AND p.archived = FALSE
                ORDER BY p.position
                """,
                (workspace_id,),
            )
            stream = [
                {
                    "page_id": row[0],
                    "title": row[1],
                    "status": row[2],
                    "chunk_count": int(row[3]),
                    "vector_ready": bool(row[4]),
                    "error": row[5],
                    "updated_at": row[6].isoformat() if row[6] else None,
                }
                for row in cur.fetchall()
            ]
            cur.execute(
                """
                SELECT COALESCE(percentile_cont(0.5) WITHIN GROUP (ORDER BY latency_ms), 0),
                       COALESCE(percentile_cont(0.95) WITHIN GROUP (ORDER BY latency_ms), 0),
                       COUNT(*),
                       COUNT(*) FILTER (WHERE cache_hit),
                       COUNT(*) FILTER (WHERE NOT cache_hit),
                       COALESCE(SUM(tokens) FILTER (WHERE ok), 0)
                FROM usage_events
                WHERE workspace_id = %s::uuid AND kind = 'ai'
                  AND created_at > CURRENT_TIMESTAMP - INTERVAL '1 day'
                """,
                (workspace_id,),
            )
            stats = cur.fetchone()
            cur.execute(
                "SELECT COALESCE(SUM(tokens), 0) FROM usage_events WHERE workspace_id = %s::uuid AND ok = TRUE",
                (workspace_id,),
            )
            used_total = int(cur.fetchone()[0])
            cur.execute(
                "SELECT seen_at, detail FROM worker_heartbeats WHERE name = 'docpulse_worker'"
            )
            beat = cur.fetchone()
            cur.execute(
                """
                SELECT COUNT(*) FROM ingestion_jobs
                WHERE status IN ('pending', 'parsing', 'indexing')
                """
            )
            ingest_depth = int(cur.fetchone()[0])
    hits = int(stats[3])
    misses = int(stats[4])
    samples = hits + misses
    healthy = False
    seen_at = None
    if beat and beat[0]:
        seen_at = beat[0].isoformat()
        healthy = (time.time() - beat[0].timestamp()) < 20
    pending = counts.get("pending", 0) + counts.get("failed", 0) + counts.get("processing", 0)
    dead = counts.get("dead", 0)
    if not healthy:
        summary = "Worker offline"
    elif dead or documents:
        summary = "Dead letters waiting"
    else:
        summary = "Worker heartbeat current"
    indexed = sum(1 for item in stream if item["status"] == "indexed")
    return {
        "summary": summary,
        "worker": {"name": "docpulse_worker", "healthy": healthy, "seen_at": seen_at, "detail": beat[1] if beat else None},
        "realtime": room_snapshot(),
        "queue": {
            "pending": pending,
            "dead": dead,
            "done": counts.get("done", 0),
            "ingestion_active": ingest_depth,
            "lag_seconds": round(float(lag), 1) if lag is not None else 0,
        },
        "latency": {"p50_ms": round(float(stats[0]), 1), "p95_ms": round(float(stats[1]), 1), "samples": int(stats[2])},
        "cache": {"hits": hits, "misses": misses, "ratio": round(hits / samples, 4) if samples else None},
        "tokens": {"used": used_total, "used_today": int(stats[5]), "quota": workspace["token_quota"]},
        "freshness": {"indexed": indexed, "total": len(stream)},
        "stream": stream,
        "dead_letters": letters + documents,
        "circuit_open": _circuit_open(),
    }
