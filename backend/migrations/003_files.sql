CREATE TABLE IF NOT EXISTS workspace_files (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    workspace_id UUID NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
    page_id UUID REFERENCES workspace_pages(id) ON DELETE SET NULL,
    block_id UUID,
    filename TEXT NOT NULL,
    kind TEXT NOT NULL CHECK (kind IN ('pdf', 'markdown')),
    byte_size INT NOT NULL,
    storage_path TEXT NOT NULL,
    sha256 TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('queued', 'parsing', 'indexing', 'indexed', 'failed')),
    progress INT NOT NULL DEFAULT 0,
    chunk_count INT NOT NULL DEFAULT 0,
    error TEXT,
    created_by UUID,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_workspace_files_page
ON workspace_files (page_id, created_at DESC);

CREATE INDEX IF NOT EXISTS idx_workspace_files_queue
ON workspace_files (created_at)
WHERE status = 'queued';
