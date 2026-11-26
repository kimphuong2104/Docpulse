export interface DocumentItem {
  id: number;
  filename: string;
  file_hash: string;
  namespace: string;
  created_at: string | null;
  chunk_count: number;
}

export interface CitationItem {
  source_id: number;
  chunk_id: number | null;
  document_id: number | null;
  filename: string | null;
  chunk_index: number | null;
  heading_path: string | null;
  content: string | null;
  is_hit: boolean;
  score: number;
}

export interface LatencyMs {
  rewrite_ms: number;
  embed_ms: number;
  retrieve_ms: number;
  expand_ms: number;
  generate_ms: number;
  total_ms: number;
}

export interface QueryResponse {
  question: string;
  mode: string;
  answer: string;
  grounded: boolean;
  citations: CitationItem[];
  raw_hit_count: number;
  context_chunk_count: number;
  latency_ms: LatencyMs;
  rewritten_query: string;
  session_id?: string | null;
}

export interface IngestResult {
  status: string;
  document_id: number;
  filename: string;
  namespace: string;
  file_hash: string;
  chunk_count: number;
}

export interface IngestJob {
  id: string;
  filename: string;
  namespace: string;
  status: "pending" | "parsing" | "indexing" | "ready" | "failed" | "duplicate";
  stage: string;
  progress: number;
  attempt: number;
  max_attempts: number;
  document_id: number | null;
  chunk_count: number | null;
  error: string | null;
}

export interface QueryRequest {
  question: string;
  top_k: number;
  mode: "hybrid" | "dense";
  neighbor_window: number;
  min_score: number;
  filename?: string;
  heading_contains?: string;
  session_id?: string;
}

export interface ChatTurn {
  id: string;
  question: string;
  pending: boolean;
  result?: QueryResponse;
  error?: string;
}

export interface ChatSession {
  id: string;
  title: string;
  namespace: string;
  summary: string | null;
  created_at: string | null;
  updated_at: string | null;
}

export interface SessionMessage {
  id: number;
  role: "user" | "assistant";
  content: string;
  rewritten_query: string | null;
  citations: CitationItem[];
  latency_ms: Record<string, unknown>;
  created_at: string | null;
}

export interface SessionDetail extends ChatSession {
  messages: SessionMessage[];
}
