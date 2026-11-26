import type { EditorBlock, PageDetail, PageSummary } from "../types/page";

async function readError(response: Response): Promise<string> {
  try {
    const body = (await response.json()) as { detail?: unknown };
    if (typeof body.detail === "string") return body.detail;
    if (body.detail && typeof body.detail === "object") return "Trang đã được lưu ở một phiên khác. Hãy tải lại.";
  } catch {
    /* not json */
  }
  return "Máy chủ trả về lỗi.";
}

export async function listPages(archived = false): Promise<PageSummary[]> {
  const response = await fetch(`/api/pages?archived=${archived ? "true" : "false"}`);
  if (!response.ok) throw new Error(await readError(response));
  const body = (await response.json()) as { pages: PageSummary[] };
  return body.pages;
}

export async function getPage(id: string): Promise<PageDetail> {
  const response = await fetch(`/api/pages/${id}`);
  if (!response.ok) throw new Error(await readError(response));
  return (await response.json()) as PageDetail;
}

export async function createPage(parentId?: string | null): Promise<PageDetail> {
  const response = await fetch("/api/pages", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ parent_id: parentId ?? null, title: "" }),
  });
  if (!response.ok) throw new Error(await readError(response));
  return (await response.json()) as PageDetail;
}

export async function patchPage(
  id: string,
  fields: Partial<Pick<PageSummary, "title" | "icon" | "favorite" | "archived" | "parent_id" | "position">>,
): Promise<PageSummary> {
  const response = await fetch(`/api/pages/${id}`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(fields),
  });
  if (!response.ok) throw new Error(await readError(response));
  return (await response.json()) as PageSummary;
}

export async function savePageBlocks(id: string, version: number, blocks: EditorBlock[]): Promise<PageDetail> {
  const response = await fetch(`/api/pages/${id}/blocks`, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      version,
      blocks: blocks.map((block, index) => ({
        id: block.id,
        type: block.type,
        position: index,
        content: {
          text: block.text,
          checked: block.checked,
          view: block.view,
          rows: block.rows,
        },
      })),
    }),
  });
  if (!response.ok) throw new Error(await readError(response));
  return (await response.json()) as PageDetail;
}

export async function deletePage(id: string): Promise<void> {
  const response = await fetch(`/api/pages/${id}`, { method: "DELETE" });
  if (!response.ok) throw new Error(await readError(response));
}

export async function askPage(id: string, mode: "ask" | "summarize" | "continue" | "translate", prompt: string): Promise<string> {
  const response = await fetch(`/api/pages/${id}/ai`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ mode, prompt }),
  });
  if (!response.ok) throw new Error(await readError(response));
  const body = (await response.json()) as { answer: string };
  return body.answer;
}
