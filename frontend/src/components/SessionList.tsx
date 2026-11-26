import { Plus, Trash2 } from "lucide-react";
import type { ChatSession } from "../types/rag";

interface SessionListProps {
  sessions: ChatSession[];
  loading: boolean;
  activeId: string | null;
  onCreate: () => void;
  onSelect: (id: string) => void;
  onDelete: (id: string, title: string) => void;
}

function formatUpdated(value: string | null): string {
  if (!value) return "";
  return new Date(value).toLocaleString("vi-VN", {
    hour: "2-digit",
    minute: "2-digit",
    day: "2-digit",
    month: "2-digit",
  });
}

export function SessionList({
  sessions,
  loading,
  activeId,
  onCreate,
  onSelect,
  onDelete,
}: SessionListProps) {
  return (
    <aside className="flex h-full w-64 shrink-0 flex-col border-r border-stone-200 bg-white">
      <div className="border-b border-stone-200 p-3">
        <button
          type="button"
          onClick={onCreate}
          className="flex w-full items-center justify-center gap-2 rounded-xl bg-stone-900 px-3 py-2 text-sm text-white"
        >
          <Plus size={16} /> Hội thoại mới
        </button>
      </div>
      <div className="flex-1 overflow-y-auto p-2">
        <p className="px-2 py-1 text-xs font-semibold tracking-wide text-stone-400">
          {loading ? "Đang tải..." : "HỘI THOẠI"}
        </p>
        <ul className="space-y-1">
          {sessions.map((session) => (
            <li key={session.id}>
              <button
                type="button"
                onClick={() => onSelect(session.id)}
                className={`w-full rounded-lg px-3 py-2 text-left ${
                  activeId === session.id ? "bg-emerald-50 text-emerald-900" : "hover:bg-stone-50"
                }`}
              >
                <span className="block truncate text-sm">{session.title}</span>
                <span className="block text-xs text-stone-400">{formatUpdated(session.updated_at)}</span>
              </button>
              <button
                type="button"
                onClick={() => onDelete(session.id, session.title)}
                className="flex items-center gap-1 px-3 pb-1 text-xs text-stone-400 hover:text-red-600"
              >
                <Trash2 size={12} /> Xóa
              </button>
            </li>
          ))}
        </ul>
      </div>
    </aside>
  );
}
