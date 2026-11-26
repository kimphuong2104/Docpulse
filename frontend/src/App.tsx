import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Send } from "lucide-react";
import { useEffect, useMemo, useState } from "react";
import {
  askQuestion,
  createSession,
  deleteDocument,
  deleteSession,
  getHealth,
  getSession,
  ingestPdf,
  listDocuments,
  listSessions,
  replayJob,
} from "./api/docpulse";
import { ChatMessage } from "./components/ChatMessage";
import { SessionList } from "./components/SessionList";
import { SidebarDocs } from "./components/SidebarDocs";
import type { ChatTurn, IngestJob, SessionDetail } from "./types/rag";

function messagesToTurns(detail: SessionDetail): ChatTurn[] {
  const turns: ChatTurn[] = [];
  for (const message of detail.messages) {
    if (message.role === "user") {
      turns.push({ id: `${detail.id}-${message.id}`, question: message.content, pending: false });
      continue;
    }
    const turn = turns[turns.length - 1];
    if (!turn) continue;
    const latency = message.latency_ms ?? {};
    const numberOf = (key: string) => Number(latency[key] ?? 0);
    turn.result = {
      question: turn.question,
      rewritten_query: message.rewritten_query ?? turn.question,
      mode: typeof latency.mode === "string" ? latency.mode : "hybrid",
      answer: message.content,
      grounded: typeof latency.grounded === "boolean" ? latency.grounded : message.citations.length > 0,
      citations: message.citations,
      raw_hit_count: message.citations.filter((item) => item.is_hit).length,
      context_chunk_count: message.citations.length,
      session_id: detail.id,
      latency_ms: {
        rewrite_ms: numberOf("rewrite_ms"),
        embed_ms: numberOf("embed_ms"),
        retrieve_ms: numberOf("retrieve_ms"),
        expand_ms: numberOf("expand_ms"),
        generate_ms: numberOf("generate_ms"),
        total_ms: numberOf("total_ms"),
      },
    };
  }
  return turns;
}

export default function App({ onOpenWorkspace }: { onOpenWorkspace?: () => void }) {
  const queryClient = useQueryClient();
  const [namespace, setNamespace] = useState("all");
  const [mode, setMode] = useState<"hybrid" | "dense">("hybrid");
  const [topK, setTopK] = useState(5);
  const [neighborWindow, setNeighborWindow] = useState(1);
  const [headingContains, setHeadingContains] = useState("");
  const [question, setQuestion] = useState("");
  const [turns, setTurns] = useState<ChatTurn[]>([]);
  const [activeCitation, setActiveCitation] = useState<number | null>(null);
  const [uploadMessage, setUploadMessage] = useState("");
  const [jobs, setJobs] = useState<IngestJob[]>([]);
  const [sessionId, setSessionId] = useState<string | null>(null);

  const health = useQuery({
    queryKey: ["health"],
    queryFn: getHealth,
    refetchInterval: 15000,
  });

  const documents = useQuery({
    queryKey: ["documents"],
    queryFn: () => listDocuments(),
  });

  const sessions = useQuery({
    queryKey: ["sessions", namespace],
    queryFn: () => listSessions(namespace),
  });

  const namespaces = useMemo(
    () => [...new Set((documents.data ?? []).map((doc) => doc.namespace))],
    [documents.data],
  );

  const upload = useMutation({
    mutationFn: ({ file, targetNamespace }: { file: File; targetNamespace: string }) =>
      ingestPdf(file, targetNamespace),
    onSuccess: (job) => {
      setJobs((current) => [job, ...current.filter((item) => item.id !== job.id)].slice(0, 8));
      setUploadMessage(job.status === "duplicate" ? "Tài liệu đã có trong kho, không embed lại." : "Đã đưa vào hàng đợi.");
      if (job.status === "duplicate" || job.status === "ready") {
        void queryClient.invalidateQueries({ queryKey: ["documents"] });
      }
    },
    onError: (error: Error) => setUploadMessage(error.message),
  });

  const activeJobKey = jobs
    .filter((job) => job.status === "pending" || job.status === "parsing" || job.status === "indexing")
    .map((job) => job.id)
    .join(",");

  useEffect(() => {
    if (!activeJobKey) return;
    const sources = activeJobKey.split(",").map((id) => {
      const source = new EventSource(`/api/jobs/${id}/events`);
      source.onmessage = (event) => {
        const next = JSON.parse(event.data) as IngestJob;
        setJobs((current) => current.map((item) => (item.id === next.id ? next : item)));
        if (next.status === "ready" || next.status === "duplicate") {
          setUploadMessage(
            next.status === "duplicate"
              ? "Tài liệu đã có trong kho."
              : `Đã lập chỉ mục ${next.chunk_count ?? 0} chunks.`,
          );
          void queryClient.invalidateQueries({ queryKey: ["documents"] });
          source.close();
        }
        if (next.status === "failed") {
          setUploadMessage(next.error || "Nạp tài liệu thất bại.");
          source.close();
        }
      };
      return source;
    });
    return () => sources.forEach((source) => source.close());
  }, [activeJobKey, queryClient]);

  const ask = useMutation({ mutationFn: askQuestion });

  async function openSession() {
    const created = await createSession(namespace === "all" ? "default" : namespace);
    setSessionId(created.id);
    setTurns([]);
    setQuestion("");
    void queryClient.invalidateQueries({ queryKey: ["sessions"] });
  }

  async function selectSession(id: string) {
    const detail = await getSession(id);
    setSessionId(detail.id);
    setTurns(messagesToTurns(detail));
    setActiveCitation(null);
  }

  async function removeSession(id: string, title: string) {
    if (!window.confirm(`Xóa hội thoại “${title}”?`)) return;
    await deleteSession(id);
    if (sessionId === id) {
      setSessionId(null);
      setTurns([]);
    }
    void queryClient.invalidateQueries({ queryKey: ["sessions"] });
  }

  async function submitQuestion() {
    const text = question.trim();
    if (text.length < 2 || ask.isPending) return;
    const id = crypto.randomUUID();
    setTurns((current) => [...current, { id, question: text, pending: true }]);
    setQuestion("");
    let activeId = sessionId;
    try {
      if (!activeId) {
        const created = await createSession(namespace === "all" ? "default" : namespace);
        activeId = created.id;
        setSessionId(activeId);
      }
    } catch (error) {
      const message = error instanceof Error ? error.message : "Không tạo được hội thoại.";
      setTurns((current) =>
        current.map((turn) => (turn.id === id ? { ...turn, pending: false, error: message } : turn)),
      );
      return;
    }
    ask.mutate(
      {
        question: text,
        top_k: topK,
        mode,
        neighbor_window: neighborWindow,
        min_score: 0,
        heading_contains: headingContains.trim() || undefined,
        session_id: activeId,
      },
      {
        onSuccess: (result) => {
          setTurns((current) =>
            current.map((turn) => (turn.id === id ? { ...turn, pending: false, result } : turn)),
          );
          void queryClient.invalidateQueries({ queryKey: ["sessions"] });
        },
        onError: (error) => {
          setTurns((current) =>
            current.map((turn) =>
              turn.id === id ? { ...turn, pending: false, error: error.message } : turn,
            ),
          );
        },
      },
    );
  }

  async function removeDocument(id: number, filename: string) {
    if (!window.confirm(`Xóa ${filename}?`)) return;
    await deleteDocument(id);
    void queryClient.invalidateQueries({ queryKey: ["documents"] });
  }

  return (
    <div className="flex h-screen flex-col bg-stone-100 text-stone-900">
      <header className="flex h-14 shrink-0 items-center justify-between border-b border-stone-200 bg-white px-5">
        <div>
          <button type="button" onClick={onOpenWorkspace} className="text-left">
            <p className="text-sm font-semibold">DocPulse</p>
            <p className="text-xs text-stone-500">Về không gian làm việc</p>
          </button>
        </div>
        <div className="flex items-center gap-4 text-sm">
          <span className="flex items-center gap-2">
            <span className={`h-2 w-2 rounded-full ${health.data ? "bg-emerald-500" : "bg-red-500"}`} />
            {health.data ? "Connected" : "Offline"}
          </span>
          <span className="text-stone-500">Namespace: {namespace === "all" ? namespaces.join(", ") || "all" : namespace}</span>
        </div>
      </header>
      <div className="flex min-h-0 flex-1">
        <SidebarDocs
          documents={documents.data ?? []}
          loadingDocs={documents.isLoading}
          namespace={namespace}
          onNamespaceChange={setNamespace}
          mode={mode}
          onModeChange={setMode}
          topK={topK}
          onTopKChange={setTopK}
          neighborWindow={neighborWindow}
          onNeighborWindowChange={setNeighborWindow}
          headingContains={headingContains}
          onHeadingContainsChange={setHeadingContains}
          uploading={upload.isPending}
          uploadMessage={uploadMessage}
          jobs={jobs}
          onReplay={(id) => {
            void replayJob(id).then((job) => {
              setJobs((current) => current.map((item) => (item.id === job.id ? job : item)));
              setUploadMessage("Đã xếp lại vào hàng đợi.");
            });
          }}
          onUpload={(file, targetNamespace) => {
            setUploadMessage("");
            upload.mutate({ file, targetNamespace });
          }}
          onDelete={(id, filename) => void removeDocument(id, filename)}
        />
        <SessionList
          sessions={sessions.data ?? []}
          loading={sessions.isLoading}
          activeId={sessionId}
          onCreate={() => void openSession()}
          onSelect={(id) => void selectSession(id)}
          onDelete={(id, title) => void removeSession(id, title)}
        />
        <main className="flex min-w-0 flex-1 flex-col">
          <div className="flex-1 space-y-4 overflow-y-auto px-6 py-5">
            {turns.length === 0 && (
              <p className="text-sm text-stone-500">Đặt câu hỏi trên toàn bộ kho. Câu trả lời chỉ dùng đoạn được truy xuất.</p>
            )}
            {turns.map((turn) => (
              <ChatMessage
                key={turn.id}
                turn={turn}
                activeCitation={activeCitation}
                onFocusCitation={setActiveCitation}
              />
            ))}
          </div>
          <form
            className="flex gap-2 border-t border-stone-200 bg-white p-4"
            onSubmit={(event) => {
              event.preventDefault();
              submitQuestion();
            }}
          >
            <input
              value={question}
              onChange={(event) => setQuestion(event.target.value)}
              placeholder="Nhập câu hỏi..."
              className="flex-1 rounded-xl border border-stone-300 px-4 py-3 text-sm outline-none focus:border-emerald-600"
            />
            <button
              type="submit"
              disabled={ask.isPending}
              className="inline-flex items-center gap-2 rounded-xl bg-stone-900 px-4 text-sm text-white disabled:opacity-50"
            >
              <Send size={16} /> Gửi
            </button>
          </form>
        </main>
      </div>
    </div>
  );
}
