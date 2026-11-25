CREATE EXTENSION IF NOT EXISTS vector;

CREATE TABLE IF NOT EXISTS documents (
    id SERIAL PRIMARY KEY,
    filename TEXT NOT NULL,
    file_hash TEXT NOT NULL UNIQUE,
    namespace TEXT NOT NULL DEFAULT 'default',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS chunks (
    id SERIAL PRIMARY KEY,
    document_id INT NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
    chunk_index INT NOT NULL,
    content TEXT NOT NULL,
    metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
    -- Khớp số chiều với model embedding thực tế (ví dụ 3072 hoặc 768)
    embedding vector(3072),
    tsv tsvector,
    UNIQUE (document_id, chunk_index)
);

-- HNSW trên kiểu vector giới hạn tối đa 2000 chiều; dùng halfvec cho 3072 chiều
CREATE INDEX IF NOT EXISTS idx_chunks_embedding
ON chunks USING hnsw ((embedding::halfvec(3072)) halfvec_cosine_ops);

-- GIN index cho Full-Text Search (Hybrid RAG)
CREATE INDEX IF NOT EXISTS idx_chunks_tsv_gin
ON chunks USING GIN (tsv);

-- GIN index cho lọc metadata JSONB khi cần
CREATE INDEX IF NOT EXISTS idx_chunks_metadata_gin
ON chunks USING GIN (metadata);

-- Trigger tự động đồng bộ tsv khi INSERT hoặc UPDATE content
CREATE OR REPLACE FUNCTION chunks_tsv_trigger() RETURNS trigger AS $$
BEGIN
    NEW.tsv := to_tsvector('simple', COALESCE(NEW.content, ''));
    RETURN NEW;
END
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_chunks_tsv_update ON chunks;
CREATE TRIGGER trg_chunks_tsv_update
BEFORE INSERT OR UPDATE OF content ON chunks
FOR EACH ROW
EXECUTE FUNCTION chunks_tsv_trigger();