export const WORKSPACE_ID = "tech";

export const pages = [
  { id: "distributed-queue", title: "Distributed Queue", section: "Architecture", favorite: false },
  { id: "database-schema", title: "Database Schema", section: "Architecture", favorite: false },
  { id: "caching-strategy", title: "Caching Strategy", section: "Architecture", favorite: false },
  { id: "api-specifications", title: "API Specifications", section: "Favorites", favorite: true },
  { id: "q4-roadmap", title: "Q4 Roadmap", section: "Favorites", favorite: true },
] as const;

export type PageId = (typeof pages)[number]["id"];

export function pagePath(pageId: string) {
  return `/w/${WORKSPACE_ID}/p/${pageId}`;
}
