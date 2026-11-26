export type BlockType =
  | "paragraph"
  | "heading1"
  | "heading2"
  | "heading3"
  | "bullet"
  | "numbered"
  | "todo"
  | "quote"
  | "code"
  | "callout"
  | "divider"
  | "database";

export interface BoardRow {
  id: string;
  title: string;
  status: string;
}

export interface EditorBlock {
  id: string;
  type: BlockType;
  text: string;
  checked: boolean;
  view: "table" | "board";
  rows: BoardRow[];
}

export interface PageSummary {
  id: string;
  parent_id: string | null;
  title: string;
  icon: string;
  position: number;
  favorite: boolean;
  archived: boolean;
  version: number;
  updated_at: string | null;
}

export interface PageDetail extends PageSummary {
  blocks: Array<{
    id: string;
    type: BlockType;
    content: {
      text?: string;
      checked?: boolean;
      view?: "table" | "board";
      rows?: BoardRow[];
    };
    position: number;
  }>;
}
