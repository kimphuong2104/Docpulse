import { useEffect, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { Search } from "lucide-react";
import { ago, api, greeting, readError } from "../api/client";
import { SearchModal } from "../app/SearchModal";

type HomeData = {
  display_name: string;
  page_count: number;
  indexed_count: number;
  recent: { id: string; title: string; icon: string; updated_at: string | null; index_status: string; chunk_count: number; vector_ready: boolean }[];
  tasks: { id: string; title: string; status: string; priority: string; area: string }[];
  activity: { action: string; detail: string; created_at: string | null; actor: string; target: string }[];
};

export function Home() {
  const { workspaceId = "" } = useParams();
  const [data, setData] = useState<HomeData | null>(null);
  const [error, setError] = useState("");
  const [searchOpen, setSearchOpen] = useState(false);

  async function load() {
    const response = await api(`/workspaces/${workspaceId}/home`);
    if (!response.ok) {
      setError(await readError(response));
      return;
    }
    setData((await response.json()) as HomeData);
  }

  useEffect(() => {
    void load();
  }, [workspaceId]);

  async function cycle(task: HomeData["tasks"][number]) {
    const order = ["Todo", "In Progress", "In Review", "Completed"];
    const status = order[(order.indexOf(task.status) + 1) % order.length];
    const response = await api(`/workspaces/${workspaceId}/tasks/${task.id}`, {
      method: "PATCH",
      body: JSON.stringify({ status }),
    });
    if (!response.ok) {
      setError(await readError(response));
      return;
    }
    await load();
  }

  const freshness = data && data.page_count ? Math.round((data.indexed_count / data.page_count) * 1000) / 10 : 0;

  return (
    <div className="h-full overflow-y-auto px-10 py-8">
      <div className="mb-1 flex items-center justify-between text-xs text-stone-400">
        <span>● Workspace {workspaceId}</span>
        <span className="text-emerald-600">{data ? `${freshness}% indexed` : "Loading index state"}</span>
      </div>
      <h1 className="text-4xl font-semibold tracking-tight">{data ? greeting(data.display_name) : "Loading workspace"}</h1>
      <p className="mt-1 text-stone-500">Pages, assigned tasks, and workspace activity stored for this account.</p>
      {error && <p className="mt-3 text-sm text-rose-600">{error}</p>}
      <button type="button" onClick={() => setSearchOpen(true)} className="mt-6 flex w-full items-center gap-3 rounded-2xl border border-[#eceaf3] bg-white px-4 py-4 text-left shadow-sm">
        <Search size={16} className="text-[#5b4dff]" />
        <span className="text-stone-400">Ask across {data?.page_count ?? 0} workspace pages…</span>
        <span className="ml-auto rounded-lg bg-[#5b4dff] px-3 py-1.5 text-sm text-white">Ask AI</span>
      </button>
      <div className="mt-6 flex items-center justify-between text-xs text-stone-400">
        <span>RECENTLY VISITED</span>
        <span>{data?.recent.length ?? 0} pages</span>
      </div>
      <div className="mt-2 grid grid-cols-4 gap-3">
        {(data?.recent ?? []).map((page) => (
          <Link key={page.id} to={`/w/${workspaceId}/p/${page.id}`} className="rounded-2xl border border-[#eceaf3] bg-white p-4 shadow-sm">
            <div className="flex items-center justify-between text-xs">
              <span>{page.icon}</span>
              <span className="text-emerald-600">{page.vector_ready ? "Vectors" : page.index_status}</span>
            </div>
            <p className="mt-3 font-medium">{page.title}</p>
            <p className="text-xs text-stone-400">{ago(page.updated_at)}</p>
            <p className="text-xs text-stone-400">{page.chunk_count} chunks</p>
          </Link>
        ))}
      </div>
      <div className="mt-6 grid grid-cols-2 gap-4">
        <section className="rounded-2xl border border-[#eceaf3] bg-white p-4">
          <p className="font-medium">My Assigned Tasks {data?.tasks.length ?? 0}</p>
          <ul className="mt-3 space-y-3 text-sm">
            {(data?.tasks ?? []).map((task) => (
              <li key={task.id}>
                <button type="button" onClick={() => void cycle(task)} className="text-left">
                  <span className="block">{task.title}</span>
                  <span className="text-xs text-[#5b4dff]">{task.status}</span>
                  <span className="text-xs text-stone-400"> · {task.area}</span>
                </button>
              </li>
            ))}
          </ul>
        </section>
        <section className="rounded-2xl border border-[#eceaf3] bg-white p-4">
          <p className="font-medium">Recent Workspace Activity</p>
          <ul className="mt-3 space-y-3 text-sm">
            {(data?.activity ?? []).map((item, index) => (
              <li key={`${item.created_at}-${index}`}>
                <span className="font-medium">{item.actor}</span> {item.action} {item.target}
                <span className="mt-0.5 block text-xs text-stone-400">{ago(item.created_at)} · {item.detail}</span>
              </li>
            ))}
          </ul>
        </section>
      </div>
      <SearchModal open={searchOpen} onClose={() => setSearchOpen(false)} />
    </div>
  );
}
