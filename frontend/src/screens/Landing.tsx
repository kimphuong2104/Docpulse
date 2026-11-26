import { Link } from "react-router-dom";

export function Landing() {
  return (
    <div className="min-h-screen bg-[#f7f6fc] text-[#1c1b29]">
      <header className="mx-auto flex max-w-6xl items-center justify-between px-6 py-5 text-sm">
        <span className="font-semibold tracking-wide">SYNAPSE</span>
        <nav className="flex gap-5 text-stone-500">
          <span>Overview</span>
          <span>Interactive Sandbox</span>
          <span>Documentation</span>
          <span>Pricing</span>
        </nav>
        <div className="flex items-center gap-3">
          <Link to="/login">Sign in</Link>
          <Link to="/login" className="rounded-full bg-[#1c1b29] px-4 py-2 text-white">Launch App</Link>
        </div>
      </header>
      <main className="mx-auto max-w-5xl px-6 pb-16 text-center">
        <p className="text-xs text-emerald-600">● Zero-friction exploration · Local-first IndexedDB persistence</p>
        <h1 className="mt-3 text-4xl font-semibold tracking-tight">The Next-Gen AI Block Editor with Real-Time CRDT Sync</h1>
        <p className="mx-auto mt-3 max-w-2xl text-stone-500">
          Experience collaborative markdown blocks, contextual AI diff proposals, and hybrid semantic search. Try it here in the sandbox without an account.
        </p>
        <div className="mt-8 overflow-hidden rounded-3xl border border-[#eceaf3] bg-white text-left shadow-xl">
          <div className="flex items-center gap-2 border-b border-[#eceaf3] px-4 py-2 text-xs text-stone-400">
            <span className="h-2 w-2 rounded-full bg-rose-400" />
            <span className="h-2 w-2 rounded-full bg-amber-400" />
            <span className="h-2 w-2 rounded-full bg-emerald-400" />
            <span className="ml-2">Live Sandbox · engineering / architecture</span>
          </div>
          <div className="grid grid-cols-[1.4fr_0.8fr]">
            <div className="p-6">
              <h2 className="text-2xl font-semibold">Decentralized State Machine Architecture</h2>
              <p className="mt-2 text-sm leading-6 text-stone-600">
                The synchronization layer leverages a dual-state YATA conflict-free sequence. Local state modifications are immediately written to IndexedDB before any mesh broadcast.
              </p>
              <pre className="mt-3 overflow-x-auto rounded-xl bg-[#1c1b29] p-3 text-xs text-emerald-200">{`export function applyRemoteDelta(clock, delta) {
  const next = clock.tick(delta.clientId);
  return clock.advance(next);
}`}</pre>
              <div className="mt-3 rounded-xl bg-emerald-50 p-3 text-sm text-emerald-800">
                Local mutations persist atomically in IndexedDB using Web Workers, deferring WebSocket consensus without rendering blocks.
                <div className="mt-2">
                  <Link to="/login" className="rounded-lg bg-[#5b4dff] px-3 py-1 text-xs text-white">Accept Diff</Link>
                </div>
              </div>
            </div>
            <div className="border-l border-[#eceaf3] bg-[#f7f6fc] p-4 text-sm">
              <p className="text-xs font-semibold text-stone-400">LIVE INSPECTOR</p>
              <p className="mt-2">Vector clock: synced</p>
              <p>Lamport ticks: 142</p>
              <svg viewBox="0 0 200 60" className="mt-3 h-16 w-full">
                <path d="M0 40 C 30 38, 40 15, 80 22 S 140 50, 200 18" fill="none" stroke="#5b4dff" strokeWidth="2" />
              </svg>
            </div>
          </div>
        </div>
        <div className="mt-6 grid grid-cols-4 gap-3 text-left">
          {[
            ["0 ms", "Local Typing Latency"],
            ["Yjs Native", "Conflict-free CRDT"],
            ["IndexedDB", "Local Offline Storage"],
            ["< 50ms", "Hybrid Vector Search"],
          ].map(([title, note]) => (
            <div key={title} className="rounded-2xl bg-white p-4 shadow-sm">
              <p className="text-xl font-semibold">{title}</p>
              <p className="text-xs text-stone-400">{note}</p>
            </div>
          ))}
        </div>
        <h2 className="mt-14 text-2xl font-semibold">Architected for Speed, Privacy, and Synthetic Intelligence</h2>
        <div className="mt-6 grid grid-cols-3 gap-4 text-left">
          {[
            ["Local-First via CRDT Sync", "Yjs-compatible conflict-free replicated data types. Offline edits reconcile through IndexedDB with zero lost keystrokes."],
            ["Contextual AI Copilot", "Inline diff previews let you preview suggestions before accepting. Stream incremental revisions, block by block."],
            ["Hybrid Vector & Keyword Search", "Dense embeddings and lexical rank fused with Reciprocal Rank Fusion. Queries run against the pages you can open."],
          ].map(([title, body]) => (
            <div key={title} className="rounded-2xl bg-white p-5 shadow-sm">
              <p className="font-semibold">{title}</p>
              <p className="mt-2 text-sm leading-6 text-stone-500">{body}</p>
            </div>
          ))}
        </div>
        <div className="mt-10 flex items-end justify-between rounded-3xl bg-white p-6 text-left shadow-sm">
          <div>
            <h2 className="text-2xl font-semibold">Keep hands on the keys. Everything is a trigger.</h2>
            <ul className="mt-3 space-y-1 text-sm text-stone-500">
              <li>⌘K Universal workspace switcher</li>
              <li>/ Slash menu for blocks, databases, and AI</li>
              <li>Space accepts the copilot diff for the active paragraph</li>
            </ul>
          </div>
          <Link to="/login" className="rounded-full bg-[#5b4dff] px-4 py-2 text-sm text-white">Launch Workspace</Link>
        </div>
      </main>
      <footer className="border-t border-[#eceaf3] px-6 py-4 text-center text-xs text-stone-400">Synapse v2.4 · Mesh Active</footer>
    </div>
  );
}
