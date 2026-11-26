import type { ReactNode } from "react";
import { Activity, ChevronDown, FileText, Home, Plus, Search, Settings, Share2, Upload } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import { NavLink, Outlet, useLocation, useNavigate, useParams } from "react-router-dom";
import { api, initials, loadSession, readError, uploadForm } from "../api/client";
import { SearchModal } from "./SearchModal";

type PageLink = { id: string; title: string; icon: string; favorite: boolean };
type Member = { display_name: string; initials: string };

export function WorkspaceShell() {
  const { workspaceId = "", pageId } = useParams();
  const location = useLocation();
  const navigate = useNavigate();
  const session = loadSession();
  const [searchOpen, setSearchOpen] = useState(location.pathname.endsWith("/search"));
  const [open, setOpen] = useState(true);
  const [pages, setPages] = useState<PageLink[]>([]);
  const [members, setMembers] = useState<Member[]>([]);
  const [indexed, setIndexed] = useState(0);
  const [total, setTotal] = useState(0);
  const [error, setError] = useState("");
  const [imports, setImports] = useState<{ name: string; pct: number; detail: string }[]>([]);
  const importRef = useRef<HTMLInputElement>(null);
  const current = pages.find((page) => page.id === pageId);

  useEffect(() => {
    setSearchOpen(location.pathname.endsWith("/search"));
  }, [location.pathname]);

  useEffect(() => {
    function onKey(event: KeyboardEvent) {
      if ((event.metaKey || event.ctrlKey) && event.key.toLowerCase() === "k") {
        event.preventDefault();
        setSearchOpen(true);
      }
      if (event.key === "Escape") setSearchOpen(false);
    }
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);

  useEffect(() => {
    void (async () => {
      const [pageResponse, overviewResponse] = await Promise.all([
        api(`/workspaces/${workspaceId}/pages`),
        api(`/workspaces/${workspaceId}`),
      ]);
      if (!pageResponse.ok) {
        setError(await readError(pageResponse));
        return;
      }
      const body = (await pageResponse.json()) as { pages: PageLink[] };
      setPages(body.pages);
      if (overviewResponse.ok) {
        const overview = (await overviewResponse.json()) as { members: Member[]; indexed: number; pages: number };
        setMembers(overview.members);
        setIndexed(overview.indexed);
        setTotal(overview.pages);
      }
    })();
  }, [workspaceId, location.pathname]);

  async function importFiles(list: FileList | null) {
    if (!list?.length) return;
    setError("");
    const queued = Array.from(list);
    let lastPage = "";
    for (const file of queued) {
      setImports((current) => [...current.filter((item) => item.name !== file.name), { name: file.name, pct: 0, detail: "Uploading" }]);
      const form = new FormData();
      form.append("file", file);
      form.append("mode", "import");
      try {
        const response = await uploadForm(`/workspaces/${workspaceId}/files`, form, (pct) => {
          setImports((current) => current.map((item) => (item.name === file.name ? { ...item, pct } : item)));
        });
        if (!response.ok) {
          setError(await readError(response));
          setImports((current) => current.map((item) => (item.name === file.name ? { ...item, detail: "Failed" } : item)));
          continue;
        }
        const body = (await response.json()) as { page: { id: string }; file: { status: string } };
        lastPage = body.page.id;
        setImports((current) => current.map((item) => (item.name === file.name ? { ...item, pct: 100, detail: body.file.status } : item)));
      } catch (reason) {
        setError(reason instanceof Error ? reason.message : "Không gửi được file.");
      }
    }
    if (importRef.current) importRef.current.value = "";
    if (lastPage) navigate(`/w/${workspaceId}/p/${lastPage}`);
  }

  async function createPage() {
    const response = await api(`/workspaces/${workspaceId}/pages`, {
      method: "POST",
      body: JSON.stringify({ title: "Untitled" }),
    });
    if (!response.ok) {
      setError(await readError(response));
      return;
    }
    const page = (await response.json()) as { id: string };
    navigate(`/w/${workspaceId}/p/${page.id}`);
  }

  return (
    <div className="flex h-screen bg-[#f4f3fb] text-[#1c1b29]">
      <aside className="flex w-[248px] shrink-0 flex-col border-r border-[#eceaf3] bg-[#f7f6fc]">
        <div className="flex items-center gap-2 px-3 py-3">
          <span className="flex h-7 w-7 items-center justify-center rounded-lg bg-[#5b4dff] text-xs font-bold text-white">T</span>
          <span className="truncate text-sm font-semibold">{session?.workspace.name || "Workspace"}</span>
        </div>
        <nav className="space-y-0.5 px-2 text-sm">
          <SideLink to={`/w/${workspaceId}`} end icon={<Home size={15} />} label="Home" />
          <button type="button" onClick={() => setSearchOpen(true)} className="flex w-full items-center gap-2 rounded-lg px-2 py-1.5 text-left text-stone-600 hover:bg-white">
            <Search size={15} /> AI Search
          </button>
          <SideLink to={`/w/${workspaceId}/system`} icon={<Activity size={15} />} label="System Monitor" />
        </nav>
        <p className="mt-4 px-4 text-[11px] font-semibold tracking-wide text-stone-400">FAVORITES</p>
        <div className="px-2">
          {pages.filter((page) => page.favorite).map((page) => (
            <SideLink key={page.id} to={`/w/${workspaceId}/p/${page.id}`} icon={<FileText size={14} />} label={page.title} />
          ))}
        </div>
        <button type="button" onClick={() => setOpen((value) => !value)} className="mt-3 flex items-center gap-1 px-4 text-[11px] font-semibold tracking-wide text-stone-400">
          <ChevronDown size={12} className={open ? "" : "-rotate-90"} /> WORKSPACE PAGES
        </button>
        {open && (
          <div className="px-2">
            {pages.filter((page) => !page.favorite).map((page) => (
              <SideLink key={page.id} to={`/w/${workspaceId}/p/${page.id}`} icon={<span className="w-4 text-center">{page.icon}</span>} label={page.title} />
            ))}
          </div>
        )}
        {error && <p className="px-4 py-2 text-xs text-rose-600">{error}</p>}
        <div className="mt-auto space-y-2 p-3">
          {imports.map((item) => (
            <div key={item.name} className="rounded-xl bg-white px-2 py-1.5 text-xs shadow-sm">
              <p className="truncate">{item.name}</p>
              <div className="mt-1 h-1 rounded-full bg-[#f4f3fb]"><div className="h-1 rounded-full bg-[#5b4dff]" style={{ width: `${item.pct}%` }} /></div>
              <p className="mt-1 text-stone-400">{item.pct < 100 ? `${item.pct}%` : item.detail}</p>
            </div>
          ))}
          <input ref={importRef} type="file" accept=".pdf,.md,.markdown,application/pdf,text/markdown" multiple hidden onChange={(event) => void importFiles(event.target.files)} />
          <div className="grid grid-cols-2 gap-2">
            <button type="button" onClick={() => void createPage()} className="flex items-center justify-center gap-1 rounded-xl bg-white py-2 text-sm shadow-sm">
              <Plus size={14} /> New Page
            </button>
            <button type="button" onClick={() => importRef.current?.click()} className="flex items-center justify-center gap-1 rounded-xl bg-white py-2 text-sm shadow-sm">
              <Upload size={14} /> Import
            </button>
          </div>
        </div>
      </aside>
      <div className="flex min-w-0 flex-1 flex-col">
        <header className="flex h-12 items-center gap-3 border-b border-[#eceaf3] bg-[#f7f6fc] px-4 text-sm">
          <span className="truncate text-stone-500">{current ? current.title : session?.workspace.name}</span>
          <button type="button" onClick={() => setSearchOpen(true)} className="ml-auto flex items-center gap-2 rounded-lg bg-white px-3 py-1 text-stone-400 shadow-sm">
            <Search size={14} /> Search or jump to... <span className="text-xs">⌘K</span>
          </button>
          <span className="rounded-full bg-violet-50 px-2 py-0.5 text-xs font-medium text-violet-600">{indexed}/{total} vectors</span>
          <span className="flex -space-x-1 text-[10px] font-semibold text-white">
            {members.slice(0, 3).map((member) => (
              <i key={member.display_name} className="flex h-6 w-6 items-center justify-center rounded-full bg-violet-500 not-italic">{member.initials}</i>
            ))}
          </span>
          <button type="button" onClick={() => navigate(`/w/${workspaceId}/settings`)} className="inline-flex items-center gap-1 rounded-lg px-2 py-1 text-stone-500">
            <Share2 size={14} /> Share
          </button>
          <button type="button" onClick={() => navigate(`/w/${workspaceId}/settings`)} className="flex h-7 w-7 items-center justify-center rounded-full bg-stone-800 text-xs text-white" title="Settings">
            {session ? initials(session.user.display_name) : <Settings size={14} />}
          </button>
        </header>
        <div className="min-h-0 flex-1 overflow-hidden">
          <Outlet />
        </div>
      </div>
      <SearchModal open={searchOpen} onClose={() => setSearchOpen(false)} />
    </div>
  );
}

function SideLink({ to, icon, label, end = false }: { to: string; icon: ReactNode; label: string; end?: boolean }) {
  return (
    <NavLink
      to={to}
      end={end}
      className={({ isActive }) =>
        `flex items-center gap-2 rounded-lg px-2 py-1.5 ${isActive ? "bg-white font-medium shadow-sm" : "text-stone-600 hover:bg-white"}`
      }
    >
      {icon} <span className="truncate">{label}</span>
    </NavLink>
  );
}
