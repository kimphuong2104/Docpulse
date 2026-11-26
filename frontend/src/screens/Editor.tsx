import { Paperclip, Plus } from "lucide-react";
import { FormEvent, useEffect, useRef, useState } from "react";
import { useNavigate, useParams } from "react-router-dom";
import { ago, api, loadSession, readError, uploadForm } from "../api/client";

type FileContent = {
  text?: string;
  checked?: boolean;
  view?: string;
  rows?: unknown[];
  source?: string;
  file_id?: string;
  filename?: string;
  byte_size?: number;
  kind?: string;
  status?: string;
  progress?: number;
  chunk_count?: number;
  pdf_page?: number | null;
  error?: string | null;
  vector_ready?: boolean;
};

type Block = {
  id: string;
  type: string;
  content: FileContent;
  position: number;
};

type Page = {
  id: string;
  title: string;
  icon: string;
  version: number;
  properties: { status?: string; tags?: string[] };
  owner_name: string | null;
  index_status: string;
  vector_ready: boolean;
  index_error: string | null;
  chunk_count: number;
  indexed_at: string | null;
  blocks: Block[];
  role: string;
};

type Task = { id: string; title: string; status: string; priority: string; area: string };
type Citation = { page_id: string; title: string; snippet: string; pdf_page?: number };
type Done = {
  model?: string;
  latency_ms?: number;
  ttft_ms?: number;
  tokens?: number;
  cache_hit?: boolean;
  grounded?: boolean;
  citations?: Citation[];
};
type Attachment = { id: string; pageId: string; filename: string; status: string; chunkCount: number; error?: string | null };

const STATUSES = ["Todo", "In Progress", "In Review", "Completed"];

const COMMANDS = [
  { command: "/file", label: "File", accept: ".pdf,.md,.markdown,application/pdf,text/markdown" },
  { command: "/pdf", label: "PDF", accept: ".pdf,application/pdf" },
];

function formatSize(bytes?: number): string {
  const size = bytes || 0;
  if (size < 1024) return `${size} B`;
  if (size < 1024 * 1024) return `${(size / 1024).toFixed(1)} KB`;
  return `${(size / 1024 / 1024).toFixed(1)} MB`;
}

export function Editor() {
  const { workspaceId = "", pageId = "" } = useParams();
  const navigate = useNavigate();
  const [page, setPage] = useState<Page | null>(null);
  const [blocks, setBlocks] = useState<Block[]>([]);
  const [tasks, setTasks] = useState<Task[]>([]);
  const [view, setView] = useState<"table" | "board">("table");
  const [error, setError] = useState("");
  const [prompt, setPrompt] = useState("");
  const [answer, setAnswer] = useState("");
  const [meta, setMeta] = useState<Done | null>(null);
  const [asking, setAsking] = useState(false);
  const [live, setLive] = useState(false);
  const [command, setCommand] = useState("");
  const [dragging, setDragging] = useState(false);
  const [pending, setPending] = useState<{ name: string; pct: number } | null>(null);
  const [attachments, setAttachments] = useState<Attachment[]>([]);
  const dirty = useRef(false);
  const version = useRef(1);
  const pagePicker = useRef<HTMLInputElement>(null);
  const contextPicker = useRef<HTMLInputElement>(null);

  async function load() {
    const [pageResponse, taskResponse] = await Promise.all([
      api(`/workspaces/${workspaceId}/pages/${pageId}`),
      api(`/workspaces/${workspaceId}/tasks`),
    ]);
    if (!pageResponse.ok) {
      setError(await readError(pageResponse));
      return;
    }
    const body = (await pageResponse.json()) as Page;
    setPage(body);
    setBlocks(body.blocks);
    version.current = body.version;
    dirty.current = false;
    if (taskResponse.ok) {
      const taskBody = (await taskResponse.json()) as { tasks: Task[] };
      setTasks(taskBody.tasks);
    }
  }

  useEffect(() => {
    void load();
  }, [workspaceId, pageId]);

  useEffect(() => {
    const session = loadSession();
    if (!session) return;
    const proto = location.protocol === "https:" ? "wss:" : "ws:";
    const socket = new WebSocket(`${proto}//${location.host}/api/ws/${workspaceId}/pages/${pageId}`);
    socket.onopen = () => socket.send(JSON.stringify({ type: "auth", token: session.access_token }));
    socket.onmessage = (event) => {
      const message = JSON.parse(event.data) as { type?: string };
      if (message.type === "ready") setLive(true);
      if (message.type === "changed" && !dirty.current) void load();
    };
    socket.onclose = () => setLive(false);
    const ping = window.setInterval(() => {
      if (socket.readyState === WebSocket.OPEN) socket.send(JSON.stringify({ type: "ping" }));
    }, 15000);
    return () => {
      window.clearInterval(ping);
      socket.close();
    };
  }, [workspaceId, pageId]);

  async function persist(next: Block[]) {
    const response = await api(`/workspaces/${workspaceId}/pages/${pageId}/blocks`, {
      method: "PUT",
      body: JSON.stringify({
        version: version.current,
        blocks: next.map((block) => ({ id: block.id, type: block.type, content: block.content })),
      }),
    });
    if (response.status === 409) {
      setError("Trang đã được lưu ở một phiên khác.");
      await load();
      return;
    }
    if (!response.ok) {
      setError(await readError(response));
      return;
    }
    const body = (await response.json()) as Page;
    version.current = body.version;
    setPage(body);
    dirty.current = false;
  }

  function editText(id: string, text: string) {
    dirty.current = true;
    setBlocks((current) => current.map((block) => (block.id === id ? { ...block, content: { ...block.content, text } } : block)));
  }

  async function rename(title: string) {
    const response = await api(`/workspaces/${workspaceId}/pages/${pageId}`, {
      method: "PATCH",
      body: JSON.stringify({ title }),
    });
    if (!response.ok) {
      setError(await readError(response));
      return;
    }
    setPage((await response.json()) as Page);
  }

  async function cycle(task: Task) {
    const status = STATUSES[(STATUSES.indexOf(task.status) + 1) % STATUSES.length];
    const response = await api(`/workspaces/${workspaceId}/tasks/${task.id}`, {
      method: "PATCH",
      body: JSON.stringify({ status }),
    });
    if (!response.ok) {
      setError(await readError(response));
      return;
    }
    const body = (await response.json()) as { tasks: Task[] };
    setTasks(body.tasks);
  }

  function applyPage(body: Page) {
    setPage(body);
    setBlocks(body.blocks);
    version.current = body.version;
    dirty.current = false;
  }

  async function sendFiles(list: FileList | File[], mode: "attach" | "context") {
    if (dirty.current) await persist(blocks);
    for (const file of Array.from(list)) {
      setPending({ name: file.name, pct: 0 });
      const form = new FormData();
      form.append("file", file);
      form.append("mode", mode);
      form.append("page_id", pageId);
      let response: Response;
      try {
        response = await uploadForm(`/workspaces/${workspaceId}/files`, form, (pct) => setPending({ name: file.name, pct }));
      } catch (reason) {
        setError(reason instanceof Error ? reason.message : "Không gửi được file.");
        setPending(null);
        return;
      }
      if (!response.ok) {
        setError(await readError(response));
        setPending(null);
        return;
      }
      const body = (await response.json()) as { file: { id: string; page_id: string; filename: string; status: string; chunk_count: number; error?: string | null }; page: Page };
      if (mode === "attach") applyPage(body.page);
      else {
        setAttachments((current) => [
          ...current,
          { id: body.file.id, pageId: body.file.page_id, filename: body.file.filename, status: body.file.status, chunkCount: body.file.chunk_count, error: body.file.error },
        ]);
      }
      setPending(null);
    }
  }

  useEffect(() => {
    const watched = [
      ...blocks.filter((block) => block.type === "file" && block.content.file_id && block.content.status !== "failed" && !(block.content.chunk_count && block.content.status === "indexed")).map((block) => block.content.file_id as string),
      ...attachments.filter((item) => item.status !== "indexed" && item.status !== "failed").map((item) => item.id),
    ];
    if (!watched.length) return;
    const timer = window.setInterval(() => {
      void (async () => {
        for (const id of watched) {
          const response = await api(`/workspaces/${workspaceId}/files/${id}`);
          if (!response.ok) continue;
          const file = (await response.json()) as { id: string; status: string; chunk_count: number; progress: number; error?: string | null; filename: string };
          setBlocks((current) =>
            current.map((block) =>
              block.content.file_id === file.id && !block.content.pdf_page
                ? { ...block, content: { ...block.content, status: file.status, chunk_count: file.chunk_count, progress: file.progress, error: file.error, filename: file.filename } }
                : block,
            ),
          );
          setAttachments((current) =>
            current.map((item) => (item.id === file.id ? { ...item, status: file.status, chunkCount: file.chunk_count, error: file.error } : item)),
          );
        }
      })();
    }, 2000);
    return () => window.clearInterval(timer);
  }, [attachments, blocks, workspaceId]);

  useEffect(() => {
    const match = window.location.hash.match(/^#pdf-(\d+)$/);
    if (!match) return;
    document.querySelector(`[data-pdf-page="${match[1]}"]`)?.scrollIntoView({ block: "center" });
  }, [blocks]);

  function openCitation(citation: Citation) {
    if (citation.page_id === pageId && citation.pdf_page) {
      document.querySelector(`[data-pdf-page="${citation.pdf_page}"]`)?.scrollIntoView({ block: "center" });
      return;
    }
    navigate(`/w/${workspaceId}/p/${citation.page_id}${citation.pdf_page ? `#pdf-${citation.pdf_page}` : ""}`);
  }

  async function ask(event: FormEvent) {
    event.preventDefault();
    if (prompt.trim().length < 2) return;
    setAsking(true);
    setAnswer("");
    setMeta(null);
    setError("");
    const response = await api(`/workspaces/${workspaceId}/ai/stream`, {
      method: "POST",
      body: JSON.stringify({ prompt, page_id: pageId, scope: "page", attachment_page_ids: attachments.map((item) => item.pageId) }),
    });
    if (!response.ok || !response.body) {
      setError(await readError(response));
      setAsking(false);
      return;
    }
    const reader = response.body.getReader();
    const decoder = new TextDecoder();
    let buffer = "";
    while (true) {
      const step = await reader.read();
      if (step.done) break;
      buffer += decoder.decode(step.value, { stream: true });
      const parts = buffer.split("\n\n");
      buffer = parts.pop() || "";
      for (const part of parts) {
        const name = part.match(/^event: (.+)$/m)?.[1];
        const dataLine = part.match(/^data: (.+)$/m)?.[1];
        if (!name || !dataLine) continue;
        const data = JSON.parse(dataLine) as Done & { text?: string; detail?: string };
        if (name === "token" && data.text) setAnswer((current) => current + data.text);
        if (name === "done") setMeta(data);
        if (name === "error" && data.detail) setError(data.detail);
      }
    }
    setAsking(false);
  }

  async function accept() {
    const additions: Block[] = answer
      .split(/\n+/)
      .map((text) => text.trim())
      .filter(Boolean)
      .map((text) => ({
        id: crypto.randomUUID(),
        type: "paragraph",
        position: 0,
        content: { text, checked: false, view: "table", rows: [], source: "" },
      }));
    const next = [...blocks, ...additions];
    dirty.current = true;
    setBlocks(next);
    await persist(next);
    setAnswer("");
    setMeta(null);
  }

  const columns = STATUSES.filter((status) => tasks.some((task) => task.status === status));
  const indexLabel = !page
    ? "Loading"
    : page.vector_ready
      ? `Vectors ready · ${page.chunk_count} chunks · ${ago(page.indexed_at)}`
      : `${page.index_status} · ${page.chunk_count} chunks`;

  return (
    <div className="flex h-full">
      <article
        className="relative min-w-0 flex-1 overflow-y-auto px-12 py-8"
        onDragOver={(event) => {
          event.preventDefault();
          setDragging(true);
        }}
        onDragLeave={() => setDragging(false)}
        onDrop={(event) => {
          event.preventDefault();
          setDragging(false);
          if (event.dataTransfer.files.length) void sendFiles(event.dataTransfer.files, "attach");
        }}
      >
        {dragging && <div className="pointer-events-none absolute inset-3 z-10 flex items-center justify-center rounded-3xl border-2 border-dashed border-[#5b4dff] bg-white/80 text-sm text-[#5b4dff]">Drop a PDF or Markdown file</div>}
        <span className="inline-flex items-center gap-1 rounded-full bg-amber-50 px-2 py-1 text-xs font-medium text-amber-700">{page?.icon} {page?.properties.status || "Draft"}</span>
        <input
          value={page?.title ?? ""}
          onChange={(event) => setPage((current) => (current ? { ...current, title: event.target.value } : current))}
          onBlur={(event) => void rename(event.target.value)}
          className="mt-3 w-full bg-transparent text-4xl font-semibold tracking-tight outline-none"
        />
        <div className="mt-4 grid max-w-xl grid-cols-[120px_1fr] gap-y-2 text-sm">
          <span className="text-stone-400">Owner</span>
          <span className="font-medium">{page?.owner_name || "—"}</span>
          <span className="text-stone-400">Index</span>
          <span className={page?.vector_ready ? "text-emerald-600" : "text-amber-600"}>{indexLabel}</span>
          <span className="text-stone-400">Sync</span>
          <span>{live ? "Channel open" : "Channel closed"}</span>
          <span className="text-stone-400">Tags</span>
          <span className="space-x-1 text-xs">
            {(page?.properties.tags ?? []).map((tag) => (
              <i key={tag} className="rounded-md bg-[#f4f3fb] px-2 py-0.5 not-italic text-stone-500">{tag}</i>
            ))}
          </span>
        </div>
        {page?.index_error && <p className="mt-3 max-w-3xl text-sm text-rose-600">{page.index_error}</p>}
        <div className="mt-6 max-w-3xl space-y-3">
          {blocks.map((block) =>
            block.type === "file" ? (
              <FileCard key={block.id} block={block} />
            ) : block.content.source === "tasks" || block.type === "database" ? (
              <section key={block.id} className="rounded-2xl border border-[#eceaf3] bg-white">
                <div className="flex items-center justify-between border-b border-[#eceaf3] px-4 py-3">
                  <p className="font-medium">{block.content.text || "Tasks"}</p>
                  <div className="flex gap-1 text-sm">
                    <button type="button" onClick={() => setView("table")} className={`rounded-lg px-3 py-1 ${view === "table" ? "bg-[#f4f3fb]" : ""}`}>Table View</button>
                    <button type="button" onClick={() => setView("board")} className={`rounded-lg px-3 py-1 ${view === "board" ? "bg-[#f4f3fb]" : ""}`}>Kanban Board</button>
                  </div>
                </div>
                {view === "table" ? (
                  <table className="w-full text-sm">
                    <tbody>
                      {tasks.map((task) => (
                        <tr key={task.id} className="border-t border-[#f4f3fb]">
                          <td className="px-4 py-2">{task.title}</td>
                          <td className="px-2 text-stone-500">{task.priority}</td>
                          <td className="px-2">
                            <button type="button" onClick={() => void cycle(task)} className="text-[#5b4dff]">{task.status}</button>
                          </td>
                          <td className="px-4 text-stone-400">{task.area}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                ) : (
                  <div className="grid grid-cols-2 gap-3 p-4">
                    {columns.map((status) => (
                      <div key={status}>
                        <p className="text-xs text-stone-400">{status}</p>
                        {tasks.filter((task) => task.status === status).map((task) => (
                          <button key={task.id} type="button" onClick={() => void cycle(task)} className="mt-2 block w-full rounded-xl bg-[#f6f5fb] px-3 py-2 text-left text-sm">{task.title}</button>
                        ))}
                      </div>
                    ))}
                  </div>
                )}
              </section>
            ) : (
              <textarea
                key={block.id}
                data-pdf-page={block.content.pdf_page || undefined}
                value={block.content.text || ""}
                onChange={(event) => editText(block.id, event.target.value)}
                onBlur={() => {
                  if (dirty.current) void persist(blocks);
                }}
                rows={block.type.startsWith("heading") ? 1 : 3}
                className={`w-full resize-none bg-transparent outline-none ${block.type === "heading2" ? "text-2xl font-semibold" : "leading-7 text-stone-700"}`}
              />
            ),
          )}
        </div>
        {pending && <FileProgress name={pending.name} pct={pending.pct} />}
        <div className="relative mt-4 max-w-3xl">
          <div className="flex items-center gap-2">
            <button type="button" onClick={() => pagePicker.current?.click()} className="flex h-8 w-8 items-center justify-center rounded-lg text-stone-400 hover:bg-white" title="Attach a file">
              <Plus size={16} />
            </button>
            <input
              value={command}
              onChange={(event) => setCommand(event.target.value)}
              onKeyDown={(event) => {
                if (event.key !== "Enter") return;
                const match = COMMANDS.find((item) => command.trim() === item.command || command.trim().startsWith(item.command));
                if (!match || !pagePicker.current) return;
                event.preventDefault();
                pagePicker.current.accept = match.accept;
                pagePicker.current.click();
                setCommand("");
              }}
              placeholder="Type /file or /pdf, or drop a file"
              className="w-full bg-transparent text-sm text-stone-500 outline-none"
            />
          </div>
          {command.startsWith("/") && (
            <div className="absolute z-10 mt-1 w-56 rounded-xl border border-[#eceaf3] bg-white p-1 shadow-lg">
              {COMMANDS.filter((item) => item.command.startsWith(command.trim()) || command.trim().startsWith(item.command)).map((item) => (
                <button
                  key={item.command}
                  type="button"
                  onClick={() => {
                    if (!pagePicker.current) return;
                    pagePicker.current.accept = item.accept;
                    pagePicker.current.click();
                    setCommand("");
                  }}
                  className="block w-full rounded-lg px-3 py-2 text-left text-sm hover:bg-[#f6f5fb]"
                >
                  {item.command} <span className="text-stone-400">{item.label}</span>
                </button>
              ))}
            </div>
          )}
          <input
            ref={pagePicker}
            type="file"
            hidden
            onChange={(event) => {
              if (event.target.files?.length) void sendFiles(event.target.files, "attach");
              event.target.value = "";
            }}
          />
        </div>
        {answer && (
          <div className="mt-4 max-w-3xl rounded-2xl border border-violet-100 bg-violet-50/40 p-4">
            <div className="mb-2 flex items-center justify-between text-sm">
              <span className="font-medium text-[#5b4dff]">AI proposal</span>
              <span className="rounded-md bg-white px-2 py-0.5 text-xs text-stone-400">{meta?.model || "streaming"}{meta?.cache_hit ? " · cache" : ""}</span>
            </div>
            <pre className="whitespace-pre-wrap rounded-xl bg-emerald-50 px-4 py-3 font-mono text-sm leading-6 text-emerald-800">{answer}</pre>
            <div className="mt-3 flex items-center gap-3 text-sm">
              <span className="text-stone-400">{meta ? `response ${meta.latency_ms} ms · first token ${meta.ttft_ms} ms · ${meta.tokens} tokens` : "Waiting for the model"}</span>
              <button type="button" onClick={() => { setAnswer(""); setMeta(null); }} className="ml-auto text-stone-500">Discard</button>
              <button type="button" onClick={() => void ask({ preventDefault() {} } as FormEvent)} className="rounded-lg bg-white px-3 py-1.5">Regenerate</button>
              <button type="button" onClick={() => void accept()} className="rounded-lg bg-[#5b4dff] px-3 py-1.5 text-white">Accept & Insert</button>
            </div>
          </div>
        )}
        {error && <p className="mt-4 text-sm text-rose-600">{error}</p>}
      </article>
      <aside className="flex w-[340px] shrink-0 flex-col border-l border-[#eceaf3] bg-white">
        <div className="border-b border-[#eceaf3] px-4 py-3">
          <p className="text-sm font-semibold">AI Workspace</p>
          <p className="text-xs text-stone-400">Copilot answers from this page, then waits for Accept.</p>
        </div>
        <div className="flex-1 overflow-y-auto px-4 py-4 text-sm leading-6 text-stone-600">
          {answer || "Ask about the saved text on this page. The answer is a proposal until you insert it."}
          {!!meta?.citations?.length && (
            <div className="mt-3 flex flex-wrap gap-2">
              {meta.citations.map((citation, index) => (
                <button key={`${citation.page_id}-${index}`} type="button" onClick={() => openCitation(citation)} className="rounded-full bg-[#f6f5fb] px-2 py-1 text-xs text-[#5b4dff]">
                  [{index + 1}] {citation.title}{citation.pdf_page ? ` · p.${citation.pdf_page}` : ""}
                </button>
              ))}
            </div>
          )}
        </div>
        <form onSubmit={ask} className="border-t border-[#eceaf3] p-3">
          {!!attachments.length && (
            <div className="mb-2 flex flex-wrap gap-1">
              {attachments.map((item) => (
                <span key={item.id} className="rounded-full bg-[#f6f5fb] px-2 py-1 text-[11px] text-stone-500">
                  {item.filename} · {fileBadge(item.status, item.chunkCount, item.error)}
                </span>
              ))}
            </div>
          )}
          <textarea value={prompt} onChange={(event) => setPrompt(event.target.value)} rows={3} placeholder="Ask about this page" className="w-full resize-none rounded-xl bg-[#f6f5fb] px-3 py-2 text-sm outline-none" />
          <div className="mt-2 flex items-center gap-2">
            <button type="button" onClick={() => contextPicker.current?.click()} className="flex h-9 w-9 items-center justify-center rounded-xl text-stone-400 hover:bg-[#f6f5fb]" title="Attach a reference file">
              <Paperclip size={16} />
            </button>
            <button type="submit" disabled={asking} className="flex-1 rounded-xl bg-[#5b4dff] py-2 text-sm text-white disabled:opacity-60">{asking ? "Streaming…" : "Ask"}</button>
          </div>
          <input
            ref={contextPicker}
            type="file"
            accept=".pdf,.md,.markdown,application/pdf,text/markdown"
            hidden
            onChange={(event) => {
              if (event.target.files?.length) void sendFiles(event.target.files, "context");
              event.target.value = "";
            }}
          />
        </form>
      </aside>
    </div>
  );
}

function fileBadge(status: string, chunks: number, error?: string | null): string {
  if (status === "failed") return error || "Failed";
  if (status === "queued" || status === "parsing") return "Parsing PDF…";
  if (chunks > 0) return `Indexed • ${chunks} chunks`;
  return "Indexing…";
}

function FileCard({ block }: { block: Block }) {
  const content = block.content;
  return (
    <section className="rounded-2xl border border-[#eceaf3] bg-white px-4 py-3">
      <div className="flex items-center justify-between gap-3">
        <div className="min-w-0">
          <p className="truncate text-sm font-medium">{content.filename || content.text || "File"}</p>
          <p className="text-xs text-stone-400">{formatSize(content.byte_size)} · {content.kind === "pdf" ? "PDF" : "Markdown"}</p>
        </div>
        <span className={`shrink-0 rounded-full px-2 py-1 text-xs ${content.status === "failed" ? "bg-rose-50 text-rose-600" : "bg-violet-50 text-[#5b4dff]"}`}>
          {fileBadge(content.status || "", content.chunk_count || 0, content.error)}
        </span>
      </div>
      {content.status !== "indexed" && content.status !== "failed" && (
        <div className="mt-2 h-1 rounded-full bg-[#f4f3fb]"><div className="h-1 rounded-full bg-[#5b4dff]" style={{ width: `${content.progress || 15}%` }} /></div>
      )}
    </section>
  );
}

function FileProgress({ name, pct }: { name: string; pct: number }) {
  return (
    <section className="mt-3 max-w-3xl rounded-2xl border border-[#eceaf3] bg-white px-4 py-3">
      <p className="truncate text-sm font-medium">{name}</p>
      <p className="text-xs text-stone-400">Uploading {pct}%</p>
      <div className="mt-2 h-1 rounded-full bg-[#f4f3fb]"><div className="h-1 rounded-full bg-[#5b4dff]" style={{ width: `${pct}%` }} /></div>
    </section>
  );
}
