"""Hàng đợi nạp tài liệu. PostgreSQL vừa lưu trạng thái vừa là hàng đợi (SKIP LOCKED)."""

import hashlib
import json
import shutil
import time
import uuid
from pathlib import Path
from typing import Any

from psycopg import errors

from app.config import UPLOAD_DIR
from app.db import connect

TERMINAL = {"ready", "failed", "duplicate"}
_RATE: dict[str, list[float]] = {}

_COLUMNS = """
    id, filename, namespace, status, stage, progress, attempt, max_attempts,
    idempotency_key, file_path, document_id, chunk_count, error, created_at, updated_at
"""


def _row_to_job(row: tuple) -> dict[str, Any]:
    return {
        "id": str(row[0]),
        "filename": row[1],
        "namespace": row[2],
        "status": row[3],
        "stage": row[4],
        "progress": int(row[5]),
        "attempt": int(row[6]),
        "max_attempts": int(row[7]),
        "idempotency_key": row[8],
        "file_path": row[9],
        "document_id": row[10],
        "chunk_count": row[11],
        "error": row[12],
        "created_at": row[13].isoformat() if row[13] else None,
        "updated_at": row[14].isoformat() if row[14] else None,
    }


def allow_upload(client_key: str, limit: int = 20) -> bool:
    now = time.time()
    recent = [stamp for stamp in _RATE.get(client_key, []) if now - stamp < 60]
    if len(recent) >= limit:
        _RATE[client_key] = recent
        return False
    recent.append(now)
    _RATE[client_key] = recent
    return True


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def get_job(job_id: str) -> dict[str, Any] | None:
    with connect() as conn:
        with conn.cursor() as cur:
            cur.execute(f"SELECT {_COLUMNS} FROM ingestion_jobs WHERE id = %s::uuid", (job_id,))
            row = cur.fetchone()
    return _row_to_job(row) if row else None


def list_jobs(limit: int = 20) -> dict[str, Any]:
    with connect() as conn:
        with conn.cursor() as cur:
            cur.execute(
                f"SELECT {_COLUMNS} FROM ingestion_jobs ORDER BY created_at DESC LIMIT %s",
                (limit,),
            )
            rows = cur.fetchall()
    jobs = [_row_to_job(row) for row in rows]
    return {"total": len(jobs), "jobs": jobs}


def _insert_job(cur, **fields) -> dict[str, Any]:
    cur.execute(
        f"""
        INSERT INTO ingestion_jobs (
            id, filename, namespace, status, stage, progress, idempotency_key, file_path,
            document_id, chunk_count, error
        )
        VALUES (%s::uuid, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        RETURNING {_COLUMNS}
        """,
        (
            fields["id"],
            fields["filename"],
            fields["namespace"],
            fields["status"],
            fields["stage"],
            fields["progress"],
            fields["idempotency_key"],
            fields["file_path"],
            fields.get("document_id"),
            fields.get("chunk_count"),
            fields.get("error"),
        ),
    )
    return _row_to_job(cur.fetchone())


def accept_upload(source: Path, filename: str, namespace: str, file_hash: str) -> dict[str, Any]:
    """Ghi job. File đã có trong kho thì kết thúc ngay, không xếp hàng."""
    namespace = namespace.strip() or "default"
    job_id = str(uuid.uuid4())
    destination = Path(UPLOAD_DIR) / f"{job_id}.pdf"
    destination.parent.mkdir(parents=True, exist_ok=True)

    with connect() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT id FROM documents WHERE file_hash = %s", (file_hash,))
            existing = cur.fetchone()
            if existing:
                job = _insert_job(
                    cur,
                    id=job_id,
                    filename=filename,
                    namespace=namespace,
                    status="duplicate",
                    stage="already_indexed",
                    progress=100,
                    idempotency_key=file_hash,
                    file_path="",
                    document_id=existing[0],
                    chunk_count=None,
                    error=None,
                )
                conn.commit()
                return job

            cur.execute(
                """
                SELECT id::text FROM ingestion_jobs
                WHERE idempotency_key = %s AND status IN ('pending', 'parsing', 'indexing')
                ORDER BY created_at DESC
                LIMIT 1
                """,
                (file_hash,),
            )
            active = cur.fetchone()
            if active:
                conn.commit()
                found = get_job(active[0])
                if found:
                    return found

            shutil.copyfile(source, destination)
            try:
                job = _insert_job(
                    cur,
                    id=job_id,
                    filename=filename,
                    namespace=namespace,
                    status="pending",
                    stage="queued",
                    progress=0,
                    idempotency_key=file_hash,
                    file_path=str(destination),
                    document_id=None,
                    chunk_count=None,
                    error=None,
                )
            except errors.UniqueViolation:
                conn.rollback()
                destination.unlink(missing_ok=True)
                with conn.cursor() as again:
                    again.execute(
                        """
                        SELECT id::text FROM ingestion_jobs
                        WHERE idempotency_key = %s AND status IN ('pending', 'parsing', 'indexing')
                        LIMIT 1
                        """,
                        (file_hash,),
                    )
                    row = again.fetchone()
                return get_job(row[0]) if row else {}
        conn.commit()
    return job


def update_job(job_id: str, **fields) -> dict[str, Any]:
    assignments = ["updated_at = CURRENT_TIMESTAMP"]
    values: list[Any] = []
    for key, value in fields.items():
        assignments.append(f"{key} = %s")
        values.append(value)
    values.append(job_id)
    with connect() as conn:
        with conn.cursor() as cur:
            cur.execute(
                f"""
                UPDATE ingestion_jobs
                SET {", ".join(assignments)}
                WHERE id = %s::uuid
                RETURNING {_COLUMNS}
                """,
                values,
            )
            row = cur.fetchone()
        conn.commit()
    if not row:
        raise KeyError(job_id)
    return _row_to_job(row)


def claim_job() -> dict[str, Any] | None:
    with connect() as conn:
        with conn.cursor() as cur:
            cur.execute(
                f"""
                WITH picked AS (
                    SELECT id FROM ingestion_jobs
                    WHERE status = 'pending'
                      AND next_run_at <= CURRENT_TIMESTAMP
                    ORDER BY created_at
                    FOR UPDATE SKIP LOCKED
                    LIMIT 1
                )
                UPDATE ingestion_jobs AS job
                SET status = 'parsing',
                    stage = 'parsing',
                    progress = 10,
                    attempt = job.attempt + 1,
                    error = NULL,
                    updated_at = CURRENT_TIMESTAMP
                FROM picked
                WHERE job.id = picked.id
                RETURNING job.id, job.filename, job.namespace, job.status, job.stage, job.progress,
                          job.attempt, job.max_attempts, job.idempotency_key, job.file_path,
                          job.document_id, job.chunk_count, job.error, job.created_at, job.updated_at
                """
            )
            row = cur.fetchone()
        conn.commit()
    return _row_to_job(row) if row else None


def recover_stuck_jobs() -> int:
    with connect() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                UPDATE ingestion_jobs
                SET status = 'pending', stage = 'requeued', next_run_at = CURRENT_TIMESTAMP,
                    updated_at = CURRENT_TIMESTAMP
                WHERE status IN ('parsing', 'indexing')
                """
            )
            recovered = cur.rowcount
        conn.commit()
    return recovered


def replay_job(job_id: str) -> dict[str, Any] | None:
    job = get_job(job_id)
    if not job or job["status"] != "failed":
        return None
    if not job["file_path"] or not Path(job["file_path"]).exists():
        return update_job(job_id, error="File tạm không còn để chạy lại.")
    with connect() as conn:
        with conn.cursor() as cur:
            cur.execute(
                f"""
                UPDATE ingestion_jobs
                SET status = 'pending', stage = 'replayed', progress = 0, error = NULL,
                    attempt = 0, next_run_at = CURRENT_TIMESTAMP, updated_at = CURRENT_TIMESTAMP
                WHERE id = %s::uuid
                RETURNING {_COLUMNS}
                """,
                (job_id,),
            )
            row = cur.fetchone()
        conn.commit()
    return _row_to_job(row) if row else None


def iter_job_events(job_id: str):
    """SSE bằng cách đọc trạng thái trong database cho tới khi job kết thúc."""
    started = time.time()
    while time.time() - started < 900:
        current = get_job(job_id)
        if not current:
            yield 'event: error\ndata: {"detail": "missing"}\n\n'
            return
        yield f"data: {json.dumps(current, ensure_ascii=False)}\n\n"
        if current["status"] in TERMINAL:
            return
        time.sleep(2)


def schedule_retry(job_id: str, delay_seconds: float, error: str) -> None:
    with connect() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                UPDATE ingestion_jobs
                SET status = 'pending',
                    stage = 'retrying',
                    error = %s,
                    next_run_at = CURRENT_TIMESTAMP + (%s || ' seconds')::interval,
                    updated_at = CURRENT_TIMESTAMP
                WHERE id = %s::uuid
                """,
                (error, str(int(delay_seconds)), job_id),
            )
        conn.commit()
