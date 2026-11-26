"""Nạp toàn bộ corpus MultiHop-RAG và xuất đủ 2556 câu hỏi đã map chunk."""

import json
import re
from pathlib import Path

from datasets import load_dataset

from app.db import connect
from app.ingest import ingest_plain_text

REPO_ROOT = Path(__file__).resolve().parents[2]
NAMESPACE = "multihop"
OUT_PATH = REPO_ROOT / "test_benchmark_multihop" / "eval_multihop.json"


def normalize(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", text.lower()).strip()


def filename_for(index: int) -> str:
    return f"article_{index:03d}.md"


def clear_namespace() -> None:
    with connect() as conn:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM documents WHERE namespace = %s", (NAMESPACE,))
            removed = cur.rowcount
        conn.commit()
    print(f"Đã xóa {removed} tài liệu namespace {NAMESPACE}.")


def ingest_corpus() -> dict[str, str]:
    corpus = load_dataset("yixuantt/MultiHopRAG", "corpus", split="train")
    url_to_file: dict[str, str] = {}
    for index, row in enumerate(corpus, start=1):
        filename = filename_for(index)
        url_to_file[row["url"]] = filename
        title = row["title"] or filename
        source = row["source"] or ""
        body = row["body"] or ""
        text = f"## {title}\n\nSource: {source}\n\n{body}"
        ingest_plain_text(filename, text, namespace=NAMESPACE)
        if index % 25 == 0 or index == len(corpus):
            print(f"Corpus {index}/{len(corpus)}", flush=True)
    return url_to_file


def map_questions(url_to_file: dict[str, str]) -> None:
    qa = load_dataset("yixuantt/MultiHopRAG", "MultiHopRAG", split="train")
    with connect() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT d.filename, c.chunk_index, c.content
                FROM chunks c
                JOIN documents d ON d.id = c.document_id
                WHERE d.namespace = %s;
                """,
                (NAMESPACE,),
            )
            by_file: dict[str, list[tuple[int, str]]] = {}
            for filename, chunk_index, content in cur.fetchall():
                by_file.setdefault(filename, []).append((int(chunk_index), normalize(content)))

    items = []
    unmapped = 0
    for row in qa:
        files = []
        found = []
        seen = set()
        for ev in row["evidence_list"]:
            filename = url_to_file.get(ev["url"])
            if not filename:
                continue
            files.append(filename)
            needle = normalize(ev["fact"] or "")[:120]
            if len(needle) < 20:
                continue
            for chunk_index, content in by_file.get(filename, []):
                key = (filename, chunk_index)
                if key in seen or needle not in content:
                    continue
                seen.add(key)
                found.append({"filename": filename, "chunk_index": chunk_index})
                break
        if not found:
            unmapped += 1
        items.append(
            {
                "id": f"mh_{len(items) + 1:04d}",
                "question": row["query"],
                "expected_answer": row["answer"],
                "question_type": row["question_type"],
                "namespace": NAMESPACE,
                "source_files": sorted(set(files)),
                "relevant_chunk_indices": found,
            }
        )

    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUT_PATH.write_text(json.dumps(items, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"Đã ghi {len(items)} câu hỏi, {unmapped} câu chưa map được chunk, vào {OUT_PATH}")


def main() -> None:
    url_to_file = ingest_corpus()
    map_questions(url_to_file)


if __name__ == "__main__":
    main()
