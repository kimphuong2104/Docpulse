import type { CitationItem } from "../types/rag";

interface CitationListProps {
  citations: CitationItem[];
  activeId: number | null;
}

export function CitationList({ citations, activeId }: CitationListProps) {
  if (citations.length === 0) return null;

  return (
    <details className="mt-3 rounded-xl border border-stone-200 bg-stone-50" open>
      <summary className="cursor-pointer px-3 py-2 text-sm font-medium text-stone-700">
        Nguồn trích dẫn ({citations.length} chunks)
      </summary>
      <ul className="space-y-2 px-3 pb-3">
        {citations.map((item) => (
          <li
            key={`${item.source_id}-${item.chunk_id}`}
            id={`cite-${item.source_id}`}
            className={`rounded-lg border bg-white p-3 text-sm ${
              activeId === item.source_id ? "border-emerald-500" : "border-stone-200"
            }`}
          >
            <div className="mb-2 flex flex-wrap gap-2 text-xs">
              <span className="rounded-full bg-stone-900 px-2 py-0.5 text-white">Nguồn {item.source_id}</span>
              <span
                className={`rounded-full px-2 py-0.5 ${
                  item.is_hit ? "bg-emerald-100 text-emerald-800" : "bg-amber-100 text-amber-800"
                }`}
              >
                {item.is_hit ? "HIT" : "NEIGHBOR"}
              </span>
              <span className="text-stone-500">#{item.chunk_index ?? "-"}</span>
              <span className="text-stone-500">{item.score.toFixed(4)}</span>
            </div>
            <p className="text-xs text-stone-500">
              {item.filename || "Không rõ file"}
              {item.heading_path ? ` · ${item.heading_path}` : ""}
            </p>
            {item.content && (
              <p className="mt-2 whitespace-pre-wrap text-sm leading-6 text-stone-800">{item.content}</p>
            )}
          </li>
        ))}
      </ul>
    </details>
  );
}
