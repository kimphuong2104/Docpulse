"""Nhận file .pdf và .md vào workspace. PDF được worker đọc sau, markdown thành block ngay."""

import hashlib
import re
import uuid
from pathlib import Path
from typing import Any

from psycopg.types.json import Json

from app.accounts import PlatformError, consume_rate, require_workspace
from app.config import MAX_UPLOAD_MB, UPLOAD_DIR
from app.db import connect
from app.indexing import enqueue_index, text_from_blocks
from app.security import redact

_STATUSES = {"queued", "parsing", "indexing", "indexed", "failed"}


def overlay_files(cur, blocks: list[dict[str, Any]]) -> list[dict[str, Any]]:
    ids = []
    for block in blocks:
        content = block.get("content") if isinstance(block.get("content"), dict) else {}
        raw = str(content.get("file_id") or "")
        try:
            ids.append(str(uuid.UUID(raw)))
        except ValueError:
            continue
    if not ids:
        return blocks
    cur.execute(
        """
        SELECT f.id::text, f.status, f.progress, f.error, f.chunk_count, f.byte_size, f.filename, f.kind,
               COALESCE(s.chunk_count, 0), COALESCE(s.status, ''), COALESCE(s.vector_ready, FALSE)
        FROM workspace_files f
        LEFT JOIN page_index_state s ON s.page_id = f.page_id
        WHERE f.id = ANY(%s::uuid[])
        """,
        (ids,),
    )
    found = {row[0]: row for row in cur.fetchall()}
    merged = []
    for block in blocks:
        content = dict(block.get("content") or {})
        row = found.get(str(content.get("file_id") or ""))
        if row and not content.get("pdf_page"):
            content.update(_public_fields(row))
        merged.append({**block, "content": content})
    return merged


def read_file(user_id: str, slug: str, file_id: str) -> dict[str, Any]:
    workspace = require_workspace(user_id, slug)
    with connect() as conn:
        with conn.cursor() as cur:
            row = _lock_file(cur, workspace["id"], file_id)
    if not row:
        raise PlatformError(404, "Không tìm thấy file trong workspace này.")
    return _public_file(row)


def accept_file(
    user_id: str,
    slug: str,
    *,
    filename: str,
    payload: bytes,
    mode: str,
    page_id: str | None,
    trace_id: str,
) -> dict[str, Any]:
    workspace = require_workspace(user_id, slug, write=True)
    if not consume_rate(f"upload:{user_id}", 20):
        raise PlatformError(429, "Quá nhiều file trong một phút.")
    name, kind = _filename(filename)
    if not payload:
        raise PlatformError(400, "File trống.")
    if len(payload) > MAX_UPLOAD_MB * 1024 * 1024:
        raise PlatformError(413, f"File vượt quá {MAX_UPLOAD_MB}MB.")
    if mode not in {"attach", "import", "context"}:
        raise PlatformError(400, "Chế độ nhận file không hợp lệ.")
    if mode == "attach" and not page_id:
        raise PlatformError(400, "Đính kèm file cần một trang đang mở.")
    file_id = str(uuid.uuid4())
    destination = Path(UPLOAD_DIR) / "workspace" / f"{file_id}{'.pdf' if kind == 'pdf' else '.md'}"
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_bytes(payload)
    digest = hashlib.sha256(payload).hexdigest()
    target_page = page_id
    block_id = str(uuid.uuid4())
    status = "queued" if kind == "pdf" else "indexing"
    try:
        with connect() as conn:
            with conn.cursor() as cur:
                if mode == "attach":
                    _assert_page(cur, workspace["id"], page_id or "")
                else:
                    target_page = _create_page(cur, workspace["id"], user_id, _title(name), page_id if mode == "context" else None)
                cur.execute(
                    """
                    INSERT INTO workspace_files (
                        id, workspace_id, page_id, block_id, filename, kind, byte_size,
                        storage_path, sha256, status, progress, created_by
                    )
                    VALUES (%s::uuid, %s::uuid, %s::uuid, %s::uuid, %s, %s, %s, %s, %s, %s, %s, %s::uuid)
                    """,
                    (
                        file_id,
                        workspace["id"],
                        target_page,
                        block_id,
                        name,
                        kind,
                        len(payload),
                        str(destination),
                        digest,
                        status,
                        100 if kind == "markdown" else 0,
                        user_id,
                    ),
                )
                record = {
                    "id": file_id,
                    "filename": name,
                    "kind": kind,
                    "byte_size": len(payload),
                    "status": status,
                    "progress": 100 if kind == "markdown" else 0,
                    "chunk_count": 0,
                    "error": None,
                }
                extra = markdown_blocks(payload.decode("utf-8", errors="replace")) if kind == "markdown" else []
                _insert_blocks(cur, target_page or "", block_id, record, extra, replace=mode != "attach")
                cur.execute(
                    """
                    UPDATE workspace_pages
                    SET version = version + 1, updated_at = CURRENT_TIMESTAMP
                    WHERE id = %s::uuid
                    RETURNING version, title
                    """,
                    (target_page,),
                )
                version, title = cur.fetchone()
                blocks = _blocks_for_index(cur, target_page or "")
                enqueue_index(
                    cur,
                    page_id=target_page or "",
                    workspace_id=workspace["id"],
                    version=int(version),
                    plain=text_from_blocks(title or name, blocks),
                    trace_id=trace_id,
                    actor_id=user_id,
                )
                cur.execute(
                    """
                    INSERT INTO activity_events (workspace_id, actor_id, action, target, detail)
                    VALUES (%s::uuid, %s::uuid, 'imported', %s, %s)
                    """,
                    (workspace["id"], user_id, target_page, name[:200]),
                )
            conn.commit()
    except Exception:
        destination.unlink(missing_ok=True)
        raise
    from app.accounts import read_workspace_page

    return {"file": read_file(user_id, slug, file_id), "page": read_workspace_page(user_id, slug, target_page or "")}


def markdown_blocks(text: str) -> list[tuple[str, str]]:
    blocks: list[tuple[str, str]] = []
    paragraph: list[str] = []

    def flush() -> None:
        joined = " ".join(paragraph).strip()
        paragraph.clear()
        if joined:
            blocks.append(("paragraph", joined[:8000]))

    for line in text.splitlines():
        stripped = line.strip()
        if not stripped:
            flush()
            continue
        heading = _heading(stripped)
        if heading:
            flush()
            blocks.append(heading)
            continue
        numbered = re.match(r"^\d+\.\s+(.*)$", stripped)
        if numbered and numbered.group(1).strip():
            flush()
            blocks.append(("numbered", numbered.group(1).strip()[:8000]))
            continue
        if stripped.startswith(("- ", "* ")) and stripped[2:].strip():
            flush()
            blocks.append(("bullet", stripped[2:].strip()[:8000]))
            continue
        paragraph.append(stripped)
    flush()
    return blocks[:200]


def claim_workspace_file() -> dict[str, Any] | None:
    with connect() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                WITH picked AS (
                    SELECT id FROM workspace_files
                    WHERE status = 'queued' AND kind = 'pdf'
                    ORDER BY created_at
                    FOR UPDATE SKIP LOCKED
                    LIMIT 1
                )
                UPDATE workspace_files AS file
                SET status = 'parsing', progress = 20, error = NULL, updated_at = CURRENT_TIMESTAMP
                FROM picked
                WHERE file.id = picked.id
                RETURNING file.id::text, file.workspace_id::text, file.page_id::text, file.block_id::text,
                          file.filename, file.storage_path, file.created_by::text
                """
            )
            row = cur.fetchone()
        conn.commit()
    if not row:
        return None
    return {
        "id": row[0],
        "workspace_id": row[1],
        "page_id": row[2],
        "block_id": row[3],
        "filename": row[4],
        "storage_path": row[5],
        "created_by": row[6],
    }


def recover_stuck_files() -> int:
    with connect() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                UPDATE workspace_files
                SET status = 'queued', progress = 0, updated_at = CURRENT_TIMESTAMP
                WHERE status = 'parsing'
                  AND updated_at < CURRENT_TIMESTAMP - INTERVAL '10 minutes'
                """
            )
            count = cur.rowcount
        conn.commit()
    return count


def process_workspace_file(record: dict[str, Any]) -> None:
    try:
        pages = extract_pdf_pages(record["storage_path"])
        if not pages:
            raise ValueError("Không đọc được chữ trong file PDF.")
        _write_pdf(record, pages)
    except Exception as exc:
        _fail_file(record, redact(str(exc))[:500])
        raise


def extract_pdf_pages(path: str) -> list[tuple[int, str]]:
    import pypdfium2 as pdfium

    document = pdfium.PdfDocument(path)
    pages: list[tuple[int, str]] = []
    try:
        for index in range(min(len(document), 40)):
            page = document[index]
            text_page = page.get_textpage()
            text = text_page.get_text_bounded().strip()
            text_page.close()
            page.close()
            if text:
                pages.append((index + 1, text[:8000]))
    finally:
        document.close()
    return pages


def _write_pdf(record: dict[str, Any], pages: list[tuple[int, str]]) -> None:
    pieces = [("paragraph", f"PDF page {number}\n{text}") for number, text in pages]
    with connect() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                DELETE FROM workspace_blocks
                WHERE page_id = %s::uuid
                  AND content->>'file_id' = %s
                  AND content ? 'pdf_page'
                """,
                (record["page_id"], record["id"]),
            )
            cur.execute(
                "SELECT COALESCE(MAX(position), -1) FROM workspace_blocks WHERE page_id = %s::uuid",
                (record["page_id"],),
            )
            position = float(cur.fetchone()[0])
            for offset, (number, _text) in enumerate(pages, start=1):
                cur.execute(
                    """
                    INSERT INTO workspace_blocks (id, page_id, block_type, content, position)
                    VALUES (%s::uuid, %s::uuid, 'paragraph', %s, %s)
                    """,
                    (
                        str(uuid.uuid4()),
                        record["page_id"],
                        Json(
                            {
                                "text": pieces[offset - 1][1],
                                "checked": False,
                                "view": "table",
                                "rows": [],
                                "source": "",
                                "file_id": record["id"],
                                "filename": record["filename"],
                                "pdf_page": number,
                            }
                        ),
                        position + offset,
                    ),
                )
            cur.execute(
                """
                UPDATE workspace_files
                SET status = 'indexing', progress = 100, chunk_count = %s, error = NULL, updated_at = CURRENT_TIMESTAMP
                WHERE id = %s::uuid
                """,
                (len(pages), record["id"]),
            )
            cur.execute(
                """
                UPDATE workspace_blocks
                SET content = content || %s::jsonb
                WHERE id = %s::uuid
                """,
                (Json({"status": "indexing", "progress": 100, "chunk_count": len(pages)}), record["block_id"]),
            )
            cur.execute(
                """
                UPDATE workspace_pages
                SET version = version + 1, updated_at = CURRENT_TIMESTAMP
                WHERE id = %s::uuid
                RETURNING version, title
                """,
                (record["page_id"],),
            )
            version, title = cur.fetchone()
            blocks = _blocks_for_index(cur, record["page_id"])
            enqueue_index(
                cur,
                page_id=record["page_id"],
                workspace_id=record["workspace_id"],
                version=int(version),
                plain=text_from_blocks(title or record["filename"], blocks),
                trace_id=str(uuid.uuid4()),
                actor_id=record["created_by"],
            )
        conn.commit()


def _fail_file(record: dict[str, Any], message: str) -> None:
    with connect() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                UPDATE workspace_files
                SET status = 'failed', progress = 100, error = %s, updated_at = CURRENT_TIMESTAMP
                WHERE id = %s::uuid
                """,
                (message, record["id"]),
            )
            if record.get("block_id"):
                cur.execute(
                    """
                    UPDATE workspace_blocks
                    SET content = content || %s::jsonb
                    WHERE id = %s::uuid
                    """,
                    (Json({"status": "failed", "progress": 100, "error": message}), record["block_id"]),
                )
        conn.commit()


def _insert_blocks(cur, page_id: str, block_id: str, record: dict[str, Any], extra: list[tuple[str, str]], *, replace: bool) -> None:
    if replace:
        cur.execute("DELETE FROM workspace_blocks WHERE page_id = %s::uuid", (page_id,))
        position = -1.0
    else:
        cur.execute("SELECT COALESCE(MAX(position), -1) FROM workspace_blocks WHERE page_id = %s::uuid", (page_id,))
        position = float(cur.fetchone()[0])
    rows = [(block_id, "file", _file_json(record))]
    rows.extend((str(uuid.uuid4()), kind, {"text": text, "checked": False, "view": "table", "rows": [], "source": ""}) for kind, text in extra)
    for offset, (row_id, kind, content) in enumerate(rows, start=1):
        cur.execute(
            """
            INSERT INTO workspace_blocks (id, page_id, block_type, content, position)
            VALUES (%s::uuid, %s::uuid, %s, %s, %s)
            """,
            (row_id, page_id, kind, Json(content), position + offset),
        )


def _file_json(record: dict[str, Any]) -> dict[str, Any]:
    return {
        "text": record["filename"],
        "checked": False,
        "view": "table",
        "rows": [],
        "source": "",
        "file_id": record["id"],
        "filename": record["filename"],
        "byte_size": record["byte_size"],
        "kind": record["kind"],
        "status": record["status"],
        "progress": record["progress"],
        "chunk_count": record["chunk_count"],
    }


def _blocks_for_index(cur, page_id: str) -> list[dict[str, Any]]:
    cur.execute(
        "SELECT block_type, content FROM workspace_blocks WHERE page_id = %s::uuid ORDER BY position",
        (page_id,),
    )
    return [{"type": row[0], "content": row[1] if isinstance(row[1], dict) else {}} for row in cur.fetchall()]


def _create_page(cur, workspace_id: str, user_id: str, title: str, parent_id: str | None) -> str:
    if parent_id:
        _assert_page(cur, workspace_id, parent_id)
    page_id = str(uuid.uuid4())
    cur.execute(
        """
        SELECT COALESCE(MAX(position), -1) + 1
        FROM workspace_pages
        WHERE workspace_id = %s::uuid AND archived = FALSE
        """,
        (workspace_id,),
    )
    position = float(cur.fetchone()[0])
    cur.execute(
        """
        INSERT INTO workspace_pages (id, workspace_id, parent_id, created_by, title, icon, position, properties)
        VALUES (%s::uuid, %s::uuid, %s::uuid, %s::uuid, %s, '📎', %s, %s)
        """,
        (
            page_id,
            workspace_id,
            parent_id,
            user_id,
            title,
            position,
            Json({"status": "Active", "tags": ["import"]}),
        ),
    )
    return page_id


def _assert_page(cur, workspace_id: str, page_id: str) -> None:
    try:
        parsed = str(uuid.UUID(page_id))
    except ValueError as exc:
        raise PlatformError(400, "page_id không hợp lệ.") from exc
    cur.execute(
        "SELECT 1 FROM workspace_pages WHERE id = %s::uuid AND workspace_id = %s::uuid AND archived = FALSE",
        (parsed, workspace_id),
    )
    if not cur.fetchone():
        raise PlatformError(404, "Không tìm thấy trang trong workspace này.")


def _lock_file(cur, workspace_id: str, file_id: str):
    try:
        parsed = str(uuid.UUID(file_id))
    except ValueError as exc:
        raise PlatformError(400, "file_id không hợp lệ.") from exc
    cur.execute(
        """
        SELECT f.id::text, f.status, f.progress, f.error, f.chunk_count, f.byte_size, f.filename, f.kind,
               COALESCE(s.chunk_count, 0), COALESCE(s.status, ''), COALESCE(s.vector_ready, FALSE),
               f.page_id::text, f.block_id::text
        FROM workspace_files f
        LEFT JOIN page_index_state s ON s.page_id = f.page_id
        WHERE f.id = %s::uuid AND f.workspace_id = %s::uuid
        """,
        (parsed, workspace_id),
    )
    return cur.fetchone()


def _public_fields(row: tuple) -> dict[str, Any]:
    file_status, progress, error, file_chunks, byte_size, filename, kind = row[1], row[2], row[3], row[4], row[5], row[6], row[7]
    index_chunks, index_status, vector_ready = int(row[8]), row[9], bool(row[10])
    chunks = index_chunks or int(file_chunks or 0)
    status = _display_status(file_status, index_status, chunks)
    return {
        "filename": filename,
        "byte_size": int(byte_size),
        "kind": kind,
        "status": status,
        "progress": int(progress or 0),
        "chunk_count": chunks,
        "error": error,
        "vector_ready": vector_ready,
    }


def _public_file(row: tuple) -> dict[str, Any]:
    fields = _public_fields(row)
    fields.update({"id": row[0], "page_id": row[11], "block_id": row[12]})
    return fields


def _display_status(file_status: str, index_status: str, chunks: int) -> str:
    if file_status == "failed" or (index_status in {"failed", "dead"} and chunks == 0):
        return "failed"
    if chunks > 0 and index_status in {"indexed", "indexing", "failed", "dead"}:
        return "indexed"
    if file_status in {"queued", "parsing"}:
        return file_status
    if file_status not in _STATUSES:
        return "indexing"
    return "indexing" if file_status == "indexing" else file_status


def _filename(filename: str) -> tuple[str, str]:
    name = Path(filename or "").name.strip()
    lower = name.lower()
    if lower.endswith(".pdf"):
        return name[:240], "pdf"
    if lower.endswith(".md") or lower.endswith(".markdown"):
        return name[:240], "markdown"
    raise PlatformError(400, "Chỉ nhận file .pdf hoặc .md.")


def _title(filename: str) -> str:
    stem = Path(filename).stem.replace("_", " ").replace("-", " ").strip()
    return (stem or filename)[:200]


def _heading(stripped: str) -> tuple[str, str] | None:
    for marker, kind in (("### ", "heading3"), ("## ", "heading2"), ("# ", "heading1")):
        if stripped.startswith(marker) and stripped[len(marker) :].strip():
            return kind, stripped[len(marker) :].strip()[:8000]
    return None
