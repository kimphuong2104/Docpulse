import { FileText, Trash2, Upload } from "lucide-react";
import { useRef, useState } from "react";
import type { DocumentItem, IngestJob } from "../types/rag";

const MAX_BYTES = 25 * 1024 * 1024;

interface SidebarDocsProps {
  documents: DocumentItem[];
  loadingDocs: boolean;
  namespace: string;
  onNamespaceChange: (value: string) => void;
  mode: "hybrid" | "dense";
  onModeChange: (mode: "hybrid" | "dense") => void;
  topK: number;
  onTopKChange: (value: number) => void;
  neighborWindow: number;
  onNeighborWindowChange: (value: number) => void;
  headingContains: string;
  onHeadingContainsChange: (value: string) => void;
  onUpload: (file: File, namespace: string) => void;
  uploading: boolean;
  uploadMessage: string;
  jobs: IngestJob[];
  onReplay: (id: string) => void;
  onDelete: (id: number, filename: string) => void;
}

const JOB_LABEL: Record<IngestJob["status"], string> = {
  pending: "Đang chờ",
  parsing: "Đang bóc tách",
  indexing: "Đang lập chỉ mục",
  ready: "Sẵn sàng",
  failed: "Lỗi",
  duplicate: "Đã có trong kho",
};

function shortHash(hash: string): string {
  return hash.slice(0, 10);
}

function formatDate(value: string | null): string {
  if (!value) return "";
  return new Date(value).toLocaleDateString("vi-VN");
}

export function SidebarDocs({
  documents,
  loadingDocs,
  namespace,
  onNamespaceChange,
  mode,
  onModeChange,
  topK,
  onTopKChange,
  neighborWindow,
  onNeighborWindowChange,
  headingContains,
  onHeadingContainsChange,
  onUpload,
  uploading,
  uploadMessage,
  jobs,
  onReplay,
  onDelete,
}: SidebarDocsProps) {
  const inputRef = useRef<HTMLInputElement>(null);
  const [dragOver, setDragOver] = useState(false);
  const [localError, setLocalError] = useState("");
  const [uploadNamespace, setUploadNamespace] = useState("default");
  const visibleDocuments =
    namespace === "all" ? documents : documents.filter((doc) => doc.namespace === namespace);

  function acceptFile(file: File | undefined) {
    if (!file) return;
    if (!file.name.toLowerCase().endsWith(".pdf")) {
      setLocalError("Chỉ nhận file PDF.");
      return;
    }
    if (file.size > MAX_BYTES) {
      setLocalError("File vượt quá 25MB.");
      return;
    }
    setLocalError("");
    onUpload(file, uploadNamespace.trim() || "default");
  }

  return (
    <aside className="flex h-full w-[340px] shrink-0 flex-col overflow-y-auto border-r border-stone-800 bg-stone-950 text-stone-100">
      <section className="border-b border-stone-800 p-4">
        <p className="mb-3 text-xs font-semibold tracking-wide text-stone-400">1. NẠP TÀI LIỆU</p>
        <button
          type="button"
          onClick={() => inputRef.current?.click()}
          onDragOver={(event) => {
            event.preventDefault();
            setDragOver(true);
          }}
          onDragLeave={() => setDragOver(false)}
          onDrop={(event) => {
            event.preventDefault();
            setDragOver(false);
            acceptFile(event.dataTransfer.files[0]);
          }}
          className={`flex w-full flex-col items-center gap-2 rounded-xl border border-dashed px-3 py-6 text-sm ${
            dragOver ? "border-emerald-400 bg-emerald-950" : "border-stone-600 bg-stone-900"
          }`}
        >
          <Upload size={18} />
          Kéo thả file PDF vào đây
          <span className="text-xs text-stone-400">Tối đa 25MB</span>
        </button>
        <input
          ref={inputRef}
          type="file"
          accept="application/pdf,.pdf"
          className="hidden"
          onChange={(event) => {
            acceptFile(event.target.files?.[0]);
            event.target.value = "";
          }}
        />
        <label className="mt-3 block text-xs text-stone-400">
          Namespace
          <input
            value={uploadNamespace}
            onChange={(event) => setUploadNamespace(event.target.value)}
            className="mt-1 w-full rounded-lg border border-stone-700 bg-stone-900 px-3 py-2 text-sm text-stone-100"
          />
        </label>
        <p className="mt-2 min-h-5 text-xs text-amber-300">{localError || uploadMessage}</p>
        {uploading && <p className="text-xs text-emerald-300">Đang đưa file vào hàng đợi...</p>}
        {jobs.length > 0 && (
          <ul className="mt-3 space-y-2">
            {jobs.map((job) => (
              <li key={job.id} className="rounded-lg bg-stone-900 px-3 py-2 text-xs">
                <span className="block text-stone-200">{job.filename}</span>
                <span className="mt-1 block text-stone-400">
                  {JOB_LABEL[job.status]} · {job.progress}%
                  {job.attempt > 0 ? ` · lần ${job.attempt}` : ""}
                </span>
                <span className="mt-1 block h-1 overflow-hidden rounded bg-stone-800">
                  <span className="block h-full bg-emerald-500" style={{ width: `${job.progress}%` }} />
                </span>
                {job.error && <span className="mt-1 block text-red-300">{job.error}</span>}
                {job.status === "failed" && (
                  <button type="button" onClick={() => onReplay(job.id)} className="mt-1 text-emerald-300">
                    Chạy lại
                  </button>
                )}
              </li>
            ))}
          </ul>
        )}
      </section>

      <section className="border-b border-stone-800 p-4">
        <div className="mb-3 flex items-center justify-between">
          <p className="text-xs font-semibold tracking-wide text-stone-400">2. KHO TÀI LIỆU</p>
          <span className="text-xs text-stone-500">{loadingDocs ? "..." : `${visibleDocuments.length} files`}</span>
        </div>
        <label className="mb-3 block text-xs text-stone-400">
          Lọc namespace
          <select
            value={namespace}
            onChange={(event) => onNamespaceChange(event.target.value)}
            className="mt-1 w-full rounded-lg border border-stone-700 bg-stone-900 px-3 py-2 text-sm"
          >
            <option value="all">all</option>
            {[...new Set(documents.map((doc) => doc.namespace))].map((item) => (
              <option key={item} value={item}>
                {item}
              </option>
            ))}
          </select>
        </label>
        <p className="mb-2 text-xs text-stone-400">Câu hỏi tìm trên toàn bộ kho, không cần chọn file.</p>
        <ul className="space-y-2">
          {visibleDocuments.map((doc) => (
            <li key={doc.id} className="rounded-lg bg-stone-900 px-3 py-2 text-sm text-stone-200">
              <div className="flex items-start gap-2">
                <FileText size={16} className="mt-0.5 shrink-0" />
                <span>
                  <span className="block">{doc.filename}</span>
                  <span className="block text-xs text-stone-500">
                    {doc.chunk_count} chunks · {doc.namespace} · {shortHash(doc.file_hash)} · {formatDate(doc.created_at)}
                  </span>
                </span>
              </div>
              <button
                type="button"
                onClick={() => onDelete(doc.id, doc.filename)}
                className="flex items-center gap-1 px-3 pb-2 text-xs text-stone-500 hover:text-red-300"
              >
                <Trash2 size={12} /> Xóa
              </button>
            </li>
          ))}
        </ul>
      </section>

      <section className="p-4">
        <p className="mb-3 text-xs font-semibold tracking-wide text-stone-400">3. CẤU HÌNH TRUY XUẤT</p>
        <div className="mb-3 flex gap-4 text-sm">
          <label className="flex items-center gap-2">
            <input type="radio" checked={mode === "hybrid"} onChange={() => onModeChange("hybrid")} />
            Hybrid
          </label>
          <label className="flex items-center gap-2">
            <input type="radio" checked={mode === "dense"} onChange={() => onModeChange("dense")} />
            Dense
          </label>
        </div>
        <label className="mb-3 block text-xs text-stone-400">
          Top-K: {topK}
          <input
            type="range"
            min={1}
            max={10}
            value={topK}
            onChange={(event) => onTopKChange(Number(event.target.value))}
            className="mt-2 w-full"
          />
        </label>
        <label className="mb-3 block text-xs text-stone-400">
          Neighbor window
          <input
            type="number"
            min={0}
            max={2}
            value={neighborWindow}
            onChange={(event) => onNeighborWindowChange(Number(event.target.value))}
            className="mt-1 w-full rounded-lg border border-stone-700 bg-stone-900 px-3 py-2 text-sm text-stone-100"
          />
        </label>
        <label className="block text-xs text-stone-400">
          Lọc heading
          <input
            value={headingContains}
            onChange={(event) => onHeadingContainsChange(event.target.value)}
            placeholder="Lọc tiêu đề..."
            className="mt-1 w-full rounded-lg border border-stone-700 bg-stone-900 px-3 py-2 text-sm text-stone-100"
          />
        </label>
      </section>
    </aside>
  );
}
