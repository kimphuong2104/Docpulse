from typing import Any

from psycopg.types.json import Json

from app.db import connect
from app.rag import run_query_pipeline


def _row_session(row: tuple) -> dict[str, Any]:
    return {
        "id": row[0],
        "title": row[1],
        "namespace": row[2],
        "summary": row[3],
        "created_at": row[4].isoformat() if row[4] else None,
        "updated_at": row[5].isoformat() if row[5] else None,
    }


def list_sessions(namespace: str | None = None) -> dict[str, Any]:
    sql = """
        SELECT id::text, title, namespace, summary, created_at, updated_at
        FROM chat_sessions
        WHERE (%s::text IS NULL OR namespace = %s)
        ORDER BY updated_at DESC;
    """
    with connect() as conn:
        with conn.cursor() as cur:
            cur.execute(sql, (namespace, namespace))
            rows = cur.fetchall()
    sessions = [_row_session(row) for row in rows]
    return {"total": len(sessions), "sessions": sessions}


def create_session(title: str | None = None, namespace: str = "default") -> dict[str, Any]:
    label = (title or "Hội thoại mới").strip() or "Hội thoại mới"
    scope = namespace.strip() or "default"
    sql = """
        INSERT INTO chat_sessions (title, namespace)
        VALUES (%s, %s)
        RETURNING id::text, title, namespace, summary, created_at, updated_at;
    """
    with connect() as conn:
        with conn.cursor() as cur:
            cur.execute(sql, (label, scope))
            row = cur.fetchone()
        conn.commit()
    return _row_session(row)


def get_session(session_id: str) -> dict[str, Any] | None:
    with connect() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT id::text, title, namespace, summary, created_at, updated_at
                FROM chat_sessions
                WHERE id = %s::uuid;
                """,
                (session_id,),
            )
            session = cur.fetchone()
            if not session:
                return None
            cur.execute(
                """
                SELECT id, role, content, rewritten_query, citations, latency_ms, created_at
                FROM chat_messages
                WHERE session_id = %s::uuid
                ORDER BY created_at ASC, id ASC;
                """,
                (session_id,),
            )
            messages = cur.fetchall()
    detail = _row_session(session)
    detail["messages"] = [
        {
            "id": row[0],
            "role": row[1],
            "content": row[2],
            "rewritten_query": row[3],
            "citations": row[4] or [],
            "latency_ms": row[5] or {},
            "created_at": row[6].isoformat() if row[6] else None,
        }
        for row in messages
    ]
    return detail


def delete_session(session_id: str) -> bool:
    with connect() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "DELETE FROM chat_sessions WHERE id = %s::uuid RETURNING id;",
                (session_id,),
            )
            row = cur.fetchone()
        conn.commit()
    return row is not None


def _recent_history(cur, session_id: str, limit: int = 8) -> list[dict[str, str]]:
    cur.execute(
        """
        SELECT role, content
        FROM (
            SELECT role, content, created_at, id
            FROM chat_messages
            WHERE session_id = %s::uuid
            ORDER BY created_at DESC, id DESC
            LIMIT %s
        ) recent
        ORDER BY created_at ASC, id ASC;
        """,
        (session_id, limit),
    )
    return [{"role": row[0], "content": row[1]} for row in cur.fetchall()]


def answer_in_session(session_id: str, question: str, **pipeline_kwargs: Any) -> dict[str, Any] | None:
    """Lưu lượt hỏi, chạy RAG có nhớ ngữ cảnh, rồi lưu câu trả lời. None nếu không có phiên."""
    with connect() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT 1 FROM chat_sessions WHERE id = %s::uuid;", (session_id,))
            if cur.fetchone() is None:
                return None
            history = _recent_history(cur, session_id, limit=8)
            is_first = not history
            cur.execute(
                """
                INSERT INTO chat_messages (session_id, role, content)
                VALUES (%s::uuid, 'user', %s);
                """,
                (session_id, question),
            )
        conn.commit()

    result = run_query_pipeline(question=question, history=history, **pipeline_kwargs)
    stored_latency = {
        **result["latency_ms"],
        "mode": result["mode"],
        "grounded": result["grounded"],
    }
    title = question.strip().replace("\n", " ")[:60] if is_first else None
    with connect() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO chat_messages (
                    session_id, role, content, rewritten_query, citations, latency_ms
                )
                VALUES (%s::uuid, 'assistant', %s, %s, %s, %s);
                """,
                (
                    session_id,
                    result["answer"],
                    result["rewritten_query"],
                    Json(result["citations"]),
                    Json(stored_latency),
                ),
            )
            cur.execute(
                """
                UPDATE chat_sessions
                SET updated_at = CURRENT_TIMESTAMP,
                    title = COALESCE(%s, title)
                WHERE id = %s::uuid;
                """,
                (title, session_id),
            )
        conn.commit()
    result["session_id"] = session_id
    return result
