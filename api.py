import os
import shutil
import tempfile
from pathlib import Path
from typing import Any

import psycopg
from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

from ingest import ingest_pdf_file
from query import run_query_pipeline

DATABASE_URL = os.environ["DATABASE_URL"]
MAX_UPLOAD_MB = int(os.getenv("MAX_UPLOAD_MB", "25"))

app = FastAPI(
    title="DocPulse — Hybrid RAG & Document Intelligence API",
    description="ETL/ELT PDF ingestion (Docling) + PostgreSQL Hybrid Search (pgvector halfvec + tsvector RRF) + Grounded QA.",
    version="1.0.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)


class QueryRequest(BaseModel):
    question: str = Field(..., min_length=2, description="Câu hỏi cần truy vấn từ kho tài liệu")
    top_k: int = Field(default=5, ge=1, le=20)
    mode: str = Field(default="hybrid", pattern="^(hybrid|dense)$")
    neighbor_window: int = Field(default=1, ge=0, le=3)
    min_score: float = Field(default=0.0, ge=0.0)
    filename: str | None = Field(default=None, description="Lọc theo tên file cụ thể (tùy chọn)")
    heading_contains: str | None = Field(default=None, description="Lọc theo tiêu đề mục trong metadata (tùy chọn)")


class CitationItem(BaseModel):
    source_id: int
    chunk_id: int | None = None
    document_id: int | None = None
    filename: str | None = None
    chunk_index: int | None = None
    heading_path: str | None = None
    is_hit: bool = True
    score: float = 0.0


class QueryResponse(BaseModel):
    question: str
    mode: str
    answer: str
    grounded: bool
    citations: list[CitationItem]
    raw_hit_count: int
    context_chunk_count: int
    latency_ms: dict[str, float]


@app.get("/health")
def health_check() -> dict[str, Any]:
    try:
        with psycopg.connect(DATABASE_URL) as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT 1;")
                cur.fetchone()
        return {"status": "ok", "database": "connected"}
    except Exception as exc:
        raise HTTPException(status_code=503, detail=f"Database unavailable: {exc}") from exc


@app.get("/documents")
def list_documents(namespace: str | None = None) -> dict[str, Any]:
    sql = """
        SELECT
            d.id,
            d.filename,
            d.file_hash,
            d.namespace,
            d.created_at,
            COUNT(c.id) AS chunk_count
        FROM documents d
        LEFT JOIN chunks c ON c.document_id = d.id
        WHERE (%s::text IS NULL OR d.namespace = %s)
        GROUP BY d.id
        ORDER BY d.created_at DESC;
    """
    with psycopg.connect(DATABASE_URL) as conn:
        with conn.cursor() as cur:
            cur.execute(sql, (namespace, namespace))
            rows = cur.fetchall()

    docs = [
        {
            "id": row[0],
            "filename": row[1],
            "file_hash": row[2],
            "namespace": row[3],
            "created_at": row[4].isoformat() if row[4] else None,
            "chunk_count": int(row[5]),
        }
        for row in rows
    ]
    return {"total": len(docs), "documents": docs}


@app.post("/ingest")
async def ingest_endpoint(
    file: UploadFile = File(...),
    namespace: str = Form(default="default"),
) -> dict[str, Any]:
    if not file.filename or not file.filename.lower().endswith(".pdf"):
        raise HTTPException(status_code=400, detail="Chỉ hỗ trợ định dạng file .pdf")

    suffix = Path(file.filename).suffix or ".pdf"
    tmp_path: str | None = None

    try:
        with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
            shutil.copyfileobj(file.file, tmp)
            tmp_path = tmp.name

        size_mb = os.path.getsize(tmp_path) / (1024 * 1024)
        if size_mb > MAX_UPLOAD_MB:
            raise HTTPException(
                status_code=413,
                detail=f"File vượt quá giới hạn {MAX_UPLOAD_MB}MB (kích thước: {size_mb:.2f}MB)",
            )

        return ingest_pdf_file(
            pdf_path=tmp_path,
            original_filename=file.filename,
            namespace=namespace.strip() or "default",
        )
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Lỗi khi ingest PDF: {exc}") from exc
    finally:
        await file.close()
        if tmp_path and os.path.exists(tmp_path):
            os.remove(tmp_path)


@app.post("/query", response_model=QueryResponse)
def query_endpoint(payload: QueryRequest) -> dict[str, Any]:
    try:
        return run_query_pipeline(
            question=payload.question,
            top_k=payload.top_k,
            mode=payload.mode,
            neighbor_window=payload.neighbor_window,
            min_score=payload.min_score,
            filename=payload.filename,
            heading_contains=payload.heading_contains,
        )
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Lỗi truy vấn RAG: {exc}") from exc
