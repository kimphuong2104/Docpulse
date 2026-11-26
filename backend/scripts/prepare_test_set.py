"""Xuất một tập PDF nhỏ từ MultiHop-RAG và file câu hỏi để chấm DocPulse."""

import json
import re
from pathlib import Path

from datasets import load_dataset
from fpdf import FPDF

REPO_ROOT = Path(__file__).resolve().parents[2]
NAMESPACE = "multihop"


def safe_filename(name: str, idx: int) -> str:
    cleaned = re.sub(r"[^a-zA-Z0-9_-]+", "_", name.strip())[:40].strip("_")
    return f"doc_{idx:02d}_{cleaned or 'article'}.pdf"


def latin1(text: str) -> str:
    return text.encode("latin-1", "replace").decode("latin-1")


def text_to_pdf(title: str, body: str, out_path: Path) -> None:
    pdf = FPDF()
    pdf.set_auto_page_break(auto=True, margin=15)
    pdf.add_page()
    pdf.set_font("Helvetica", "B", 14)
    pdf.multi_cell(0, 8, latin1(title))
    pdf.ln(4)
    pdf.set_font("Helvetica", "", 11)
    pdf.multi_cell(0, 6, latin1(body))
    pdf.output(str(out_path))


def evidence_items(row: dict) -> list[dict]:
    raw = row.get("evidence_list") or row.get("evidences") or []
    items = []
    for ev in raw:
        if isinstance(ev, str):
            items.append({"key": ev[:80], "fact": ev, "title": ev[:80]})
            continue
        fact = ev.get("fact") or ev.get("text") or ev.get("content") or ""
        key = ev.get("url") or ev.get("title") or fact[:80]
        if key and fact:
            items.append({"key": key, "fact": fact, "title": ev.get("title") or key})
    return items


def main(max_docs: int = 5, max_questions: int = 10) -> None:
    out_dir = REPO_ROOT / "test_benchmark_multihop"
    pdf_dir = out_dir / "pdfs"
    pdf_dir.mkdir(parents=True, exist_ok=True)

    dataset = load_dataset("yixuantt/MultiHopRAG", "MultiHopRAG", split="train")
    covered_questions = []
    source_facts: dict[str, list[str]] = {}
    source_title: dict[str, str] = {}

    for row in dataset:
        evidences = evidence_items(row)
        if len(evidences) < 2:
            continue
        keys = {item["key"] for item in evidences}
        if len(source_facts.keys() | keys) > max_docs and not keys <= source_facts.keys():
            continue
        for item in evidences:
            facts = source_facts.setdefault(item["key"], [])
            if item["fact"] not in facts:
                facts.append(item["fact"])
            source_title.setdefault(item["key"], item["title"])
        covered_questions.append((row, evidences))
        if len(covered_questions) >= max_questions:
            break

    title_to_pdf: dict[str, str] = {}
    for idx, (src_key, facts) in enumerate(source_facts.items(), start=1):
        pdf_name = safe_filename(source_title[src_key], idx)
        text_to_pdf(
            title=f"Document {idx}: {source_title[src_key][:80]}",
            body="\n\n".join(facts),
            out_path=pdf_dir / pdf_name,
        )
        title_to_pdf[src_key] = pdf_name

    eval_items = []
    for row, evidences in covered_questions:
        files = []
        snippets = []
        for item in evidences:
            files.append(title_to_pdf[item["key"]])
            snippets.append(item["fact"])
        eval_items.append(
            {
                "id": f"mh_{len(eval_items) + 1:02d}",
                "question": row.get("query") or row.get("question"),
                "expected_answer": row.get("answer"),
                "namespace": NAMESPACE,
                "source_files": sorted(set(files)),
                "expected_quote": " | ".join(snippets),
                "relevant_chunk_indices": [],
            }
        )

    eval_path = out_dir / "eval_multihop.json"
    eval_path.write_text(json.dumps(eval_items, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"Đã xuất {len(title_to_pdf)} file PDF vào {pdf_dir}")
    print(f"Đã lưu {len(eval_items)} câu hỏi đủ chứng cứ vào {eval_path}")


if __name__ == "__main__":
    main()
