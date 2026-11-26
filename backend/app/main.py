import asyncio
import json
import os
import tempfile
import time
import uuid
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse

from app.config import MAX_UPLOAD_MB
from app.db import connect
from app.documents import delete_document, list_documents
from app.jobs import accept_upload, allow_upload, file_sha256, get_job, iter_job_events, list_jobs, replay_job
from app.pages import (
    VersionConflict,
    ask_page,
    create_page,
    delete_page,
    get_page,
    list_pages,
    save_blocks,
    update_page,
)
from app.rag import run_query_pipeline
from app.schemas import (
    PageAiRequest,
    PageCreate,
    PagePatch,
    QueryRequest,
    QueryResponse,
    SaveBlocksRequest,
    SessionCreate,
    SessionDetail,
    SessionItem,
)
from app.realtime import bind_loop, start_listener, stop_listener
from app.routes_platform import router as platform_router
from app.sessions import answer_in_session, create_session, delete_session, get_session, list_sessions


@asynccontextmanager
async def lifespan(_app: FastAPI):
    bind_loop(asyncio.get_running_loop())
    start_listener()
    yield
    stop_listener()


app = FastAPI(
    title="DocPulse — Hybrid RAG & Document Intelligence API",
    description="ETL/ELT PDF ingestion (Docling) + PostgreSQL Hybrid Search (pgvector halfvec + tsvector RRF) + Grounded QA.",
    version="1.0.0",
    lifespan=lifespan,
)

app.include_router(platform_router)


@app.middleware("http")
async def trace_requests(request: Request, call_next):
    request_id = request.headers.get("x-request-id") or str(uuid.uuid4())
    request.state.trace_id = request_id
    started = time.perf_counter()
    response = await call_next(request)
    response.headers["X-Request-Id"] = request_id
    if request.url.path != "/health":
        print(
            json.dumps(
                {
                    "trace_id": request_id,
                    "method": request.method,
                    "path": request.url.path,
                    "status": response.status_code,
                    "ms": round((time.perf_counter() - started) * 1000, 1),
                }
            ),
            flush=True,
        )
    return response

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/health")
def health_check() -> dict[str, Any]:
    try:
        with connect() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT 1;")
                cur.fetchone()
        return {"status": "ok", "database": "connected"}
    except Exception as exc:
        raise HTTPException(status_code=503, detail=f"Dependency unavailable: {exc}") from exc


@app.get("/documents")
def documents(namespace: str | None = None) -> dict[str, Any]:
    return list_documents(namespace)


@app.delete("/documents/{document_id}")
def remove_document(document_id: int) -> dict[str, Any]:
    deleted = delete_document(document_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="Không tìm thấy tài liệu.")
    document_id, filename = deleted
    return {"deleted": True, "id": document_id, "filename": filename}


@app.post("/ingest", status_code=202)
async def ingest_endpoint(
    request: Request,
    file: UploadFile = File(...),
    namespace: str = Form(default="default"),
) -> dict[str, Any]:
    if not file.filename or not file.filename.lower().endswith(".pdf"):
        raise HTTPException(status_code=400, detail="Chỉ hỗ trợ định dạng file .pdf")

    client_key = request.client.host if request.client else "unknown"
    try:
        if not allow_upload(client_key):
            raise HTTPException(status_code=429, detail="Quá nhiều file trong một phút. Hãy thử lại sau.")
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=503, detail=f"Hàng đợi chưa sẵn sàng: {exc}") from exc

    suffix = Path(file.filename).suffix or ".pdf"
    tmp_path: str | None = None
    try:
        with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
            while True:
                block = await file.read(1024 * 1024)
                if not block:
                    break
                tmp.write(block)
            tmp_path = tmp.name

        size_mb = os.path.getsize(tmp_path) / (1024 * 1024)
        if size_mb > MAX_UPLOAD_MB:
            raise HTTPException(
                status_code=413,
                detail=f"File vượt quá giới hạn {MAX_UPLOAD_MB}MB (kích thước: {size_mb:.2f}MB)",
            )
        file_hash = file_sha256(Path(tmp_path))
        return accept_upload(Path(tmp_path), file.filename, namespace, file_hash)
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Lỗi khi nhận file: {exc}") from exc
    finally:
        await file.close()
        if tmp_path and os.path.exists(tmp_path):
            os.remove(tmp_path)


@app.get("/jobs")
def jobs(limit: int = 20) -> dict[str, Any]:
    return list_jobs(max(1, min(limit, 50)))


@app.get("/jobs/{job_id}")
def read_job(job_id: str) -> dict[str, Any]:
    job = get_job(_parse_session_id(job_id))
    if not job:
        raise HTTPException(status_code=404, detail="Không tìm thấy job.")
    return job


@app.get("/jobs/{job_id}/events")
def job_events(job_id: str) -> StreamingResponse:
    parsed = _parse_session_id(job_id)
    if not get_job(parsed):
        raise HTTPException(status_code=404, detail="Không tìm thấy job.")
    return StreamingResponse(
        iter_job_events(parsed),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@app.post("/jobs/{job_id}/replay")
def replay(job_id: str) -> dict[str, Any]:
    job = replay_job(_parse_session_id(job_id))
    if not job:
        raise HTTPException(status_code=404, detail="Chỉ chạy lại được job đã lỗi.")
    if job["status"] == "failed":
        raise HTTPException(status_code=409, detail=job["error"] or "Không chạy lại được.")
    return job


def _parse_session_id(session_id: str) -> str:
    try:
        return str(uuid.UUID(session_id))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="session_id không hợp lệ.") from exc


@app.get("/sessions", response_model=dict[str, Any])
def sessions(namespace: str | None = None) -> dict[str, Any]:
    return list_sessions(namespace)


@app.post("/sessions", response_model=SessionItem)
def open_session(payload: SessionCreate) -> dict[str, Any]:
    return create_session(title=payload.title, namespace=payload.namespace)


@app.get("/sessions/{session_id}", response_model=SessionDetail)
def read_session(session_id: str) -> dict[str, Any]:
    detail = get_session(_parse_session_id(session_id))
    if not detail:
        raise HTTPException(status_code=404, detail="Không tìm thấy phiên hội thoại.")
    return detail


@app.delete("/sessions/{session_id}")
def remove_session(session_id: str) -> dict[str, Any]:
    removed = delete_session(_parse_session_id(session_id))
    if not removed:
        raise HTTPException(status_code=404, detail="Không tìm thấy phiên hội thoại.")
    return {"deleted": True, "id": session_id}


@app.get("/pages")
def pages(archived: bool = False) -> dict[str, Any]:
    return list_pages(archived=archived)


@app.post("/pages", status_code=201)
def add_page(payload: PageCreate) -> dict[str, Any]:
    parent_id = _parse_session_id(payload.parent_id) if payload.parent_id else None
    try:
        return create_page(payload.title, parent_id, payload.icon)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.get("/pages/{page_id}")
def read_page(page_id: str) -> dict[str, Any]:
    detail = get_page(_parse_session_id(page_id))
    if not detail:
        raise HTTPException(status_code=404, detail="Không tìm thấy trang.")
    return detail


@app.patch("/pages/{page_id}")
def patch_page(page_id: str, payload: PagePatch) -> dict[str, Any]:
    fields = payload.model_dump(exclude_unset=True)
    if "parent_id" in fields and fields["parent_id"]:
        fields["parent_id"] = _parse_session_id(fields["parent_id"])
    try:
        updated = update_page(_parse_session_id(page_id), fields)
    except VersionConflict as exc:
        raise HTTPException(status_code=409, detail=exc.current) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if not updated:
        raise HTTPException(status_code=404, detail="Không tìm thấy trang.")
    return updated


@app.put("/pages/{page_id}/blocks")
def replace_blocks(page_id: str, payload: SaveBlocksRequest) -> dict[str, Any]:
    try:
        return save_blocks(
            _parse_session_id(page_id),
            payload.version,
            [block.model_dump() for block in payload.blocks],
        )
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Không tìm thấy trang.") from exc
    except VersionConflict as exc:
        raise HTTPException(status_code=409, detail=exc.current) from exc


@app.delete("/pages/{page_id}")
def remove_page(page_id: str) -> dict[str, Any]:
    removed = delete_page(_parse_session_id(page_id))
    if not removed:
        raise HTTPException(status_code=404, detail="Chỉ xóa hẳn được trang đang ở thùng rác.")
    return {"deleted": True}


@app.post("/pages/{page_id}/ai")
def page_ai(page_id: str, payload: PageAiRequest, request: Request) -> dict[str, str]:
    client_key = request.client.host if request.client else "unknown"
    try:
        return ask_page(_parse_session_id(page_id), payload.mode, payload.prompt, client_key)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Không tìm thấy trang.") from exc
    except PermissionError as exc:
        raise HTTPException(status_code=429, detail=str(exc)) from exc
    except Exception as exc:
        message = str(exc)
        if "key=" in message or "googleapis" in message:
            message = "Dịch vụ AI đang từ chối hoặc đã hết hạn mức."
        raise HTTPException(status_code=502, detail=message[:300]) from exc


@app.post("/query", response_model=QueryResponse)
def query_endpoint(payload: QueryRequest) -> dict[str, Any]:
    try:
        pipeline_kwargs = {
            "top_k": payload.top_k,
            "mode": payload.mode,
            "neighbor_window": payload.neighbor_window,
            "min_score": payload.min_score,
            "filename": payload.filename,
            "heading_contains": payload.heading_contains,
        }
        if payload.session_id:
            result = answer_in_session(
                _parse_session_id(payload.session_id),
                payload.question,
                **pipeline_kwargs,
            )
            if result is None:
                raise HTTPException(status_code=404, detail="Không tìm thấy phiên hội thoại.")
            return result
        history = [item.model_dump() for item in payload.history] if payload.history else None
        return run_query_pipeline(question=payload.question, history=history, **pipeline_kwargs)
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Lỗi truy vấn RAG: {exc}") from exc
