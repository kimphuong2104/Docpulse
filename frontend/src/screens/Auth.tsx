import { FormEvent, useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { readError, saveSession } from "../api/client";

export function Auth({ mode }: { mode: "login" | "register" }) {
  const navigate = useNavigate();
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [org, setOrg] = useState("");
  const [show, setShow] = useState(false);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [latency, setLatency] = useState<number | null>(null);

  async function submit(event: FormEvent) {
    event.preventDefault();
    setBusy(true);
    setError("");
    const started = performance.now();
    const response = await fetch(mode === "login" ? "/api/auth/login" : "/api/auth/register", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ email, password, workspace_name: org || "Workspace" }),
    });
    setLatency(Math.round(performance.now() - started));
    if (!response.ok) {
      setError(await readError(response));
      setBusy(false);
      return;
    }
    const session = await response.json();
    saveSession(session);
    navigate(`/w/${session.workspace.slug}`);
  }

  return (
    <div className="flex min-h-screen items-center justify-center bg-[#eef0fb] px-4">
      <form className="w-full max-w-md rounded-3xl bg-white px-8 py-8 shadow-xl" onSubmit={submit}>
        <div className="mx-auto mb-3 flex h-12 w-12 items-center justify-center rounded-2xl bg-[#4f46e5] text-white">▶</div>
        <h1 className="text-center text-2xl font-semibold">
          Tech Workspace <span className="align-middle text-xs text-stone-400">v2.4</span>
        </h1>
        <p className="mt-1 text-center text-sm text-stone-500">Sign in or create your collaborative workspace with real-time state synchronization.</p>
        <div className="mt-5 grid grid-cols-2 rounded-xl bg-[#f4f3fb] p-1 text-sm">
          <Link to="/login" className={`rounded-lg py-2 text-center ${mode === "login" ? "bg-white shadow-sm" : "text-stone-500"}`}>Sign In</Link>
          <Link to="/register" className={`rounded-lg py-2 text-center ${mode === "register" ? "bg-white shadow-sm" : "text-stone-500"}`}>Create Account</Link>
        </div>
        {mode === "register" && (
          <label className="mt-4 block text-xs text-stone-500">
            Organization / Workspace Name
            <input value={org} onChange={(event) => setOrg(event.target.value)} required className="mt-1 w-full rounded-xl bg-[#f6f5fb] px-3 py-2 text-sm outline-none" />
          </label>
        )}
        <p className="mt-2 text-xs text-[#5b4dff]">A new account creates a workspace, starter pages, and queues those pages for indexing.</p>
        <label className="mt-3 block text-xs text-stone-500">
          Work Email
          <input type="email" required value={email} onChange={(event) => setEmail(event.target.value)} className="mt-1 w-full rounded-xl bg-[#f6f5fb] px-3 py-2 text-sm outline-none" />
        </label>
        <label className="mt-3 block text-xs text-stone-500">
          Password
          <span className="relative mt-1 block">
            <input type={show ? "text" : "password"} required minLength={8} value={password} onChange={(event) => setPassword(event.target.value)} className="w-full rounded-xl bg-[#f6f5fb] px-3 py-2 text-sm outline-none" />
            <button type="button" onClick={() => setShow((value) => !value)} className="absolute top-2 right-3 text-xs text-stone-400">show</button>
          </span>
        </label>
        {error && <p className="mt-3 text-sm text-rose-600">{error}</p>}
        <p className="mt-3 text-xs text-stone-500">{busy ? "Creating the account, workspace, and index jobs…" : "Access JWT plus refresh-token rotation. A reused refresh token revokes the session."}</p>
        <button type="submit" disabled={busy} className="mt-4 w-full rounded-xl bg-[#3d4bff] py-3 text-sm font-medium text-white disabled:opacity-60">
          {busy ? "Working…" : "Continue to Workspace →"}
        </button>
        <p className="my-3 text-center text-[11px] tracking-wide text-stone-400">OR FEDERATED IDENTITY</p>
        <div className="grid grid-cols-2 gap-2">
          <button type="button" onClick={() => setError("GitHub SSO chưa được nối với nhà cung cấp danh tính.")} className="rounded-xl border border-[#eceaf3] py-2 text-sm">Continue with GitHub</button>
          <button type="button" onClick={() => setError("Google SSO chưa được nối với nhà cung cấp danh tính.")} className="rounded-xl border border-[#eceaf3] py-2 text-sm">Google SSO</button>
        </div>
      </form>
      <span className="fixed right-4 bottom-4 rounded-full bg-white px-3 py-1 text-xs shadow">
        ● Auth gateway {latency === null ? "idle" : `${latency} ms`}
      </span>
    </div>
  );
}
