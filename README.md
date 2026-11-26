# DocPulse — Hybrid RAG & Document Intelligence Pipeline

DocPulse is an end-to-end document ingestion and retrieval-augmented generation (RAG) backend for question answering over PDF documents.

## Architecture

1. **Ingestion (`backend/app/ingest.py`, `chunking.py`, `parsing.py`)**
   - IBM Docling converts a PDF to Markdown.
   - SHA-256 deduplicates the file so the same document is not embedded twice.
   - Chunks keep JSONB metadata: `filename`, `chunk_index`, `char_count`, `heading_path`.
2. **Storage (`backend/init.sql`, PostgreSQL 16 + pgvector)**
   - Embeddings are 3072-dimensional (`gemini-embedding-001`).
   - HNSW is built on `halfvec(3072)` because a `vector` HNSW index only supports 2000 dimensions.
   - A trigger fills `tsv` on insert or content update. A GIN index serves lexical search.
3. **Retrieval (`backend/app/rag/`)**
   - Dense search uses pgvector cosine distance. Lexical search uses `ts_rank_cd`.
   - Reciprocal Rank Fusion (`k = 60`) merges the two rankings.
   - Neighbor expansion adds chunks `i - 1` and `i + 1` from the same document before the answer is generated.
- A follow-up question is rewritten into a standalone query before embedding and hybrid search. The original question, plus the last four turns, is what the grounded answer sees. The first turn skips rewriting.
Ingestion from the API is asynchronous. `POST /ingest` stores the PDF, records an `ingestion_jobs` row, and returns `202` with a job id. A worker (`python -m app.worker`) claims pending rows with `FOR UPDATE SKIP LOCKED`, moves the job through `pending → parsing → indexing → ready`, and the UI follows `GET /jobs/{id}/events`. The same file hash is not embedded twice. A transient embedding error is retried with backoff; a quota or permanent error is marked `failed` and can be replayed with `POST /jobs/{id}/replay`. Uploads are limited to 20 files per minute per client.

4. **Generation (`backend/app/main.py`, `backend/app/rag/`)**
   - The model may only use the retrieved context and must cite `[Nguồn X]`.
   - If the context does not contain the answer, the reply says so instead of inventing a fact.
   - Each request records `rewrite_ms`, `embed_ms`, `retrieve_ms`, `expand_ms`, `generate_ms`, and `total_ms`.
   - Chat sessions and messages live in `chat_sessions` and `chat_messages`. `POST /query` without `session_id` stays stateless, so `eval_recall.py` does not write chat rows.

Retrieval metrics are computed on the raw top-5 hits, before neighbor expansion.

The workspace at the site root is a block editor. Pages form a tree, blocks keep an order, and a save carries the page version so a stale write is rejected. Slash commands and markdown shortcuts (`#`, `-`, `[]`) change the block type. A page can hold a small table that also renders as a kanban board. The side assistant reads only the open page and inserts text after confirmation. PDF question answering stays available from Kho PDF.

## Evaluation

Dataset: `eval_dataset.json`, 10 questions over `sample_report.pdf` (9 answerable questions and 1 unanswerable trap, q20).

| Retrieval | Questions | Mean Recall@5 | Hit Rate@5 |
| :--- | :--- | :---: | :---: |
| Hybrid (`pgvector` + `tsvector` + RRF) | 10 | 90.0% | 90.0% (9/10) |

The nine answerable questions each scored Recall@5 = 1.00. q20 has an empty gold set, so its recall is 0 by definition and pulls the mean to 90%. Re-run with:

```bash
cd backend
python -m eval.eval_recall
```

A separate MultiHop-RAG corpus lives in namespace `multihop`: all 609 articles and 2,556 questions. Load it with:

```bash
cd backend
python -m scripts.ingest_multihop_corpus
python -m eval.eval_recall ..\test_benchmark_multihop\eval_multihop.json both
```

## Quickstart

```bash
cp .env.example .env
# Set EMBEDDING_API_KEY and LLM_API_KEY.
# If port 5432 is already in use, set POSTGRES_PORT to a free port.
# If port 8000 is already in use, set API_PORT to a free port.
# DATABASE_URL inside Compose is overridden to hostname db.

docker compose up --build -d
```

- Workspace: http://localhost:${WEB_PORT:-8088}
- Swagger UI: http://localhost:${API_PORT:-8000}/docs
- Health: `GET http://localhost:${API_PORT:-8000}/health`

Ingest:

```bash
curl -X POST "http://localhost:8000/ingest" \
  -F "file=@sample_report.pdf" \
  -F "namespace=default"
```

Query:

```bash
curl -X POST "http://localhost:8000/query" \
  -H "Content-Type: application/json" \
  -d "{\"question\": \"Tập Thâm Tâm truyện ngắn năm 2000 gồm bao nhiêu truyện?\", \"mode\": \"hybrid\", \"top_k\": 5}"
```

`init.sql` is mounted into `docker-entrypoint-initdb.d` and runs only when the database volume is created for the first time.

## API

| Method | Path | Purpose |
| :--- | :--- | :--- |
| GET | `/health` | Database connectivity |
| GET | `/documents` | List ingested files and chunk counts |
| DELETE | `/documents/{document_id}` | Delete a document and its chunks |
| POST | `/ingest` | Queue a PDF (max size `MAX_UPLOAD_MB`). Returns `202` and a job |
| GET | `/jobs` | Recent ingestion jobs |
| GET | `/jobs/{job_id}` | One job |
| GET | `/jobs/{job_id}/events` | Server-sent events until the job finishes |
| POST | `/jobs/{job_id}/replay` | Requeue a failed job |
| POST | `/query` | Hybrid retrieval, query rewrite, and grounded answer |
| GET | `/sessions` | List chat sessions |
| POST | `/sessions` | Open a chat session |
| GET | `/sessions/{session_id}` | Restore a session and its messages |
| DELETE | `/sessions/{session_id}` | Delete a session and its messages |

## Local scripts

With `DATABASE_URL` pointing at the published Postgres port:

```bash
cd backend
python -m app.ingest ..\sample_report.pdf
python -m app.ingest ..\test_benchmark_multihop\pdfs --namespace multihop
python -m app.rag "Trong tập Thơ Thâm Tâm ấn hành năm 1988, bài thơ sớm nhất ghi năm nào?"
python -m eval.eval_recall
```
