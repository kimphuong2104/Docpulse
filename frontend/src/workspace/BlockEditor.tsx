import { GripVertical } from "lucide-react";
import { useEffect, useState } from "react";
import type { BlockType, BoardRow, EditorBlock } from "../types/page";

const COMMANDS: Array<{ type: BlockType; label: string; hint: string }> = [
  { type: "paragraph", label: "Văn bản", hint: "Đoạn chữ thường" },
  { type: "heading1", label: "Tiêu đề 1", hint: "#" },
  { type: "heading2", label: "Tiêu đề 2", hint: "##" },
  { type: "heading3", label: "Tiêu đề 3", hint: "###" },
  { type: "bullet", label: "Danh sách", hint: "-" },
  { type: "numbered", label: "Danh sách số", hint: "1." },
  { type: "todo", label: "Việc cần làm", hint: "[]" },
  { type: "quote", label: "Trích dẫn", hint: ">" },
  { type: "code", label: "Mã", hint: "```" },
  { type: "callout", label: "Callout", hint: "Khối nhấn mạnh" },
  { type: "divider", label: "Đường kẻ", hint: "---" },
  { type: "database", label: "Cơ sở dữ liệu", hint: "Bảng / Kanban" },
];

const STATUSES = ["Cần làm", "Đang làm", "Xong"];

function blank(type: BlockType = "paragraph"): EditorBlock {
  return {
    id: crypto.randomUUID(),
    type,
    text: "",
    checked: false,
    view: "table",
    rows:
      type === "database"
        ? [
            { id: crypto.randomUUID(), title: "Việc mới", status: "Cần làm" },
            { id: crypto.randomUUID(), title: "Việc tiếp theo", status: "Đang làm" },
          ]
        : [],
  };
}

function shortcut(text: string): BlockType | null {
  const rules: Array<[string, BlockType]> = [
    ["### ", "heading3"],
    ["## ", "heading2"],
    ["# ", "heading1"],
    ["- ", "bullet"],
    ["* ", "bullet"],
    ["1. ", "numbered"],
    ["[] ", "todo"],
    ["[ ] ", "todo"],
    ["> ", "quote"],
    ["```", "code"],
  ];
  return rules.find(([prefix]) => text === prefix)?.[1] ?? null;
}

interface BlockEditorProps {
  blocks: EditorBlock[];
  onChange: (blocks: EditorBlock[]) => void;
}

export function BlockEditor({ blocks, onChange }: BlockEditorProps) {
  const [menu, setMenu] = useState<{ id: string; query: string } | null>(null);
  const [focusId, setFocusId] = useState<string | null>(null);

  useEffect(() => {
    if (!focusId) return;
    const node = document.getElementById(`block-${focusId}`) as HTMLTextAreaElement | null;
    node?.focus();
    setFocusId(null);
  }, [focusId, blocks]);

  function update(id: string, patch: Partial<EditorBlock>) {
    onChange(blocks.map((block) => (block.id === id ? { ...block, ...patch } : block)));
  }

  function insertAfter(id: string, type: BlockType = "paragraph") {
    const next = blank(type);
    const index = blocks.findIndex((block) => block.id === id);
    const copy = [...blocks];
    copy.splice(index + 1, 0, next);
    onChange(copy);
    setFocusId(next.id);
    setMenu(null);
  }

  function remove(id: string) {
    if (blocks.length === 1) {
      update(id, { type: "paragraph", text: "", checked: false });
      return;
    }
    const index = blocks.findIndex((block) => block.id === id);
    const copy = blocks.filter((block) => block.id !== id);
    onChange(copy);
    setFocusId(copy[Math.max(0, index - 1)].id);
  }

  function applyType(id: string, type: BlockType) {
    const created = blank(type);
    onChange(blocks.map((block) => (block.id === id ? { ...created, id } : block)));
    setMenu(null);
    setFocusId(id);
  }

  function move(sourceId: string, targetId: string) {
    if (sourceId === targetId) return;
    const source = blocks.find((block) => block.id === sourceId);
    if (!source) return;
    const rest = blocks.filter((block) => block.id !== sourceId);
    const index = rest.findIndex((block) => block.id === targetId);
    rest.splice(index, 0, source);
    onChange(rest);
  }

  const menuItems = COMMANDS.filter((item) => item.label.toLowerCase().includes((menu?.query ?? "").toLowerCase()));

  return (
    <div className="space-y-1">
      {blocks.map((block, index) => {
        let number = 0;
        if (block.type === "numbered") {
          for (let cursor = index; cursor >= 0 && blocks[cursor].type === "numbered"; cursor -= 1) number += 1;
        }
        return (
          <div
            key={block.id}
            className="group relative flex items-start gap-2 rounded-md py-0.5"
            onDragOver={(event) => event.preventDefault()}
            onDrop={(event) => {
              event.preventDefault();
              move(event.dataTransfer.getData("text/plain"), block.id);
            }}
          >
            <button
              type="button"
              draggable
              aria-label="Kéo khối"
              onDragStart={(event) => event.dataTransfer.setData("text/plain", block.id)}
              className="mt-1 text-stone-300 opacity-0 group-hover:opacity-100"
            >
              <GripVertical size={16} />
            </button>
            {block.type === "bullet" && <span className="mt-2 text-stone-400">•</span>}
            {block.type === "numbered" && <span className="mt-1 w-5 text-sm text-stone-400">{number}.</span>}
            {block.type === "todo" && (
              <input
                type="checkbox"
                checked={block.checked}
                onChange={(event) => update(block.id, { checked: event.target.checked })}
                className="mt-2"
              />
            )}
            {block.type === "divider" ? (
              <hr className="my-3 flex-1 border-stone-200" />
            ) : block.type === "database" ? (
              <DatabaseBlock block={block} onChange={(patch) => update(block.id, patch)} />
            ) : (
              <textarea
                id={`block-${block.id}`}
                value={block.text}
                rows={1}
                placeholder={index === 0 && !block.text ? "Gõ / để chèn khối" : ""}
                onChange={(event) => {
                  const value = event.target.value;
                  const nextType = shortcut(value);
                  if (nextType) {
                    update(block.id, { type: nextType, text: "" });
                    return;
                  }
                  update(block.id, { text: value });
                  if (value.startsWith("/")) setMenu({ id: block.id, query: value.slice(1) });
                  else if (menu?.id === block.id) setMenu(null);
                  event.target.style.height = "0px";
                  event.target.style.height = `${event.target.scrollHeight}px`;
                }}
                onKeyDown={(event) => {
                  if (event.key === "Enter" && !event.shiftKey) {
                    event.preventDefault();
                    if (menu?.id === block.id && menuItems[0]) {
                      applyType(block.id, menuItems[0].type);
                      return;
                    }
                    if (["bullet", "numbered", "todo"].includes(block.type) && block.text.trim() === "") {
                      update(block.id, { type: "paragraph" });
                      return;
                    }
                    insertAfter(block.id, ["bullet", "numbered", "todo"].includes(block.type) ? block.type : "paragraph");
                  }
                  if (event.key === "Backspace" && block.text === "") {
                    event.preventDefault();
                    remove(block.id);
                  }
                  if (event.key === "Escape") setMenu(null);
                }}
                className={`min-h-7 flex-1 resize-none bg-transparent outline-none ${
                  block.type === "heading1"
                    ? "text-3xl font-semibold"
                    : block.type === "heading2"
                      ? "text-2xl font-semibold"
                      : block.type === "heading3"
                        ? "text-xl font-semibold"
                        : block.type === "code"
                          ? "rounded bg-stone-100 px-3 py-2 font-mono text-sm"
                          : block.type === "quote"
                            ? "border-l-2 border-stone-300 pl-3 text-stone-600"
                            : block.type === "callout"
                              ? "rounded-lg bg-amber-50 px-3 py-2"
                              : "text-base"
                } ${block.checked ? "text-stone-400 line-through" : "text-stone-800"}`}
              />
            )}
            {menu?.id === block.id && (
              <ul className="absolute top-8 left-8 z-10 w-64 overflow-hidden rounded-lg border border-stone-200 bg-white shadow-lg">
                {menuItems.map((item) => (
                  <li key={item.type}>
                    <button
                      type="button"
                      onMouseDown={(event) => event.preventDefault()}
                      onClick={() => applyType(block.id, item.type)}
                      className="flex w-full items-center justify-between px-3 py-2 text-left text-sm hover:bg-stone-100"
                    >
                      <span>{item.label}</span>
                      <span className="text-xs text-stone-400">{item.hint}</span>
                    </button>
                  </li>
                ))}
                {menuItems.length === 0 && <li className="px-3 py-2 text-sm text-stone-400">Không có lệnh</li>}
              </ul>
            )}
          </div>
        );
      })}
    </div>
  );
}

function DatabaseBlock({
  block,
  onChange,
}: {
  block: EditorBlock;
  onChange: (patch: Partial<EditorBlock>) => void;
}) {
  function setRows(rows: BoardRow[]) {
    onChange({ rows });
  }

  return (
    <div className="flex-1 rounded-lg border border-stone-200">
      <div className="flex items-center justify-between border-b border-stone-200 px-3 py-2 text-sm">
        <span className="font-medium text-stone-700">Bảng việc</span>
        <div className="flex gap-1">
          {(["table", "board"] as const).map((view) => (
            <button
              key={view}
              type="button"
              onClick={() => onChange({ view })}
              className={`rounded px-2 py-1 ${block.view === view ? "bg-stone-900 text-white" : "text-stone-500"}`}
            >
              {view === "table" ? "Bảng" : "Kanban"}
            </button>
          ))}
        </div>
      </div>
      {block.view === "table" ? (
        <table className="w-full text-sm">
          <thead>
            <tr className="text-left text-stone-400">
              <th className="px-3 py-2 font-normal">Tên</th>
              <th className="px-3 py-2 font-normal">Trạng thái</th>
            </tr>
          </thead>
          <tbody>
            {block.rows.map((row) => (
              <tr key={row.id} className="border-t border-stone-100">
                <td className="px-3 py-1">
                  <input
                    value={row.title}
                    onChange={(event) =>
                      setRows(block.rows.map((item) => (item.id === row.id ? { ...item, title: event.target.value } : item)))
                    }
                    className="w-full bg-transparent outline-none"
                  />
                </td>
                <td className="px-3 py-1">
                  <select
                    value={row.status}
                    onChange={(event) =>
                      setRows(block.rows.map((item) => (item.id === row.id ? { ...item, status: event.target.value } : item)))
                    }
                    className="bg-transparent outline-none"
                  >
                    {STATUSES.map((status) => (
                      <option key={status}>{status}</option>
                    ))}
                  </select>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      ) : (
        <div className="grid grid-cols-3 gap-2 p-3">
          {STATUSES.map((status) => (
            <div key={status} className="rounded-lg bg-stone-50 p-2">
              <p className="mb-2 text-xs font-medium text-stone-500">{status}</p>
              {block.rows
                .filter((row) => row.status === status)
                .map((row) => (
                  <button
                    key={row.id}
                    type="button"
                    onClick={() => {
                      const next = STATUSES[(STATUSES.indexOf(row.status) + 1) % STATUSES.length];
                      setRows(block.rows.map((item) => (item.id === row.id ? { ...item, status: next } : item)));
                    }}
                    className="mb-2 w-full rounded-md bg-white px-2 py-2 text-left text-sm shadow-sm"
                  >
                    {row.title}
                  </button>
                ))}
            </div>
          ))}
        </div>
      )}
      <button
        type="button"
        onClick={() => setRows([...block.rows, { id: crypto.randomUUID(), title: "Việc mới", status: "Cần làm" }])}
        className="px-3 py-2 text-sm text-stone-500"
      >
        + Thêm dòng
      </button>
    </div>
  );
}

export function toEditorBlocks(
  blocks: Array<{ id: string; type: BlockType; content: { text?: string; checked?: boolean; view?: "table" | "board"; rows?: BoardRow[] } }>,
): EditorBlock[] {
  return blocks.map((block) => ({
    id: block.id,
    type: block.type,
    text: block.content.text ?? "",
    checked: Boolean(block.content.checked),
    view: block.content.view === "board" ? "board" : "table",
    rows: block.content.rows ?? [],
  }));
}
