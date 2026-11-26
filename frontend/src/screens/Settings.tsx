import { FormEvent, useEffect, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { ago, api, loadSession, readError } from "../api/client";

type Member = { id: string; display_name: string; email: string; role: string; joined_at: string | null };
type KeyRow = { id: string; name: string; prefix: string; created_at: string | null; revoked_at: string | null; created_by: string | null };
type Quota = { tokens: { used: number; used_today: number; quota: number }; cache: { ratio: number | null }; latency: { p50_ms: number; p95_ms: number } };

export function Settings() {
  const { workspaceId = "" } = useParams();
  const session = loadSession();
  const [members, setMembers] = useState<Member[]>([]);
  const [role, setRole] = useState(session?.workspace.role ?? "viewer");
  const [keys, setKeys] = useState<KeyRow[]>([]);
  const [quota, setQuota] = useState<Quota | null>(null);
  const [email, setEmail] = useState("");
  const [inviteRole, setInviteRole] = useState("editor");
  const [secret, setSecret] = useState("");
  const [error, setError] = useState("");
  const [pages, setPages] = useState<{ id: string }[]>([]);

  async function load() {
    const [memberResponse, keyResponse, quotaResponse, pageResponse] = await Promise.all([
      api(`/workspaces/${workspaceId}/members`),
      api(`/workspaces/${workspaceId}/keys`),
      api(`/workspaces/${workspaceId}/quota`),
      api(`/workspaces/${workspaceId}/pages`),
    ]);
    if (memberResponse.ok) {
      const body = (await memberResponse.json()) as { members: Member[]; role: string };
      setMembers(body.members);
      setRole(body.role);
    } else setError(await readError(memberResponse));
    if (keyResponse.ok) setKeys(((await keyResponse.json()) as { keys: KeyRow[] }).keys);
    if (quotaResponse.ok) setQuota((await quotaResponse.json()) as Quota);
    if (pageResponse.ok) setPages(((await pageResponse.json()) as { pages: { id: string }[] }).pages);
  }

  useEffect(() => {
    void load();
  }, [workspaceId]);

  async function invite(event: FormEvent) {
    event.preventDefault();
    const response = await api(`/workspaces/${workspaceId}/members`, {
      method: "POST",
      body: JSON.stringify({ email, role: inviteRole }),
    });
    if (!response.ok) {
      setError(await readError(response));
      return;
    }
    setEmail("");
    await load();
  }

  async function createKey() {
    const response = await api(`/workspaces/${workspaceId}/keys`, {
      method: "POST",
      body: JSON.stringify({ name: "Production pipeline" }),
    });
    if (!response.ok) {
      setError(await readError(response));
      return;
    }
    const body = (await response.json()) as { secret: string };
    setSecret(body.secret);
    await load();
  }

  async function revoke(id: string) {
    const response = await api(`/workspaces/${workspaceId}/keys/${id}/revoke`, { method: "POST" });
    if (!response.ok) {
      setError(await readError(response));
      return;
    }
    await load();
  }

  const used = quota?.tokens.used ?? 0;
  const limit = quota?.tokens.quota ?? 1;
  const percent = Math.min(100, Math.round((used / limit) * 1000) / 10);
  const firstPage = pages[0]?.id;

  return (
    <div className="flex h-screen bg-[#f4f3fb]">
      <aside className="flex w-[240px] shrink-0 flex-col border-r border-[#eceaf3] bg-[#f7f6fc] p-4">
        <div className="mb-6 flex items-center gap-2">
          <span className="flex h-8 w-8 items-center justify-center rounded-lg bg-[#5b4dff] font-bold text-white">S</span>
          <div>
            <p className="text-sm font-semibold">SYNAPSE</p>
            <p className="text-[11px] text-stone-400">{workspaceId}</p>
          </div>
        </div>
        <p className="text-[11px] font-semibold tracking-wide text-stone-400">CORE EXECUTION</p>
        <Link to={`/w/${workspaceId}`} className="mt-2 rounded-lg px-2 py-1.5 text-sm text-stone-600">Main Engine</Link>
        {firstPage && <Link to={`/w/${workspaceId}/p/${firstPage}`} className="rounded-lg px-2 py-1.5 text-sm text-stone-600">Canvas</Link>}
        <Link to={`/w/${workspaceId}/system`} className="rounded-lg px-2 py-1.5 text-sm text-stone-600">Telemetry</Link>
        <p className="mt-4 text-[11px] font-semibold tracking-wide text-stone-400">ORGANIZATION</p>
        <span className="mt-2 rounded-lg bg-[#5b4dff] px-2 py-1.5 text-sm text-white">Workspace Settings</span>
      </aside>
      <main className="min-w-0 flex-1 overflow-y-auto px-10 py-6">
        <p className="text-xs text-stone-400">{session?.workspace.name} › Settings & Governance</p>
        <h1 className="mt-1 text-3xl font-semibold">Workspace Settings & Governance</h1>
        <p className="text-sm text-stone-500">Members, API keys, and token usage recorded for this workspace. Your role is {role}.</p>
        {error && <p className="mt-3 text-sm text-rose-600">{error}</p>}
        <section className="mt-6 rounded-2xl border border-[#eceaf3] bg-white p-4">
          <div className="mb-3 flex items-center justify-between">
            <p className="font-semibold">Team Directory & RBAC</p>
            <span className="text-xs text-stone-400">{members.length} members</span>
          </div>
          <table className="w-full text-sm">
            <tbody>
              {members.map((member) => (
                <tr key={member.id} className="border-t border-[#f4f3fb]">
                  <td className="py-2 font-medium">{member.display_name}{member.id === session?.user.id ? " · You" : ""}</td>
                  <td className="text-stone-500">{member.email}</td>
                  <td>{member.role}</td>
                  <td className="text-stone-400">{ago(member.joined_at)}</td>
                </tr>
              ))}
            </tbody>
          </table>
          {role === "owner" && (
            <form onSubmit={invite} className="mt-4 flex gap-2">
              <input value={email} onChange={(event) => setEmail(event.target.value)} type="email" required placeholder="member@company.com" className="flex-1 rounded-xl bg-[#f6f5fb] px-3 py-2 text-sm outline-none" />
              <select value={inviteRole} onChange={(event) => setInviteRole(event.target.value)} className="rounded-xl bg-[#f6f5fb] px-2 text-sm">
                <option value="editor">editor</option>
                <option value="viewer">viewer</option>
              </select>
              <button type="submit" className="rounded-xl bg-[#5b4dff] px-3 text-sm text-white">Invite</button>
            </form>
          )}
        </section>
        <section className="mt-4 rounded-2xl border border-[#eceaf3] bg-white p-4">
          <div className="flex items-center justify-between">
            <p className="font-semibold">Developer API Keys</p>
            {role === "owner" && <button type="button" onClick={() => void createKey()} className="rounded-lg bg-[#f4f3fb] px-3 py-1 text-sm">Generate</button>}
          </div>
          {secret && <p className="mt-3 rounded-xl bg-amber-50 px-3 py-2 text-sm">Copy this secret now. It is stored only as a hash. {secret}</p>}
          <ul className="mt-3 space-y-2 text-sm">
            {keys.map((key) => (
              <li key={key.id} className="flex items-center justify-between rounded-xl bg-[#f6f5fb] px-3 py-2">
                <span>{key.name} · {key.prefix}… · {key.created_by} · {ago(key.created_at)}{key.revoked_at ? " · revoked" : ""}</span>
                {!key.revoked_at && role === "owner" && <button type="button" onClick={() => void revoke(key.id)} className="text-rose-600">Revoke</button>}
              </li>
            ))}
            {keys.length === 0 && <li className="text-stone-400">No keys yet.</li>}
          </ul>
        </section>
        <section className="mt-4 rounded-2xl border border-[#eceaf3] bg-white p-4">
          <p className="font-semibold">AI Token Quota</p>
          <p className="mt-3 text-3xl font-semibold">{used.toLocaleString()} <span className="text-base font-normal text-stone-400">/ {limit.toLocaleString()}</span></p>
          <div className="mt-2 h-2 rounded-full bg-[#f4f3fb]"><div className="h-2 rounded-full bg-[#5b4dff]" style={{ width: `${percent}%` }} /></div>
          <p className="mt-2 text-xs text-stone-400">{percent}% of quota · today {quota?.tokens.used_today ?? 0} tokens · p50 {quota ? Math.round(quota.latency.p50_ms) : "—"} ms · p95 {quota ? Math.round(quota.latency.p95_ms) : "—"} ms</p>
          {percent >= 75 && <p className="mt-3 rounded-xl bg-[#f6f5fb] px-3 py-2 text-sm">Usage has passed 75% of the workspace quota.</p>}
        </section>
      </main>
    </div>
  );
}
