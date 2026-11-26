import { FormEvent, useEffect, useMemo, useState } from "react";
import { useNavigate, useParams } from "react-router-dom";
import { api, readError } from "../api/client";

type Match = {
  page_id: string;
  title: string;
  icon: string;
  snippet: string;
  score: number;
  score_kind: string;
};

export function SearchModal({ open, onClose }: { open: boolean; onClose: () => void }) {
  const { workspaceId = "" } = useParams();
  const navigate = useNavigate();
  const [query, setQuery] = useState("");
  const [matches, setMatches] = useState<Match[]>([]);
  const [mode, setMode] = useState("");
  const [answer, setAnswer] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const trimmed = query.trim();

  useEffect(() => {
    if (!open || trimmed.length < 2) {
      setMatches([]);
      setMode("");
      return;
    }
    const handle = window.setTimeout(async () => {
      const response = await api(`/workspaces/${workspaceId}/search?q=${encodeURIComponent(trimmed)}`);
      if (!response.ok) {
        setError(await readError(response));
        return;
      }
      const body = (await response.json()) as { mode: string; matches: Match[] };
      setMode(body.mode);
      setMatches(body.matches);
      setError("");
    }, 250);
    return () => window.clearTimeout(handle);
  }, [open, trimmed, workspaceId]);

  const summary = useMemo(() => (mode === "hybrid" ? "Keyword + vector" : mode === "keyword" ? "Keyword" : ""), [mode]);

  if (!open) return null;

  async function ask(event: FormEvent) {
    event.preventDefault();
    if (trimmed.length < 2) return;
    setBusy(true);
    setAnswer("");
    setError("");
    const response = await api(`/workspaces/${workspaceId}/ai/stream`, {
      method: "POST",
      body: JSON.stringify({ prompt: trimmed, scope: "workspace" }),
    });
    if (!response.ok || !response.body) {
      setError(await readError(response));
      setBusy(false);
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
      for (const part of parts) applyEvent(part, setAnswer, setError);
    }
    setBusy(false);
  }

  return (
    <div className="fixed inset-0 z-30 flex items-start justify-center bg-black/30 px-4 pt-24" onClick={onClose}>
      <div className="w-full max-w-2xl rounded-2xl bg-white p-4 shadow-2xl" onClick={(event) => event.stopPropagation()}>
        <form onSubmit={ask}>
          <input autoFocus value={query} onChange={(event) => setQuery(event.target.value)} placeholder="Search this workspace" className="w-full rounded-xl bg-[#f6f5fb] px-4 py-3 text-sm outline-none" />
        </form>
        <div className="mt-2 flex items-center justify-between text-xs text-stone-400">
          <span>{summary || "Type at least 2 characters"}</span>
          <button type="button" onClick={onClose}>Esc</button>
        </div>
        {error && <p className="mt-3 text-sm text-rose-600">{error}</p>}
        {answer && <p className="mt-3 rounded-xl bg-[#f6f5fb] p-3 text-sm leading-6">{answer}</p>}
        <ul className="mt-3 max-h-80 space-y-2 overflow-y-auto">
          {matches.map((match) => (
            <li key={`${match.page_id}-${match.snippet}`}>
              <button
                type="button"
                onClick={() => {
                  navigate(`/w/${workspaceId}/p/${match.page_id}`);
                  onClose();
                }}
                className="w-full rounded-xl border border-[#eceaf3] px-3 py-2 text-left"
              >
                <span className="text-sm font-medium">{match.icon} {match.title}</span>
                <span className="float-right text-xs text-stone-400">{match.score_kind} {match.score}</span>
                <span className="mt-1 block text-xs leading-5 text-stone-500">{match.snippet}</span>
              </button>
            </li>
          ))}
        </ul>
        {busy && <p className="mt-2 text-xs text-stone-400">Streaming answer…</p>}
        <p className="mt-2 text-[11px] text-stone-400">Scores come from the retrieval query for pages you can read.</p>
      </div>
    </div>
  );
}

function applyEvent(part: string, setAnswer: (value: string | ((current: string) => string)) => void, setError: (value: string) => void) {
  const event = part.match(/^event: (.+)$/m)?.[1];
  const dataLine = part.match(/^data: (.+)$/m)?.[1];
  if (!event || !dataLine) return;
  const data = JSON.parse(dataLine) as { text?: string; detail?: string };
  if (event === "token" && data.text) setAnswer((current) => current + data.text);
  if (event === "error" && data.detail) setError(data.detail);
}
