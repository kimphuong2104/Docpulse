import type {
  ChatSession,
  DocumentItem,
  IngestJob,
  QueryRequest,
  QueryResponse,
  SessionDetail,
} from "../types/rag";

async function readError(response: Response): Promise<string> {
  try {
    const body = (await response.json()) as { detail?: unknown };
    if (typeof body.detail === "string") return body.detail;
  } catch {
    /* response was not JSON */
  }
  return "Máy chủ trả về lỗi.";
}

export async function getHealth(): Promise<boolean> {
  const response = await fetch("/api/health");
  return response.ok;
}

export async function listDocuments(namespace?: string): Promise<DocumentItem[]> {
  const query = namespace && namespace !== "all" ? `?namespace=${encodeURIComponent(namespace)}` : "";
  const response = await fetch(`/api/documents${query}`);
  if (!response.ok) throw new Error(await readError(response));
  const body = (await response.json()) as { documents: DocumentItem[] };
  return body.documents;
}

export async function deleteDocument(id: number): Promise<void> {
  const response = await fetch(`/api/documents/${id}`, { method: "DELETE" });
  if (!response.ok) throw new Error(await readError(response));
}

export async function ingestPdf(file: File, namespace: string): Promise<IngestJob> {
  const data = new FormData();
  data.append("file", file);
  data.append("namespace", namespace || "default");
  const response = await fetch("/api/ingest", { method: "POST", body: data });
  if (!response.ok) throw new Error(await readError(response));
  return (await response.json()) as IngestJob;
}

export async function listJobs(): Promise<IngestJob[]> {
  const response = await fetch("/api/jobs");
  if (!response.ok) throw new Error(await readError(response));
  const body = (await response.json()) as { jobs: IngestJob[] };
  return body.jobs;
}

export async function replayJob(id: string): Promise<IngestJob> {
  const response = await fetch(`/api/jobs/${id}/replay`, { method: "POST" });
  if (!response.ok) throw new Error(await readError(response));
  return (await response.json()) as IngestJob;
}

export async function askQuestion(payload: QueryRequest): Promise<QueryResponse> {
  const response = await fetch("/api/query", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  if (!response.ok) throw new Error(await readError(response));
  return (await response.json()) as QueryResponse;
}

export async function listSessions(namespace?: string): Promise<ChatSession[]> {
  const query = namespace && namespace !== "all" ? `?namespace=${encodeURIComponent(namespace)}` : "";
  const response = await fetch(`/api/sessions${query}`);
  if (!response.ok) throw new Error(await readError(response));
  const body = (await response.json()) as { sessions: ChatSession[] };
  return body.sessions;
}

export async function createSession(namespace: string, title?: string): Promise<ChatSession> {
  const response = await fetch("/api/sessions", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ namespace, title }),
  });
  if (!response.ok) throw new Error(await readError(response));
  return (await response.json()) as ChatSession;
}

export async function getSession(id: string): Promise<SessionDetail> {
  const response = await fetch(`/api/sessions/${id}`);
  if (!response.ok) throw new Error(await readError(response));
  return (await response.json()) as SessionDetail;
}

export async function deleteSession(id: string): Promise<void> {
  const response = await fetch(`/api/sessions/${id}`, { method: "DELETE" });
  if (!response.ok) throw new Error(await readError(response));
}
