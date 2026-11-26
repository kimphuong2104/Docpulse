export type Session = {
  access_token: string;
  refresh_token: string;
  expires_in: number;
  user: { id: string; email: string; display_name: string };
  workspace: { id: string; slug: string; name: string; role: string };
};

const KEY = "docpulse.session";

export function loadSession(): Session | null {
  const raw = localStorage.getItem(KEY);
  if (!raw) return null;
  try {
    return JSON.parse(raw) as Session;
  } catch {
    return null;
  }
}

export function saveSession(session: Session) {
  localStorage.setItem(KEY, JSON.stringify(session));
}

export function clearSession() {
  localStorage.removeItem(KEY);
}

export async function readError(response: Response): Promise<string> {
  try {
    const body = (await response.json()) as { detail?: unknown };
    if (typeof body.detail === "string") return body.detail;
  } catch {
    /* response had no JSON body */
  }
  return "Máy chủ trả về lỗi.";
}

let refreshTask: Promise<Session | null> | null = null;

function refreshSession(session: Session): Promise<Session | null> {
  if (refreshTask) return refreshTask;
  refreshTask = (async () => {
    const current = loadSession() ?? session;
    try {
      const response = await fetch("/api/auth/refresh", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ refresh_token: current.refresh_token }),
      });
      if (!response.ok) {
        clearSession();
        return null;
      }
      const next = (await response.json()) as Session;
      saveSession(next);
      return next;
    } finally {
      refreshTask = null;
    }
  })();
  return refreshTask;
}

export function uploadForm(path: string, form: FormData, onProgress?: (pct: number) => void): Promise<Response> {
  const send = (token?: string) =>
    new Promise<XMLHttpRequest>((resolve, reject) => {
      const xhr = new XMLHttpRequest();
      xhr.open("POST", `/api${path}`);
      if (token) xhr.setRequestHeader("Authorization", `Bearer ${token}`);
      xhr.upload.onprogress = (event) => {
        if (event.lengthComputable && onProgress) onProgress(Math.round((event.loaded / event.total) * 100));
      };
      xhr.onload = () => resolve(xhr);
      xhr.onerror = () => reject(new Error("Không gửi được file."));
      xhr.send(form);
    });
  return (async () => {
    const session = loadSession();
    let xhr = await send(session?.access_token);
    if (xhr.status === 401 && session) {
      const next = await refreshSession(session);
      if (next) xhr = await send(next.access_token);
    }
    return new Response(xhr.responseText, { status: xhr.status });
  })();
}

export async function api(path: string, init: RequestInit = {}, retry = true): Promise<Response> {
  const session = loadSession();
  const headers = new Headers(init.headers);
  if (session) headers.set("Authorization", `Bearer ${session.access_token}`);
  if (init.body && !headers.has("Content-Type")) headers.set("Content-Type", "application/json");
  const response = await fetch(`/api${path}`, { ...init, headers });
  if (response.status === 401 && session && retry && !path.startsWith("/auth/")) {
    const next = await refreshSession(session);
    if (next) return api(path, init, false);
  }
  return response;
}

export function ago(iso: string | null | undefined): string {
  if (!iso) return "not yet";
  const minutes = Math.round((Date.now() - new Date(iso).getTime()) / 60000);
  if (Number.isNaN(minutes)) return "not yet";
  if (minutes < 1) return "just now";
  if (minutes < 60) return `${minutes}m ago`;
  const hours = Math.round(minutes / 60);
  if (hours < 48) return `${hours}h ago`;
  return `${Math.round(hours / 24)}d ago`;
}

export function greeting(name: string): string {
  const hour = new Date().getHours();
  const part = hour < 12 ? "Good morning" : hour < 18 ? "Good afternoon" : "Good evening";
  return `${part}, ${name}`;
}

export function initials(name: string): string {
  const parts = name.split(" ").filter(Boolean);
  if (parts.length === 0) return "?";
  if (parts.length === 1) return parts[0].slice(0, 2).toUpperCase();
  return (parts[0][0] + parts[parts.length - 1][0]).toUpperCase();
}
