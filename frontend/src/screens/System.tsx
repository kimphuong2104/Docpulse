import { useEffect, useState } from "react";
import { useParams } from "react-router-dom";
import { ago, api, readError } from "../api/client";

type Snapshot = {
  summary: string;
  worker: { name: string; healthy: boolean; seen_at: string | null; detail: string | null };
  realtime: { connections: number; rooms: number };
  queue: { pending: number; dead: number; done: number; ingestion_active: number; lag_seconds: number };
  latency: { p50_ms: number; p95_ms: number; samples: number };
  cache: { hits: number; misses: number; ratio: number | null };
  tokens: { used: number; used_today: number; quota: number };
  freshness: { indexed: number; total: number };
  stream: { page_id: string; title: string; status: string; chunk_count: number; vector_ready: boolean; error: string | null; updated_at: string | null }[];
  dead_letters: { id: string; event_type: string; page_id: string | null; filename?: string; error: string | null; attempt: number; status: string; updated_at: string | null; source: string }[];
  circuit_open: boolean;
};

export function System() {
  const { workspaceId = "" } = useParams();
  const [data, setData] = useState<Snapshot | null>(null);
  const [error, setError] = useState("");

  async function load() {
    const response = await api(`/workspaces/${workspaceId}/system`);
    if (!response.ok) {
      setError(await readError(response));
      return;
    }
    setData((await response.json()) as Snapshot);
    setError("");
  }

  useEffect(() => {
    void load();
    const handle = window.setInterval(() => void load(), 5000);
    return () => window.clearInterval(handle);
  }, [workspaceId]);

  async function replay(item: Snapshot["dead_letters"][number]) {
    const path = item.source === "outbox" ? `/workspaces/${workspaceId}/outbox/${item.id}/replay` : `/jobs/${item.id}/replay`;
    const response = await api(path, { method: "POST" });
    if (!response.ok) {
      setError(await readError(response));
      return;
    }
    await load();
  }

  const ratio = data?.cache.ratio === null || data?.cache.ratio === undefined ? "no samples" : `${Math.round(data.cache.ratio * 1000) / 10}%`;

  return (
    <div className="h-full overflow-y-auto px-8 py-6">
      <p className="text-xs font-semibold tracking-wide text-stone-400">ENGINE MONITORING / this workspace</p>
      <div className="mt-1 flex items-end justify-between">
        <div>
          <h1 className="text-3xl font-semibold">System Telemetry & Pipeline Health</h1>
          <p className="text-sm text-stone-500">Queue depth, dead letters, cache hits, and AI latency from this database.</p>
        </div>
        <span className={`rounded-full px-3 py-1 text-xs font-medium ${data?.worker.healthy && !data.queue.dead ? "bg-emerald-50 text-emerald-600" : "bg-amber-50 text-amber-700"}`}>
          ● {data?.summary || "Loading"}
        </span>
      </div>
      {error && <p className="mt-3 text-sm text-rose-600">{error}</p>}
      <div className="mt-5 grid grid-cols-4 gap-3">
        <Stat label="Active WS Connections" value={String(data?.realtime.connections ?? "—")} note={`${data?.realtime.rooms ?? 0} page rooms`} />
        <Stat label="Indexing Queue Depth" value={String(data?.queue.pending ?? "—")} note={`lag ${data?.queue.lag_seconds ?? 0}s · ingest ${data?.queue.ingestion_active ?? 0}`} />
        <Stat label="Semantic Cache Hit Rate" value={ratio} note={`${data?.cache.hits ?? 0} hits · ${data?.cache.misses ?? 0} misses`} />
        <Stat label="AI Query P95 Latency" value={data ? String(Math.round(data.latency.p95_ms)) : "—"} hint="ms" note={`p50 ${data ? Math.round(data.latency.p50_ms) : "—"} ms · ${data?.latency.samples ?? 0} samples`} />
      </div>
      <div className="mt-4 grid grid-cols-[1.4fr_0.8fr] gap-4">
        <section className="rounded-2xl border border-[#eceaf3] bg-white p-4">
          <p className="font-semibold">Page indexing <span className="ml-2 text-xs font-normal text-stone-400">{data?.freshness.indexed ?? 0}/{data?.freshness.total ?? 0} indexed</span></p>
          <table className="mt-3 w-full text-sm">
            <tbody>
              {(data?.stream ?? []).map((item) => (
                <tr key={item.page_id} className="border-t border-[#f4f3fb]">
                  <td className="py-2">{item.title}</td>
                  <td className={item.vector_ready ? "text-emerald-600" : "text-amber-600"}>{item.vector_ready ? "Indexed" : item.status}</td>
                  <td>{item.chunk_count} chunks</td>
                  <td className="text-stone-400">{ago(item.updated_at)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </section>
        <section className="rounded-2xl border border-[#eceaf3] bg-white p-4">
          <p className="font-semibold">Dead-letter inspector <span className="text-sm font-normal text-rose-500">{data?.dead_letters.length ?? 0}</span></p>
          <div className="mt-3 space-y-3">
            {(data?.dead_letters ?? []).length === 0 && <p className="text-sm text-stone-400">No failed jobs.</p>}
            {(data?.dead_letters ?? []).map((item) => (
              <article key={`${item.source}-${item.id}`} className="rounded-xl bg-rose-50/60 p-3 text-sm">
                <p className="font-medium">{item.filename || item.event_type}</p>
                <p className="mt-1 text-xs text-stone-600">{item.error || item.status}</p>
                <p className="mt-1 text-xs text-stone-400">{item.source} · attempt {item.attempt} · {ago(item.updated_at)}</p>
                <button type="button" onClick={() => void replay(item)} className="mt-2 rounded-lg bg-white px-2 py-1 text-xs">Replay</button>
              </article>
            ))}
          </div>
          <div className="mt-4 rounded-xl bg-[#f6f5fb] p-3 text-sm">
            <p className="text-xs text-stone-400">Worker</p>
            <p className="font-medium">{data?.worker.name}</p>
            <p className="text-xs text-stone-500">{data?.worker.healthy ? "Heartbeat current" : "No recent heartbeat"} · {ago(data?.worker.seen_at)}</p>
            <p className="text-xs text-stone-400">{data?.worker.detail}</p>
            <p className="mt-2 text-xs text-stone-400">Circuit {data?.circuit_open ? "open" : "closed"} · tokens today {data?.tokens.used_today ?? 0}</p>
          </div>
        </section>
      </div>
    </div>
  );
}

function Stat({ label, value, hint, note }: { label: string; value: string; hint?: string; note: string }) {
  return (
    <article className="rounded-2xl border border-[#eceaf3] bg-white p-4">
      <p className="text-xs text-stone-400">{label}</p>
      <p className="mt-2 text-3xl font-semibold">{value} <span className="text-sm font-normal text-stone-400">{hint}</span></p>
      <p className="mt-1 text-xs text-stone-400">{note}</p>
    </article>
  );
}
