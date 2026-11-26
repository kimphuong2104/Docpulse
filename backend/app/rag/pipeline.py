import time
from typing import Any

from app.rag.embed import embed_query
from app.rag.generate import generate_answer_grounded
from app.rag.retrieve import expand_neighbor_chunks, filter_context_chunks, retrieve_top_chunks
from app.rag.rewrite import last_turns, recent_messages, rewrite_standalone_query


def run_query_pipeline(
    question: str,
    top_k: int = 5,
    mode: str = "hybrid",
    neighbor_window: int = 1,
    min_score: float = 0.0,
    filename: str | None = None,
    heading_contains: str | None = None,
    history: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Chạy viết lại truy vấn, embed, truy xuất, mở rộng ngữ cảnh và sinh câu trả lời."""
    t_total_start = time.perf_counter()

    standalone_query, rewrite_ms = rewrite_standalone_query(question, history)

    t0 = time.perf_counter()
    q_vec = embed_query(standalone_query)
    embed_ms = (time.perf_counter() - t0) * 1000.0

    t0 = time.perf_counter()
    raw_chunks = retrieve_top_chunks(
        query_vector=q_vec,
        top_k=top_k,
        mode=mode,
        question=standalone_query,
        filename=filename,
        heading_contains=heading_contains,
    )
    retrieve_ms = (time.perf_counter() - t0) * 1000.0

    t0 = time.perf_counter()
    expanded_chunks = expand_neighbor_chunks(raw_chunks, window=neighbor_window)
    context_chunks = filter_context_chunks(expanded_chunks, min_score=min_score)
    expand_ms = (time.perf_counter() - t0) * 1000.0

    t0 = time.perf_counter()
    dialogue = last_turns(recent_messages(history, limit=8), turns=4)
    gen_result = generate_answer_grounded(
        question=question,
        chunks=context_chunks,
        history=dialogue,
    )
    generate_ms = (time.perf_counter() - t0) * 1000.0

    total_ms = (time.perf_counter() - t_total_start) * 1000.0
    return {
        "question": question,
        "rewritten_query": standalone_query,
        "mode": mode,
        "answer": gen_result["answer"],
        "grounded": gen_result["grounded"],
        "citations": gen_result["citations"],
        "raw_hit_count": len(raw_chunks),
        "context_chunk_count": len(context_chunks),
        "session_id": None,
        "latency_ms": {
            "rewrite_ms": round(rewrite_ms, 2),
            "embed_ms": round(embed_ms, 2),
            "retrieve_ms": round(retrieve_ms, 2),
            "expand_ms": round(expand_ms, 2),
            "generate_ms": round(generate_ms, 2),
            "total_ms": round(total_ms, 2),
        },
    }


def ask(
    question: str,
    top_k: int = 5,
    mode: str = "hybrid",
    neighbor_window: int = 1,
    filename: str | None = None,
    heading_contains: str | None = None,
    min_score: float = 0.0,
) -> dict[str, Any]:
    result = run_query_pipeline(
        question=question,
        top_k=top_k,
        mode=mode,
        neighbor_window=neighbor_window,
        min_score=min_score,
        filename=filename,
        heading_contains=heading_contains,
    )

    print(f"\n=== CÂU TRẢ LỜI (mode={result['mode']} | grounded={result['grounded']}) ===")
    print(result["answer"])

    print("\n=== NGUỒN TRÍCH DẪN (CONTEXT CHUNKS) ===")
    for citation in result["citations"]:
        tag = "HIT" if citation["is_hit"] else "NEIGHBOR"
        section = f" | {citation['heading_path']}" if citation.get("heading_path") else ""
        print(
            f"[Nguồn {citation['source_id']}] ({tag}) {citation['filename']} "
            f"#chunk={citation['chunk_index']}{section} | score={citation['score']:.4f}"
        )

    latency = result["latency_ms"]
    if result["rewritten_query"] != question:
        print(f"\n=== TRUY VẤN ĐỘC LẬP ===\n{result['rewritten_query']}")
    print("\n=== LATENCY (ms) ===")
    print(
        f"rewrite={latency['rewrite_ms']}ms | embed={latency['embed_ms']}ms | "
        f"retrieve={latency['retrieve_ms']}ms | expand={latency['expand_ms']}ms | "
        f"generate={latency['generate_ms']}ms | total={latency['total_ms']}ms"
    )
    return result
