# DocPulse — Hybrid RAG & Document Intelligence Pipeline

DocPulse is an end-to-end document ingestion and retrieval-augmented generation (RAG) backend for question answering over PDF documents.

## Architecture

1. **Ingestion (`ingest.py`, `chunker.py`, `document_parser.py`)**
   - IBM Docling converts a PDF to Markdown.
   - SHA-256 deduplicates the file so the same document is not embedded twice.
   - Chunks keep JSONB metadata: `filename`, `chunk_index`, `char_count`, `heading_path`.
2. **Storage (`init.sql`, PostgreSQL 16 + pgvector)**
   - Embeddings are 3072-dimensional (`gemini-embedding-001`).
   - HNSW is built on `halfvec(3072)` because a `vector` HNSW index only supports 2000 dimensions.
   - A trigger fills `tsv` on insert or content update. A GIN index serves lexical search.
3. **Retrieval (`query.py`)**
   - Dense search uses pgvector cosine distance. Lexical search uses `ts_rank_cd`.
   - Reciprocal Rank Fusion (`k = 60`) merges the two rankings.
   - Neighbor expansion adds chunks `i - 1` and `i + 1` from the same document before the answer is generated.
4. **Generation (`api.py`, `query.py`)**
   - The model may only use the retrieved context and must cite `[Nguồn X]`.
   - If the context does not contain the answer, the reply says so instead of inventing a fact.
   - Each request records `embed_ms`, `retrieve_ms`, `expand_ms`, `generate_ms`, and `total_ms`.

Retrieval metrics are computed on the raw top-5 hits, before neighbor expansion.

## Evaluation

Dataset: `eval_dataset.json`, 10 questions over `sample_report.pdf` (9 answerable questions and 1 unanswerable trap, q20).

| Retrieval | Questions | Mean Recall@5 | Hit Rate@5 |
| :--- | :--- | :---: | :---: |
| Hybrid (`pgvector` + `tsvector` + RRF) | 10 | 90.0% | 90.0% (9/10) |

The nine answerable questions each scored Recall@5 = 1.00. q20 has an empty gold set, so its recall is 0 by definition and pulls the mean to 90%. Re-run with:

```bash
python eval_recall.py
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
| POST | `/ingest` | Upload a PDF (max size `MAX_UPLOAD_MB`) |
| POST | `/query` | Hybrid retrieval and grounded answer |

## Local scripts

With `DATABASE_URL` pointing at the published Postgres port:

```bash
python ingest.py sample_report.pdf
python query.py "Trong tập Thơ Thâm Tâm ấn hành năm 1988, bài thơ sớm nhất ghi năm nào?"
python eval_recall.py
```
