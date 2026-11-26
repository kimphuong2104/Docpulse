import { useQuery, useQueryClient } from "@tanstack/react-query";
import { ChevronRight, FileText, Plus, Sparkles, Star, Trash2 } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import { askPage, createPage, deletePage, getPage, listPages, patchPage, savePageBlocks } from "../api/pages";
import type { EditorBlock, PageDetail, PageSummary } from "../types/page";
import { BlockEditor, toEditorBlocks } from "./BlockEditor";

interface WorkspaceProps {
  onOpenLibrary: () => void;
}

export function Workspace({ onOpenLibrary }: WorkspaceProps) {
  const queryClient = useQueryClient();
  const pages = useQuery({ queryKey: ["pages"], queryFn: () => listPages(false) });
  const trash = useQuery({ queryKey: ["pages", "trash"], queryFn: () => listPages(true) });
  const [pageId, setPageId] = useState<string | null>(null);
  const [title, setTitle] = useState("");
  const [icon, setIcon] = useState("📄");
  const [blocks, setBlocks] = useState<EditorBlock[]>([]);
  const [sync, setSync] = useState("Đã lưu");
  const [aiOpen, setAiOpen] = useState(true);
  const [aiMode, setAiMode] = useState<"ask" | "summarize" | "continue" | "translate">("summarize");
  const [aiPrompt, setAiPrompt] = useState("");
  const [aiAnswer, setAiAnswer] = useState("");
  const [aiBusy, setAiBusy] = useState(false);
  const [aiError, setAiError] = useState("");
  const [showTrash, setShowTrash] = useState(false);
  const versionRef = useRef(1);
  const blocksRef = useRef(blocks);
  const pageRef = useRef<string | null>(null);
  const timer = useRef<number | null>(null);
  const saving = useRef(false);
  const pending = useRef<EditorBlock[] | null>(null);
  blocksRef.current = blocks;
  pageRef.current = pageId;

  useEffect(() => {
    if (!pageId && pages.data && pages.data.length > 0) {
      void openPage(pages.data[0].id);
    }
  }, [pages.data, pageId]);

  async function openPage(id: string) {
    if (timer.current) window.clearTimeout(timer.current);
    const previous = pageRef.current;
    if (previous && previous !== id) {
      await savePageBlocks(previous, versionRef.current, blocksRef.current).catch(() => undefined);
    }
    const detail = await getPage(id);
    applyDetail(detail);
  }

  function applyDetail(detail: PageDetail) {
    setPageId(detail.id);
    setTitle(detail.title);
    setIcon(detail.icon || "📄");
    setBlocks(toEditorBlocks(detail.blocks));
    versionRef.current = detail.version;
    setSync("Đã lưu");
    setAiAnswer("");
    setAiError("");
  }

  function scheduleSave(next: EditorBlock[]) {
    setBlocks(next);
    setSync("Đang lưu…");
    if (timer.current) window.clearTimeout(timer.current);
    timer.current = window.setTimeout(() => {
      void persist(next);
    }, 500);
  }

  async function persist(next: EditorBlock[]) {
    const id = pageRef.current;
    if (!id) return;
    if (saving.current) {
      pending.current = next;
      return;
    }
    saving.current = true;
    try {
      const saved = await savePageBlocks(id, versionRef.current, next);
      versionRef.current = saved.version;
      setSync("Đã lưu");
    } catch (error) {
      setSync(error instanceof Error ? error.message : "Không lưu được");
    } finally {
      saving.current = false;
      const queued = pending.current;
      pending.current = null;
      if (queued) void persist(queued);
    }
  }

  async function addPage(parentId?: string | null) {
    const created = await createPage(parentId);
    await queryClient.invalidateQueries({ queryKey: ["pages"] });
    applyDetail(created);
  }

  async function rename(value: string) {
    setTitle(value);
    if (!pageId) return;
    await patchPage(pageId, { title: value });
    void queryClient.invalidateQueries({ queryKey: ["pages"] });
  }

  async function archiveCurrent() {
    if (!pageId) return;
    await patchPage(pageId, { archived: true });
    setPageId(null);
    setBlocks([]);
    await queryClient.invalidateQueries({ queryKey: ["pages"] });
  }

  async function restore(id: string) {
    await patchPage(id, { archived: false });
    await queryClient.invalidateQueries({ queryKey: ["pages"] });
    await openPage(id);
  }

  async function runAi() {
    if (!pageId) return;
    setAiBusy(true);
    setAiError("");
    try {
      const answer = await askPage(pageId, aiMode, aiPrompt);
      setAiAnswer(answer);
    } catch (error) {
      setAiError(error instanceof Error ? error.message : "AI không trả lời được.");
    } finally {
      setAiBusy(false);
    }
  }

  function acceptAi() {
    if (!aiAnswer.trim()) return;
    const next = [
      ...blocksRef.current,
      {
        id: crypto.randomUUID(),
        type: "callout" as const,
        text: aiAnswer.trim(),
        checked: false,
        view: "table" as const,
        rows: [],
      },
    ];
    scheduleSave(next);
    setAiAnswer("");
  }

  const tree = pages.data ?? [];

  return (
    <div className="flex h-screen bg-white text-stone-900">
      <aside className="flex w-64 shrink-0 flex-col border-r border-stone-200 bg-stone-100">
        <div className="flex items-center justify-between px-3 py-3">
          <p className="text-sm font-semibold">DocPulse</p>
          <button type="button" onClick={() => void addPage(null)} className="rounded p-1 hover:bg-stone-200" aria-label="Trang mới">
            <Plus size={16} />
          </button>
        </div>
        <button type="button" onClick={onOpenLibrary} className="mx-2 mb-2 flex items-center gap-2 rounded-md px-2 py-1.5 text-left text-sm hover:bg-stone-200">
          <FileText size={15} /> Kho PDF
        </button>
        <div className="min-h-0 flex-1 overflow-y-auto px-2">
          <PageTree
            pages={tree.filter((page) => page.favorite)}
            all={tree}
            activeId={pageId}
            onOpen={(id) => void openPage(id)}
            onAdd={(id) => void addPage(id)}
            label="Yêu thích"
          />
          <PageTree
            pages={tree.filter((page) => !page.parent_id)}
            all={tree}
            activeId={pageId}
            onOpen={(id) => void openPage(id)}
            onAdd={(id) => void addPage(id)}
          />
        </div>
        <button type="button" onClick={() => setShowTrash((value) => !value)} className="flex items-center gap-2 px-3 py-2 text-sm text-stone-500">
          <Trash2 size={14} /> Thùng rác
        </button>
        {showTrash && (
          <ul className="max-h-40 overflow-y-auto px-2 pb-3 text-sm">
            {(trash.data ?? []).map((page) => (
              <li key={page.id} className="flex items-center justify-between gap-2 px-2 py-1">
                <span className="truncate">{page.title || "Không tiêu đề"}</span>
                <button type="button" className="text-xs text-stone-500" onClick={() => void restore(page.id)}>
                  Khôi phục
                </button>
                <button
                  type="button"
                  className="text-xs text-red-500"
                  onClick={() => void deletePage(page.id).then(() => queryClient.invalidateQueries({ queryKey: ["pages"] }))}
                >
                  Xóa
                </button>
              </li>
            ))}
          </ul>
        )}
      </aside>
      <main className="min-w-0 flex-1 overflow-y-auto">
        <div className="mx-auto flex max-w-3xl items-center justify-between px-16 pt-6 text-xs text-stone-400">
          <span>{sync}</span>
          <button type="button" onClick={() => setAiOpen((value) => !value)} className="inline-flex items-center gap-1">
            <Sparkles size={14} /> Trợ lý
          </button>
        </div>
        {pageId ? (
          <article className="mx-auto max-w-3xl px-16 pb-24">
            <div className="mb-2 flex items-center gap-2">
              <input
                value={icon}
                onChange={(event) => setIcon(event.target.value.slice(0, 4))}
                onBlur={() => void patchPage(pageId, { icon })}
                className="w-12 bg-transparent text-3xl outline-none"
                aria-label="Biểu tượng"
              />
              <button
                type="button"
                onClick={() => {
                  void patchPage(pageId, { favorite: !tree.find((page) => page.id === pageId)?.favorite }).then(() =>
                    queryClient.invalidateQueries({ queryKey: ["pages"] }),
                  );
                }}
                className="text-stone-400"
                aria-label="Ghim"
              >
                <Star size={16} />
              </button>
              <button type="button" onClick={() => void archiveCurrent()} className="text-xs text-stone-400">
                Đưa vào thùng rác
              </button>
            </div>
            <textarea
              value={title}
              onChange={(event) => void rename(event.target.value)}
              placeholder="Không tiêu đề"
              rows={1}
              className="mb-6 w-full resize-none bg-transparent text-4xl font-semibold outline-none"
            />
            <BlockEditor blocks={blocks} onChange={scheduleSave} />
          </article>
        ) : (
          <p className="px-16 py-20 text-stone-400">Chọn hoặc tạo một trang.</p>
        )}
      </main>
      {aiOpen && (
        <aside className="flex w-80 shrink-0 flex-col border-l border-stone-200 bg-stone-50">
          <div className="border-b border-stone-200 px-4 py-3">
            <p className="text-sm font-semibold">Trợ lý của trang</p>
            <p className="text-xs text-stone-500">Chỉ đọc nội dung trang đang mở. Chèn khi bạn đồng ý.</p>
          </div>
          <div className="flex flex-wrap gap-1 px-4 py-3">
            {(
              [
                ["summarize", "Tóm tắt"],
                ["continue", "Viết tiếp"],
                ["translate", "Dịch"],
                ["ask", "Hỏi"],
              ] as const
            ).map(([mode, label]) => (
              <button
                key={mode}
                type="button"
                onClick={() => setAiMode(mode)}
                className={`rounded-full px-2 py-1 text-xs ${aiMode === mode ? "bg-stone-900 text-white" : "bg-white text-stone-600"}`}
              >
                {label}
              </button>
            ))}
          </div>
          {aiMode === "ask" && (
            <textarea
              value={aiPrompt}
              onChange={(event) => setAiPrompt(event.target.value)}
              placeholder="Hỏi về trang này..."
              className="mx-4 mb-3 min-h-20 rounded-lg border border-stone-200 bg-white px-3 py-2 text-sm outline-none"
            />
          )}
          <div className="px-4">
            <button
              type="button"
              disabled={aiBusy || !pageId}
              onClick={() => void runAi()}
              className="w-full rounded-lg bg-stone-900 py-2 text-sm text-white disabled:opacity-50"
            >
              {aiBusy ? "Đang đọc trang…" : "Chạy"}
            </button>
          </div>
          <div className="mt-3 flex-1 overflow-y-auto px-4 pb-4 text-sm leading-6 text-stone-700">
            {aiError && <p className="text-red-600">{aiError}</p>}
            {aiAnswer && (
              <>
                <p className="whitespace-pre-wrap">{aiAnswer}</p>
                <button type="button" onClick={acceptAi} className="mt-3 rounded-md bg-emerald-700 px-3 py-1.5 text-white">
                  Chèn vào trang
                </button>
              </>
            )}
          </div>
        </aside>
      )}
    </div>
  );
}

function PageTree({
  pages,
  all,
  activeId,
  onOpen,
  onAdd,
  label,
}: {
  pages: PageSummary[];
  all: PageSummary[];
  activeId: string | null;
  onOpen: (id: string) => void;
  onAdd: (id: string) => void;
  label?: string;
}) {
  if (pages.length === 0 && !label) return null;
  return (
    <div className="mb-2">
      {label && <p className="px-2 py-1 text-xs text-stone-400">{label}</p>}
      {pages.map((page) => (
        <PageNode key={`${label ?? "tree"}-${page.id}`} page={page} all={all} activeId={activeId} onOpen={onOpen} onAdd={onAdd} depth={0} />
      ))}
    </div>
  );
}

function PageNode({
  page,
  all,
  activeId,
  onOpen,
  onAdd,
  depth,
}: {
  page: PageSummary;
  all: PageSummary[];
  activeId: string | null;
  onOpen: (id: string) => void;
  onAdd: (id: string) => void;
  depth: number;
}) {
  const [open, setOpen] = useState(true);
  const children = all.filter((item) => item.parent_id === page.id);
  return (
    <div>
      <div
        className={`group flex items-center gap-1 rounded-md pr-1 text-sm ${activeId === page.id ? "bg-stone-200" : "hover:bg-stone-200/70"}`}
        style={{ paddingLeft: 8 + depth * 12 }}
      >
        <button type="button" onClick={() => setOpen((value) => !value)} className="text-stone-400" aria-label="Mở nhánh">
          <ChevronRight size={14} className={open ? "rotate-90" : ""} />
        </button>
        <button type="button" onClick={() => onOpen(page.id)} className="flex min-w-0 flex-1 items-center gap-1 py-1 text-left">
          <span>{page.icon}</span>
          <span className="truncate">{page.title || "Không tiêu đề"}</span>
        </button>
        <button type="button" onClick={() => onAdd(page.id)} className="opacity-0 group-hover:opacity-100" aria-label="Trang con">
          <Plus size={14} />
        </button>
      </div>
      {open &&
        children.map((child) => (
          <PageNode key={child.id} page={child} all={all} activeId={activeId} onOpen={onOpen} onAdd={onAdd} depth={depth + 1} />
        ))}
    </div>
  );
}
