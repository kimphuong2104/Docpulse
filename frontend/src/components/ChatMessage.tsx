import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import type { ChatTurn } from "../types/rag";
import { CitationList } from "./CitationList";

interface ChatMessageProps {
  turn: ChatTurn;
  activeCitation: number | null;
  onFocusCitation: (sourceId: number) => void;
}

function withSourceLinks(answer: string): string {
  return answer.replace(/\[Nguồn\s+(\d+)\]/g, "[Nguồn $1](#cite-$1)");
}

export function ChatMessage({ turn, activeCitation, onFocusCitation }: ChatMessageProps) {
  const latency = turn.result?.latency_ms;

  return (
    <div className="space-y-3">
      <div className="ml-auto max-w-[80%] rounded-2xl bg-stone-900 px-4 py-3 text-sm text-white">
        {turn.question}
      </div>
      {turn.result?.rewritten_query &&
        turn.result.rewritten_query.trim() !== turn.question.trim() && (
          <p className="mr-1 text-right text-xs italic text-stone-500">
            Truy vấn độc lập: “{turn.result.rewritten_query}”
          </p>
        )}
      <div className="max-w-[92%] rounded-2xl border border-stone-200 bg-white px-4 py-3 text-sm text-stone-800">
        {turn.pending && <p className="text-stone-500">Đang truy xuất và sinh câu trả lời...</p>}
        {turn.error && <p className="text-red-700">{turn.error}</p>}
        {turn.result && (
          <>
            <div className="mb-2 flex gap-2 text-xs">
              <span
                className={`rounded-full px-2 py-0.5 ${
                  turn.result.grounded ? "bg-emerald-100 text-emerald-800" : "bg-amber-100 text-amber-800"
                }`}
              >
                {turn.result.grounded ? "Grounded" : "Out of Context / Refused"}
              </span>
              <span className="rounded-full bg-stone-100 px-2 py-0.5 text-stone-600">{turn.result.mode}</span>
            </div>
            <div className="answer">
              <ReactMarkdown
                remarkPlugins={[remarkGfm]}
                components={{
                  a: ({ href, children }) => {
                    const match = href?.match(/^#cite-(\d+)$/);
                    if (!match) {
                      return (
                        <a href={href} className="text-emerald-700 underline">
                          {children}
                        </a>
                      );
                    }
                    const sourceId = Number(match[1]);
                    return (
                      <button
                        type="button"
                        onClick={() => {
                          onFocusCitation(sourceId);
                          document.getElementById(`cite-${sourceId}`)?.scrollIntoView({ behavior: "smooth", block: "center" });
                        }}
                        className="mx-0.5 rounded-full bg-emerald-100 px-2 py-0.5 text-xs font-medium text-emerald-800"
                      >
                        {children}
                      </button>
                    );
                  },
                }}
              >
                {withSourceLinks(turn.result.answer)}
              </ReactMarkdown>
            </div>
            {latency && (
              <p className="mt-3 text-xs text-stone-500">
                Rewrite {latency.rewrite_ms ?? 0}ms · Embed {latency.embed_ms}ms · Retrieve {latency.retrieve_ms}ms ·
                Expand {latency.expand_ms}ms · Gen {latency.generate_ms}ms · Total {latency.total_ms}ms
              </p>
            )}
            <CitationList citations={turn.result.citations} activeId={activeCitation} />
          </>
        )}
      </div>
    </div>
  );
}
