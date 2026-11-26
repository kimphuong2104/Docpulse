"""Trang và block của workspace. Mỗi lần ghi block kiểm tra version để không ghi đè lẫn nhau."""

import time
import uuid
from typing import Any

from google import genai
from openai import OpenAI
from psycopg.types.json import Json

from app.config import LLM_API_KEY, LLM_MODEL, LLM_PROVIDER, OPENAI_BASE_URL
from app.db import connect

BLOCK_TYPES = {
    "paragraph",
    "heading1",
    "heading2",
    "heading3",
    "bullet",
    "numbered",
    "todo",
    "quote",
    "code",
    "callout",
    "divider",
    "database",
    "file",
}
_AI_HITS: dict[str, list[float]] = {}


class VersionConflict(Exception):
    def __init__(self, current: dict[str, Any]):
        self.current = current


def _uuid_or_blank(value: Any) -> str:
    try:
        return str(uuid.UUID(str(value)))
    except (ValueError, TypeError, AttributeError):
        return ""


def _bounded_int(value: Any, default: int, upper: int) -> int:
    try:
        number = int(value)
    except (TypeError, ValueError):
        return default
    return max(0, min(upper, number))


def _refresh_file_blocks(cur, workspace_id: str | None, cleaned: list[tuple]) -> None:
    if not workspace_id:
        return
    ids = []
    for item in cleaned:
        payload = item[3].obj
        if payload.get("file_id") and not payload.get("pdf_page"):
            ids.append(payload["file_id"])
    if not ids:
        return
    cur.execute(
        """
        SELECT id::text, filename, byte_size, kind, status, progress, chunk_count, error
        FROM workspace_files
        WHERE workspace_id = %s::uuid AND id = ANY(%s::uuid[])
        """,
        (workspace_id, ids),
    )
    found = {row[0]: row for row in cur.fetchall()}
    for item in cleaned:
        payload = item[3].obj
        row = found.get(payload.get("file_id"))
        if not row or payload.get("pdf_page"):
            continue
        payload["filename"] = row[1]
        payload["text"] = row[1]
        payload["byte_size"] = int(row[2] or 0)
        payload["kind"] = row[3]
        payload["status"] = row[4]
        payload["progress"] = int(row[5] or 0)
        payload["chunk_count"] = int(row[6] or 0)
        payload["error"] = row[7] or ""


def _page_row(row: tuple) -> dict[str, Any]:
    return {
        "id": str(row[0]),
        "parent_id": str(row[1]) if row[1] else None,
        "title": row[2],
        "icon": row[3],
        "position": float(row[4]),
        "favorite": bool(row[5]),
        "archived": bool(row[6]),
        "version": int(row[7]),
        "created_at": row[8].isoformat() if row[8] else None,
        "updated_at": row[9].isoformat() if row[9] else None,
    }


def _block_row(row: tuple) -> dict[str, Any]:
    content = row[3] if isinstance(row[3], dict) else {}
    return {
        "id": str(row[0]),
        "page_id": str(row[1]),
        "type": row[2],
        "content": content,
        "position": float(row[4]),
    }


def _empty_block(page_id: str, position: float = 0) -> tuple:
    return (str(uuid.uuid4()), page_id, "paragraph", Json({"text": "", "checked": False}), position)


def list_pages(*, archived: bool = False) -> dict[str, Any]:
    _ensure_home()
    with connect() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT id, parent_id, title, icon, position, favorite, archived, version, created_at, updated_at
                FROM workspace_pages
                WHERE archived = %s
                ORDER BY position, created_at
                """,
                (archived,),
            )
            rows = cur.fetchall()
    pages = [_page_row(row) for row in rows]
    return {"total": len(pages), "pages": pages}


def _ensure_home() -> None:
    with connect() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT COUNT(*) FROM workspace_pages")
            count = int(cur.fetchone()[0])
            if count:
                return
            page_id = str(uuid.uuid4())
            cur.execute(
                """
                INSERT INTO workspace_pages (id, title, icon, position)
                VALUES (%s::uuid, %s, %s, 0)
                """,
                (page_id, "Chào mừng", "✨"),
            )
            seeds = [
                ("heading1", {"text": "Không gian làm việc theo khối", "checked": False}),
                (
                    "paragraph",
                    {
                        "text": "Gõ / để chọn loại khối. # và dấu cách tạo tiêu đề, - tạo danh sách, [] tạo việc cần làm.",
                        "checked": False,
                    },
                ),
                ("todo", {"text": "Kéo thả một khối để đổi thứ tự", "checked": False}),
                (
                    "callout",
                    {
                        "text": "Trợ lý bên phải chỉ dùng chữ trên trang này. Chèn câu trả lời khi bạn bấm Đồng ý.",
                        "checked": False,
                    },
                ),
            ]
            for index, (block_type, content) in enumerate(seeds):
                cur.execute(
                    """
                    INSERT INTO workspace_blocks (id, page_id, block_type, content, position)
                    VALUES (%s::uuid, %s::uuid, %s, %s, %s)
                    """,
                    (str(uuid.uuid4()), page_id, block_type, Json(content), index),
                )
        conn.commit()


def get_page(page_id: str) -> dict[str, Any] | None:
    with connect() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT id, parent_id, title, icon, position, favorite, archived, version, created_at, updated_at
                FROM workspace_pages WHERE id = %s::uuid
                """,
                (page_id,),
            )
            page = cur.fetchone()
            if not page:
                return None
            cur.execute(
                """
                SELECT id, page_id, block_type, content, position
                FROM workspace_blocks
                WHERE page_id = %s::uuid
                ORDER BY position, created_at
                """,
                (page_id,),
            )
            blocks = [_block_row(row) for row in cur.fetchall()]
    detail = _page_row(page)
    detail["blocks"] = blocks or [
        {
            "id": str(uuid.uuid4()),
            "page_id": page_id,
            "type": "paragraph",
            "content": {"text": "", "checked": False},
            "position": 0,
        }
    ]
    return detail


def create_page(title: str, parent_id: str | None, icon: str) -> dict[str, Any]:
    page_id = str(uuid.uuid4())
    with connect() as conn:
        with conn.cursor() as cur:
            if parent_id:
                cur.execute("SELECT 1 FROM workspace_pages WHERE id = %s::uuid", (parent_id,))
                if not cur.fetchone():
                    raise ValueError("Không tìm thấy trang cha.")
            cur.execute(
                """
                SELECT COALESCE(MAX(position), -1) + 1
                FROM workspace_pages
                WHERE parent_id IS NOT DISTINCT FROM %s::uuid AND archived = FALSE
                """,
                (parent_id,),
            )
            position = float(cur.fetchone()[0])
            cur.execute(
                """
                INSERT INTO workspace_pages (id, parent_id, title, icon, position)
                VALUES (%s::uuid, %s::uuid, %s, %s, %s)
                RETURNING id, parent_id, title, icon, position, favorite, archived, version, created_at, updated_at
                """,
                (page_id, parent_id, title.strip() or "Không tiêu đề", (icon or "📄")[:8], position),
            )
            page = _page_row(cur.fetchone())
            block = _empty_block(page_id)
            cur.execute(
                """
                INSERT INTO workspace_blocks (id, page_id, block_type, content, position)
                VALUES (%s::uuid, %s::uuid, %s, %s, %s)
                """,
                block,
            )
        conn.commit()
    detail = get_page(page_id)
    return detail or page


def _creates_cycle(cur, page_id: str, parent_id: str | None) -> bool:
    current = parent_id
    seen: set[str] = set()
    while current:
        if current == page_id or current in seen:
            return True
        seen.add(current)
        cur.execute("SELECT parent_id::text FROM workspace_pages WHERE id = %s::uuid", (current,))
        row = cur.fetchone()
        current = row[0] if row and row[0] else None
    return False


def update_page(page_id: str, fields: dict[str, Any]) -> dict[str, Any] | None:
    with connect() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT version FROM workspace_pages WHERE id = %s::uuid", (page_id,))
            row = cur.fetchone()
            if not row:
                return None
            if fields.get("version") is not None and int(fields["version"]) != int(row[0]):
                raise VersionConflict(get_page(page_id) or {})
            parent_id = fields.get("parent_id") if "parent_id" in fields else None
            if "parent_id" in fields and parent_id and _creates_cycle(cur, page_id, parent_id):
                raise ValueError("Không thể chuyển trang vào chính nhánh con của nó.")
            assignments = ["updated_at = CURRENT_TIMESTAMP"]
            values: list[Any] = []
            mapping = {
                "title": "title",
                "icon": "icon",
                "favorite": "favorite",
                "archived": "archived",
                "position": "position",
            }
            for key, column in mapping.items():
                if key in fields and fields[key] is not None:
                    assignments.append(f"{column} = %s")
                    values.append(fields[key][:200] if key == "title" else fields[key])
            if "parent_id" in fields:
                assignments.append("parent_id = %s::uuid")
                values.append(fields["parent_id"])
            if fields.get("archived") is True or fields.get("archived") is False:
                cur.execute(
                    """
                    WITH RECURSIVE tree AS (
                        SELECT id FROM workspace_pages WHERE id = %s::uuid
                        UNION ALL
                        SELECT child.id FROM workspace_pages child
                        JOIN tree ON child.parent_id = tree.id
                    )
                    UPDATE workspace_pages
                    SET archived = %s, updated_at = CURRENT_TIMESTAMP
                    WHERE id IN (SELECT id FROM tree) AND id <> %s::uuid
                    """,
                    (page_id, fields["archived"], page_id),
                )
            values.append(page_id)
            cur.execute(
                f"""
                UPDATE workspace_pages
                SET {", ".join(assignments)}
                WHERE id = %s::uuid
                RETURNING id, parent_id, title, icon, position, favorite, archived, version, created_at, updated_at
                """,
                values,
            )
            updated = cur.fetchone()
        conn.commit()
    return _page_row(updated) if updated else None


def save_blocks(
    page_id: str,
    version: int,
    blocks: list[dict[str, Any]],
    actor_id: str | None = None,
    trace_id: str | None = None,
) -> dict[str, Any]:
    cleaned = []
    for index, block in enumerate(blocks[:400]):
        block_type = block["type"] if block["type"] in BLOCK_TYPES else "paragraph"
        content = block.get("content") if isinstance(block.get("content"), dict) else {}
        text = str(content.get("text") or "")[:20000]
        rows = content.get("rows") if isinstance(content.get("rows"), list) else []
        file_id = _uuid_or_blank(content.get("file_id"))
        kind = content.get("kind") if content.get("kind") in {"pdf", "markdown"} else ""
        status = content.get("status") if content.get("status") in {"queued", "parsing", "indexing", "indexed", "failed"} else ""
        safe_rows = []
        for row in rows[:100]:
            if not isinstance(row, dict):
                continue
            safe_rows.append(
                {
                    "id": str(row.get("id") or uuid.uuid4()),
                    "title": str(row.get("title") or "")[:500],
                    "status": str(row.get("status") or "Cần làm")[:40],
                }
            )
        try:
            block_id = str(uuid.UUID(str(block["id"])))
        except ValueError:
            block_id = str(uuid.uuid4())
        cleaned.append(
            (
                block_id,
                page_id,
                block_type,
                Json(
                    {
                        "text": text,
                        "checked": bool(content.get("checked")),
                        "view": "board" if content.get("view") == "board" else "table",
                        "rows": safe_rows,
                        "source": "tasks" if content.get("source") == "tasks" else "",
                        "file_id": file_id,
                        "filename": str(content.get("filename") or "")[:240],
                        "byte_size": _bounded_int(content.get("byte_size"), 0, 50_000_000),
                        "kind": kind,
                        "status": status,
                        "progress": _bounded_int(content.get("progress"), 0, 100),
                        "chunk_count": _bounded_int(content.get("chunk_count"), 0, 10_000),
                        "pdf_page": _bounded_int(content.get("pdf_page"), 0, 10_000) or None,
                        "error": str(content.get("error") or "")[:500],
                    }
                ),
                float(index),
            )
        )
    if not cleaned:
        cleaned.append(_empty_block(page_id))

    with connect() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT version FROM workspace_pages WHERE id = %s::uuid FOR UPDATE",
                (page_id,),
            )
            row = cur.fetchone()
            if not row:
                raise KeyError(page_id)
            cur.execute(
                "SELECT workspace_id::text FROM workspace_pages WHERE id = %s::uuid",
                (page_id,),
            )
            workspace_id = cur.fetchone()[0]
            _refresh_file_blocks(cur, workspace_id, cleaned)
            if int(row[0]) != int(version):
                current = get_page(page_id)
                raise VersionConflict(current or {})
            cur.execute("DELETE FROM workspace_blocks WHERE page_id = %s::uuid", (page_id,))
            cur.executemany(
                """
                INSERT INTO workspace_blocks (id, page_id, block_type, content, position)
                VALUES (%s::uuid, %s::uuid, %s, %s, %s)
                """,
                cleaned,
            )
            cur.execute(
                """
                UPDATE workspace_pages
                SET version = version + 1, updated_at = CURRENT_TIMESTAMP
                WHERE id = %s::uuid
                """,
                (page_id,),
            )
            cur.execute(
                "SELECT title, workspace_id::text FROM workspace_pages WHERE id = %s::uuid",
                (page_id,),
            )
            title, workspace_id = cur.fetchone()
            from app.indexing import enqueue_index, text_from_blocks

            enqueue_index(
                cur,
                page_id=page_id,
                workspace_id=workspace_id,
                version=int(row[0]) + 1,
                plain=text_from_blocks(title or "", blocks),
                trace_id=trace_id or str(uuid.uuid4()),
                actor_id=actor_id,
            )
        conn.commit()
    detail = get_page(page_id)
    if not detail:
        raise KeyError(page_id)
    return detail


def delete_page(page_id: str) -> bool:
    with connect() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "DELETE FROM workspace_pages WHERE id = %s::uuid AND archived = TRUE RETURNING id",
                (page_id,),
            )
            removed = cur.fetchone()
        conn.commit()
    return bool(removed)


def _page_plain_text(detail: dict[str, Any]) -> str:
    lines = [f"# {detail['title'] or 'Không tiêu đề'}"]
    for block in detail["blocks"]:
        content = block["content"]
        text = str(content.get("text") or "").strip()
        kind = block["type"]
        if kind == "divider":
            continue
        if kind == "database":
            for row in content.get("rows") or []:
                lines.append(f"- {row.get('title', '')} ({row.get('status', '')})")
            continue
        if not text:
            continue
        prefix = {"heading1": "# ", "heading2": "## ", "heading3": "### ", "bullet": "- ", "numbered": "1. ", "todo": "- [ ] "}.get(kind, "")
        if kind == "todo" and content.get("checked"):
            prefix = "- [x] "
        lines.append(prefix + text)
    return "\n".join(lines)[:20000]


def _allow_ai(client_key: str) -> bool:
    now = time.time()
    recent = [stamp for stamp in _AI_HITS.get(client_key, []) if now - stamp < 60]
    if len(recent) >= 15:
        _AI_HITS[client_key] = recent
        return False
    recent.append(now)
    _AI_HITS[client_key] = recent
    return True


def ask_page(page_id: str, mode: str, prompt: str, client_key: str) -> dict[str, str]:
    if not _allow_ai(client_key):
        raise PermissionError("Quá nhiều yêu cầu AI trong một phút.")
    detail = get_page(page_id)
    if not detail:
        raise KeyError(page_id)
    page_text = _page_plain_text(detail)
    task = {
        "summarize": "Tóm tắt trang này trong vài gạch đầu dòng ngắn.",
        "continue": "Viết tiếp ý cuối của trang, chỉ viết phần mới, không chép lại cả trang.",
        "translate": "Dịch nội dung trang sang ngôn ngữ còn lại giữa tiếng Việt và tiếng Anh.",
        "ask": prompt.strip() or "Trang này đang nói về điều gì?",
    }[mode if mode in {"summarize", "continue", "translate", "ask"} else "ask"]
    system_instruction = (
        "Bạn là trợ lý gắn với một trang tài liệu. Chỉ dùng NỘI DUNG TRANG được cung cấp. "
        "Không thêm sự kiện, số liệu hay tên riêng không có trong trang. "
        "Nếu trang không đủ thông tin, nói rõ là trang chưa có phần đó. "
        "Trả lời bằng tiếng Việt, ngắn, sẵn sàng để chèn thành một khối văn bản."
    )
    user_prompt = f"NỘI DUNG TRANG:\n{page_text}\n\nYÊU CẦU:\n{task}"
    if LLM_PROVIDER == "gemini":
        client = genai.Client(api_key=LLM_API_KEY, http_options={"api_version": "v1beta"})
        response = client.models.generate_content(
            model=LLM_MODEL,
            contents=user_prompt,
            config={"system_instruction": system_instruction, "temperature": 0.2},
        )
        answer = (response.text or "").strip()
    else:
        client = OpenAI(api_key=LLM_API_KEY, base_url=OPENAI_BASE_URL or None)
        response = client.chat.completions.create(
            model=LLM_MODEL,
            temperature=0.2,
            messages=[
                {"role": "system", "content": system_instruction},
                {"role": "user", "content": user_prompt},
            ],
        )
        answer = (response.choices[0].message.content or "").strip()
    return {"answer": answer, "mode": mode}
