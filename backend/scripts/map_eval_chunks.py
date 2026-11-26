"""Gắn relevant_chunk_indices sau khi các PDF benchmark đã được nạp."""

import json
import re
import sys
from pathlib import Path

from app.db import connect

REPO_ROOT = Path(__file__).resolve().parents[2]


def normalize(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", text.lower()).strip()


def map_questions(eval_path: Path, namespace: str = "multihop") -> None:
    items = json.loads(eval_path.read_text(encoding="utf-8"))
    with connect() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT d.filename, c.chunk_index, c.content
                FROM chunks c
                JOIN documents d ON d.id = c.document_id
                WHERE d.namespace = %s
                ORDER BY d.filename, c.chunk_index;
                """,
                (namespace,),
            )
            chunks = [
                {"filename": row[0], "chunk_index": int(row[1]), "norm": normalize(row[2])}
                for row in cur.fetchall()
            ]

    for item in items:
        quotes = [part.strip() for part in item.get("expected_quote", "").split("|") if part.strip()]
        allowed = set(item.get("source_files") or [])
        found = []
        seen = set()
        for quote in quotes:
            needle = normalize(quote)[:80]
            if len(needle) < 12:
                continue
            for chunk in chunks:
                if allowed and chunk["filename"] not in allowed:
                    continue
                key = (chunk["filename"], chunk["chunk_index"])
                if key in seen:
                    continue
                if needle in chunk["norm"]:
                    seen.add(key)
                    found.append({"filename": chunk["filename"], "chunk_index": chunk["chunk_index"]})
                    break
        item["relevant_chunk_indices"] = found

    eval_path.write_text(json.dumps(items, indent=2, ensure_ascii=False), encoding="utf-8")
    missing = [item["id"] for item in items if not item["relevant_chunk_indices"]]
    print(f"Đã map {len(items) - len(missing)}/{len(items)} câu hỏi.")
    if missing:
        print("Chưa tìm thấy chunk:", ", ".join(missing))


if __name__ == "__main__":
    target = Path(sys.argv[1]) if len(sys.argv) > 1 else REPO_ROOT / "test_benchmark_multihop" / "eval_multihop.json"
    map_questions(target)
