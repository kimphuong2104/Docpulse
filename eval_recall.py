import json
import os
import sys
from pathlib import Path
from typing import Any
from dotenv import load_dotenv

from query import embed_query, retrieve_top_chunks

load_dotenv()


def recall_at_k(retrieved_pairs: set[tuple[str, int]], relevant_pairs: set[tuple[str, int]]) -> float:
    if not relevant_pairs:
        return 0.0
    hits = len(retrieved_pairs.intersection(relevant_pairs))
    return hits / len(relevant_pairs)


def evaluate(dataset_path: Path, top_k: int = 5) -> None:
    if not dataset_path.exists():
        print(f"Không tìm thấy file dataset: {dataset_path}")
        sys.exit(1)

    with dataset_path.open("r", encoding="utf-8") as f:
        samples: list[dict[str, Any]] = json.load(f)

    if not samples:
        print("Dataset rỗng, hãy thêm ít nhất 1 câu hỏi kiểm thử.")
        sys.exit(1)

    recalls: list[float] = []
    hit_count = 0

    print(f"=== ĐÁNH GIÁ RECALL@{top_k} TRÊN {len(samples)} CÂU HỎI ===\n")

    for idx, item in enumerate(samples, start=1):
        question: str = item["question"]
        filename: str = item["filename"]
        relevant_pairs = {(filename, int(c_idx)) for c_idx in item["relevant_chunk_indices"]}

        q_vec = embed_query(question)
        # Đo recall trên chunk gốc, trước khi ask() kéo thêm chunk lân cận.
        top_chunks = retrieve_top_chunks(
    query_vector=q_vec,
    top_k=top_k,
    mode="hybrid",
    question=question,
)

        retrieved_pairs = {(ch["filename"], int(ch["chunk_index"])) for ch in top_chunks}
        score = recall_at_k(retrieved_pairs, relevant_pairs)
        is_hit = len(retrieved_pairs.intersection(relevant_pairs)) > 0

        recalls.append(score)
        if is_hit:
            hit_count += 1

        status = "HIT" if is_hit else "MISS"
        print(f"[{idx}/{len(samples)}] [{status}] Recall@{top_k}={score:.2f} | Q: {question}")
        if not is_hit:
            print(f"    Expected: {sorted(relevant_pairs)}")
            print(f"    Got Top{top_k}: {[(ch['filename'], ch['chunk_index'], round(ch['similarity'], 3)) for ch in top_chunks]}")

    avg_recall = sum(recalls) / len(recalls)
    hit_rate = hit_count / len(samples)

    print("\n=== TỔNG KẾT BASELINE ===")
    print(f"Mean Recall@{top_k}: {avg_recall:.4f} ({avg_recall * 100:.1f}%)")
    print(f"Hit Rate@{top_k}  : {hit_rate:.4f} ({hit_count}/{len(samples)} câu có ít nhất 1 chunk đúng)")


if __name__ == "__main__":
    target = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("eval_dataset.json")
    evaluate(target, top_k=5)