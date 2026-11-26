import json
import sys
from pathlib import Path
from typing import Any
from app.rag import embed_query, retrieve_top_chunks


def recall_at_k(retrieved_pairs: set[tuple[str, int]], relevant_pairs: set[tuple[str, int]]) -> float:
    if not relevant_pairs:
        return 0.0
    hits = len(retrieved_pairs.intersection(relevant_pairs))
    return hits / len(relevant_pairs)


def gold_pairs(item: dict[str, Any]) -> set[tuple[str, int]]:
    raw = item.get("relevant_chunk_indices") or []
    if raw and isinstance(raw[0], dict):
        return {(row["filename"], int(row["chunk_index"])) for row in raw}
    filename = item.get("filename")
    if not filename and item.get("source_files"):
        filename = item["source_files"][0]
    if not filename:
        return set()
    return {(filename, int(idx)) for idx in raw}


def evaluate(dataset_path: Path, top_k: int = 5, mode: str = "hybrid") -> None:
    if not dataset_path.exists():
        print(f"Không tìm thấy file dataset: {dataset_path}")
        sys.exit(1)

    with dataset_path.open("r", encoding="utf-8") as f:
        samples: list[dict[str, Any]] = json.load(f)

    if not samples:
        print("Dataset rỗng, hãy thêm ít nhất 1 câu hỏi kiểm thử.")
        sys.exit(1)

    modes = ["hybrid", "dense"] if mode == "both" else [mode]
    totals = {name: {"recall": 0.0, "hits": 0, "scored": 0} for name in modes}

    print(f"=== ĐÁNH GIÁ RECALL@{top_k} ({', '.join(modes)}) TRÊN {len(samples)} CÂU HỎI ===\n")

    for idx, item in enumerate(samples, start=1):
        question: str = item["question"]
        relevant_pairs = gold_pairs(item)
        if not relevant_pairs:
            print(f"[{idx}/{len(samples)}] [SKIP] không map được chunk | {item.get('id', '')}")
            continue

        q_vec = embed_query(question)
        for name in modes:
            top_chunks = retrieve_top_chunks(
                query_vector=q_vec,
                top_k=top_k,
                mode=name,
                question=question,
                namespace=item.get("namespace"),
            )
            retrieved_pairs = {(ch["filename"], int(ch["chunk_index"])) for ch in top_chunks}
            score = recall_at_k(retrieved_pairs, relevant_pairs)
            is_hit = len(retrieved_pairs.intersection(relevant_pairs)) > 0
            totals[name]["recall"] += score
            totals[name]["hits"] += int(is_hit)
            totals[name]["scored"] += 1
            if not is_hit:
                print(f"[{idx}/{len(samples)}] [{name} MISS] Recall@{top_k}={score:.2f} | {question[:140]}")

        if idx % 50 == 0:
            hybrid = totals.get("hybrid")
            if hybrid and hybrid["scored"]:
                print(f"... {idx}/{len(samples)} hybrid mean={hybrid['recall'] / hybrid['scored']:.3f}", flush=True)

    print("\n=== TỔNG KẾT BASELINE ===")
    for name in modes:
        scored = totals[name]["scored"]
        if not scored:
            print(f"{name}: không có câu hỏi nào map được chunk.")
            continue
        avg_recall = totals[name]["recall"] / scored
        hit_rate = totals[name]["hits"] / scored
        print(f"{name} Mean Recall@{top_k}: {avg_recall:.4f} ({avg_recall * 100:.1f}%) trên {scored} câu")
        print(f"{name} Hit Rate@{top_k}  : {hit_rate:.4f} ({totals[name]['hits']}/{scored})")


if __name__ == "__main__":
    target = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(__file__).with_name("eval_dataset.json")
    mode_arg = sys.argv[2] if len(sys.argv) > 2 else "hybrid"
    if mode_arg not in ("hybrid", "dense", "both"):
        print("Chế độ phải là hybrid hoặc dense.")
        sys.exit(1)
    evaluate(target, top_k=5, mode=mode_arg)