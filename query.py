import json
import os
import sys
import time
from typing import Any
import psycopg
from pgvector.psycopg import register_vector
from dotenv import load_dotenv
from google import genai
from openai import OpenAI

load_dotenv()

DATABASE_URL = os.environ["DATABASE_URL"]
EMBEDDING_PROVIDER = os.getenv("EMBEDDING_PROVIDER", "gemini").strip().lower()
EMBEDDING_API_KEY = os.environ["EMBEDDING_API_KEY"]
EMBEDDING_MODEL = os.getenv("EMBEDDING_MODEL", "text-embedding-004")

LLM_PROVIDER = os.getenv("LLM_PROVIDER", EMBEDDING_PROVIDER).strip().lower()
LLM_API_KEY = os.getenv("LLM_API_KEY", EMBEDDING_API_KEY)
LLM_MODEL = os.getenv("LLM_MODEL", "gemini-2.5-flash" if LLM_PROVIDER == "gemini" else "gpt-4o-mini")
OPENAI_BASE_URL = os.getenv("OPENAI_BASE_URL")


def embed_query(text: str) -> list[float]:
    if EMBEDDING_PROVIDER == "gemini":
        client = genai.Client(api_key=EMBEDDING_API_KEY)
        resp = client.models.embed_content(
            model=EMBEDDING_MODEL,
            contents=text,
        )
        return list(resp.embeddings[0].values)

    client = OpenAI(api_key=EMBEDDING_API_KEY, base_url=OPENAI_BASE_URL or None)
    resp = client.embeddings.create(model=EMBEDDING_MODEL, input=text)
    return list(resp.data[0].embedding)


def _as_metadata(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    if isinstance(value, str) and value:
        return json.loads(value)
    return {}


def _build_filter_clause(
    filename: str | None = None,
    heading_contains: str | None = None,
) -> tuple[str, list[Any]]:
    clauses: list[str] = []
    params: list[Any] = []
    if filename:
        clauses.append("d.filename = %s")
        params.append(filename)
    if heading_contains:
        clauses.append("COALESCE(c.metadata->>'heading_path', '') ILIKE %s")
        params.append(f"%{heading_contains}%")
    if not clauses:
        return "", []
    return " AND " + " AND ".join(clauses), params


def retrieve_dense_chunks(
    query_vector: list[float],
    top_k: int = 5,
    filename: str | None = None,
    heading_contains: str | None = None,
) -> list[dict[str, Any]]:
    filter_sql, filter_params = _build_filter_clause(filename, heading_contains)
    sql = f"""
        SELECT
            c.id AS chunk_id,
            c.document_id,
            d.filename,
            c.chunk_index,
            c.content,
            c.metadata,
            1 - (c.embedding <=> %s::vector) AS similarity
        FROM chunks c
        JOIN documents d ON d.id = c.document_id
        WHERE c.embedding IS NOT NULL{filter_sql}
        ORDER BY c.embedding <=> %s::vector
        LIMIT %s;
    """
    params = (query_vector, *filter_params, query_vector, top_k)
    with psycopg.connect(DATABASE_URL) as conn:
        register_vector(conn)
        with conn.cursor() as cur:
            cur.execute(sql, params)
            rows = cur.fetchall()

    return [
        {
            "chunk_id": r[0],
            "document_id": r[1],
            "filename": r[2],
            "chunk_index": r[3],
            "content": r[4],
            "metadata": _as_metadata(r[5]),
            "score": float(r[6]),
            "similarity": float(r[6]),
            "is_hit": True,
        }
        for r in rows
    ]


def retrieve_hybrid_chunks(
    question: str,
    query_vector: list[float],
    top_k: int = 5,
    candidate_k: int = 20,
    rrf_k: int = 60,
    filename: str | None = None,
    heading_contains: str | None = None,
) -> list[dict[str, Any]]:
    filter_sql, filter_params = _build_filter_clause(filename, heading_contains)
    sql = f"""
        WITH dense_ranked AS (
            SELECT
                c.id AS chunk_id,
                c.document_id,
                d.filename,
                c.chunk_index,
                c.content,
                c.metadata,
                1 - (c.embedding <=> %s::vector) AS similarity,
                ROW_NUMBER() OVER (ORDER BY c.embedding <=> %s::vector) AS dense_rank
            FROM chunks c
            JOIN documents d ON d.id = c.document_id
            WHERE c.embedding IS NOT NULL{filter_sql}
            ORDER BY c.embedding <=> %s::vector
            LIMIT %s
        ),
        sparse_ranked AS (
            SELECT
                c.id AS chunk_id,
                c.document_id,
                d.filename,
                c.chunk_index,
                c.content,
                c.metadata,
                ts_rank_cd(c.tsv, plainto_tsquery('simple', %s)) AS bm25_score,
                ROW_NUMBER() OVER (
                    ORDER BY ts_rank_cd(c.tsv, plainto_tsquery('simple', %s)) DESC, c.id ASC
                ) AS sparse_rank
            FROM chunks c
            JOIN documents d ON d.id = c.document_id
            WHERE c.tsv @@ plainto_tsquery('simple', %s){filter_sql}
            ORDER BY bm25_score DESC, c.id ASC
            LIMIT %s
        ),
        rrf_combined AS (
            SELECT
                COALESCE(dr.chunk_id, sr.chunk_id) AS chunk_id,
                COALESCE(dr.document_id, sr.document_id) AS document_id,
                COALESCE(dr.filename, sr.filename) AS filename,
                COALESCE(dr.chunk_index, sr.chunk_index) AS chunk_index,
                COALESCE(dr.content, sr.content) AS content,
                COALESCE(dr.metadata, sr.metadata) AS metadata,
                COALESCE(dr.similarity, 0.0) AS similarity,
                dr.dense_rank,
                sr.sparse_rank,
                (
                    COALESCE(1.0 / (%s + dr.dense_rank), 0.0) +
                    COALESCE(1.0 / (%s + sr.sparse_rank), 0.0)
                ) AS rrf_score
            FROM dense_ranked dr
            FULL OUTER JOIN sparse_ranked sr ON dr.chunk_id = sr.chunk_id
        )
        SELECT
            chunk_id,
            document_id,
            filename,
            chunk_index,
            content,
            metadata,
            similarity,
            dense_rank,
            sparse_rank,
            rrf_score
        FROM rrf_combined
        ORDER BY rrf_score DESC, similarity DESC
        LIMIT %s;
    """
    params = (
        query_vector,
        query_vector,
        *filter_params,
        query_vector,
        candidate_k,
        question,
        question,
        question,
        *filter_params,
        candidate_k,
        rrf_k,
        rrf_k,
        top_k,
    )

    with psycopg.connect(DATABASE_URL) as conn:
        register_vector(conn)
        with conn.cursor() as cur:
            cur.execute(sql, params)
            rows = cur.fetchall()

    return [
        {
            "chunk_id": r[0],
            "document_id": r[1],
            "filename": r[2],
            "chunk_index": r[3],
            "content": r[4],
            "metadata": _as_metadata(r[5]),
            "similarity": float(r[6]),
            "dense_rank": r[7],
            "sparse_rank": r[8],
            "score": float(r[9]),
            "is_hit": True,
        }
        for r in rows
    ]


def retrieve_top_chunks(
    query_vector: list[float],
    top_k: int = 5,
    mode: str = "hybrid",
    question: str = "",
    filename: str | None = None,
    heading_contains: str | None = None,
) -> list[dict[str, Any]]:
    """Trả về đúng top-k chunk truy xuất, chưa mở rộng chunk lân cận."""
    if mode == "dense" or not question.strip():
        return retrieve_dense_chunks(
            query_vector=query_vector,
            top_k=top_k,
            filename=filename,
            heading_contains=heading_contains,
        )
    return retrieve_hybrid_chunks(
        question=question,
        query_vector=query_vector,
        top_k=top_k,
        filename=filename,
        heading_contains=heading_contains,
    )


def expand_neighbor_chunks(
    matched_chunks: list[dict[str, Any]],
    window: int = 1,
) -> list[dict[str, Any]]:
    """
    Với mỗi chunk trúng (chunk_index = i cùng document_id), kéo thêm
    các chunk trong khoảng [i - window, i + window], khử trùng và xếp
    theo (document_id, chunk_index).
    """
    if not matched_chunks or window <= 0:
        return matched_chunks

    hit_ids = {ch["chunk_id"] for ch in matched_chunks}
    target_pairs: set[tuple[int, int]] = set()
    score_map: dict[tuple[int, int], float] = {}

    for ch in matched_chunks:
        doc_id = int(ch["document_id"])
        center_idx = int(ch["chunk_index"])
        center_score = float(ch.get("score", ch.get("similarity", 0.0)))
        for offset in range(-window, window + 1):
            neighbor_idx = center_idx + offset
            if neighbor_idx < 0:
                continue
            target_pairs.add((doc_id, neighbor_idx))
            adjusted = center_score if offset == 0 else center_score * 0.85
            if adjusted > score_map.get((doc_id, neighbor_idx), -1.0):
                score_map[(doc_id, neighbor_idx)] = adjusted

    if not target_pairs:
        return matched_chunks

    placeholders = ", ".join(["(%s, %s)"] * len(target_pairs))
    flat_params: list[Any] = []
    for doc_id, chunk_index in sorted(target_pairs):
        flat_params.extend([doc_id, chunk_index])

    sql = f"""
        SELECT
            c.id AS chunk_id,
            c.document_id,
            d.filename,
            c.chunk_index,
            c.content,
            c.metadata
        FROM chunks c
        JOIN documents d ON d.id = c.document_id
        WHERE (c.document_id, c.chunk_index) IN ({placeholders})
        ORDER BY c.document_id ASC, c.chunk_index ASC;
    """
    with psycopg.connect(DATABASE_URL) as conn:
        with conn.cursor() as cur:
            cur.execute(sql, tuple(flat_params))
            rows = cur.fetchall()

    expanded: list[dict[str, Any]] = []
    seen_chunk_ids: set[int] = set()
    for row in rows:
        chunk_id = row[0]
        if chunk_id in seen_chunk_ids:
            continue
        seen_chunk_ids.add(chunk_id)
        doc_id, chunk_index = row[1], row[3]
        expanded.append(
            {
                "chunk_id": chunk_id,
                "document_id": doc_id,
                "filename": row[2],
                "chunk_index": chunk_index,
                "content": row[4],
                "metadata": _as_metadata(row[5]),
                "score": score_map.get((doc_id, chunk_index), 0.0),
                "is_hit": chunk_id in hit_ids,
            }
        )
    return expanded


def filter_context_chunks(
    chunks: list[dict[str, Any]],
    min_score: float = 0.0,
    max_chunks: int = 10,
) -> list[dict[str, Any]]:
    """Giữ chunk trúng và chunk lân cận đủ điểm, rồi giới hạn số đoạn đưa cho LLM."""
    if not chunks:
        return []

    filtered: list[dict[str, Any]] = []
    for ch in chunks:
        score = float(ch.get("score", 0.0))
        is_hit = bool(ch.get("is_hit", False))
        if not is_hit and min_score > 0.0 and score < min_score:
            continue
        filtered.append(ch)

    if not filtered:
        filtered = chunks[: min(3, len(chunks))]
    return filtered[:max_chunks]


def _format_source_label(idx: int, ch: dict[str, Any]) -> str:
    meta = _as_metadata(ch.get("metadata"))
    heading = meta.get("heading_path")
    role = "HIT" if ch.get("is_hit", True) else "NEIGHBOR"
    parts = [
        f"Nguồn {idx}",
        f"File: {ch.get('filename', 'unknown')}",
        f"Chunk #{ch.get('chunk_index', '?')}",
        f"Type: {role}",
    ]
    if heading:
        parts.append(f"Section: {heading}")
    return " | ".join(parts)


def generate_answer_grounded(
    question: str,
    chunks: list[dict[str, Any]],
) -> dict[str, Any]:
    """Sinh câu trả lời chỉ từ ngữ cảnh đã truy xuất, kèm trích dẫn [Nguồn X]."""
    if not chunks:
        return {
            "answer": (
                "Không tìm thấy đoạn ngữ cảnh phù hợp trong tài liệu để trả lời câu hỏi này. "
                "Tài liệu hiện có chưa đề cập đến thông tin được hỏi."
            ),
            "citations": [],
            "grounded": False,
        }

    source_blocks: list[str] = []
    citations: list[dict[str, Any]] = []
    for idx, ch in enumerate(chunks, start=1):
        meta = _as_metadata(ch.get("metadata"))
        source_blocks.append(f"[{_format_source_label(idx, ch)}]\n{ch['content']}")
        citations.append(
            {
                "source_id": idx,
                "chunk_id": ch.get("chunk_id"),
                "document_id": ch.get("document_id"),
                "filename": ch.get("filename"),
                "chunk_index": ch.get("chunk_index"),
                "heading_path": meta.get("heading_path"),
                "is_hit": bool(ch.get("is_hit", True)),
                "score": round(float(ch.get("score", 0.0)), 6),
            }
        )

    context_text = "\n\n====================\n\n".join(source_blocks)
    system_instruction = (
        "Bạn là hệ thống hỏi đáp tài liệu (Grounded Document QA) đặt độ chính xác và chống bịa đặt lên hàng đầu.\n"
        "QUY TẮC BẮT BUỘC:\n"
        "1. CHỈ sử dụng thông tin xuất hiện rõ ràng trong phần NGỮ CẢNH được cung cấp. Tuyệt đối không suy diễn "
        "ngoài văn bản, không tự điền năm tháng, tên riêng, số liệu hay sự kiện từ kiến thức bên ngoài.\n"
        "2. Mọi khẳng định hoặc thông tin lấy từ ngữ cảnh PHẢI gắn nhãn trích dẫn dạng [Nguồn 1], [Nguồn 2]... "
        "ngay cuối câu hoặc cuối ý tương ứng.\n"
        "3. Nếu NGỮ CẢNH không chứa thông tin hoặc chỉ chứa một phần nhỏ không đủ trả lời câu hỏi: "
        "hãy nói thẳng rằng tài liệu không có đủ thông tin về nội dung được hỏi (và chỉ nêu phần nhỏ có thật "
        "nếu có, kèm trích dẫn [Nguồn X]).\n"
        "4. Trả lời ngắn gọn, rõ ràng, đúng trọng tâm câu hỏi bằng tiếng Việt."
    )
    user_prompt = (
        f"NGỮ CẢNH:\n{context_text}\n\n"
        f"CÂU HỎI:\n{question}\n\n"
        "Hãy trả lời chỉ dựa vào NGỮ CẢNH và gắn [Nguồn X] đúng đoạn đã dùng."
    )

    if LLM_PROVIDER == "gemini":
        client = genai.Client(
            api_key=LLM_API_KEY,
            http_options={"api_version": "v1beta"},
        )
        resp = client.models.generate_content(
            model=LLM_MODEL,
            contents=user_prompt,
            config={"system_instruction": system_instruction, "temperature": 0.1},
        )
        answer_text = (resp.text or "").strip()
    else:
        client = OpenAI(api_key=LLM_API_KEY, base_url=OPENAI_BASE_URL or None)
        resp = client.chat.completions.create(
            model=LLM_MODEL,
            temperature=0.1,
            messages=[
                {"role": "system", "content": system_instruction},
                {"role": "user", "content": user_prompt},
            ],
        )
        answer_text = (resp.choices[0].message.content or "").strip()

    refusal_markers = (
        "không có thông tin",
        "không đề cập",
        "không đủ thông tin",
        "không tìm thấy",
        "chưa được nhắc tới",
        "chưa đề cập",
    )
    lower_ans = answer_text.lower()
    is_refusal = any(marker in lower_ans for marker in refusal_markers) and ("[nguồn" not in lower_ans)
    return {
        "answer": answer_text,
        "citations": citations,
        "grounded": not is_refusal,
    }


def run_query_pipeline(
    question: str,
    top_k: int = 5,
    mode: str = "hybrid",
    neighbor_window: int = 1,
    min_score: float = 0.0,
    filename: str | None = None,
    heading_contains: str | None = None,
) -> dict[str, Any]:
    """Chạy embed, truy xuất, mở rộng ngữ cảnh, sinh câu trả lời và đo thời gian từng bước."""
    t_total_start = time.perf_counter()

    t0 = time.perf_counter()
    q_vec = embed_query(question)
    embed_ms = (time.perf_counter() - t0) * 1000.0

    t0 = time.perf_counter()
    raw_chunks = retrieve_top_chunks(
        query_vector=q_vec,
        top_k=top_k,
        mode=mode,
        question=question,
        filename=filename,
        heading_contains=heading_contains,
    )
    retrieve_ms = (time.perf_counter() - t0) * 1000.0

    t0 = time.perf_counter()
    expanded_chunks = expand_neighbor_chunks(raw_chunks, window=neighbor_window)
    context_chunks = filter_context_chunks(expanded_chunks, min_score=min_score)
    expand_ms = (time.perf_counter() - t0) * 1000.0

    t0 = time.perf_counter()
    gen_result = generate_answer_grounded(question=question, chunks=context_chunks)
    generate_ms = (time.perf_counter() - t0) * 1000.0

    total_ms = (time.perf_counter() - t_total_start) * 1000.0
    return {
        "question": question,
        "mode": mode,
        "answer": gen_result["answer"],
        "grounded": gen_result["grounded"],
        "citations": gen_result["citations"],
        "raw_hit_count": len(raw_chunks),
        "context_chunk_count": len(context_chunks),
        "latency_ms": {
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
    print("\n=== LATENCY (ms) ===")
    print(
        f"embed={latency['embed_ms']}ms | retrieve={latency['retrieve_ms']}ms | "
        f"expand={latency['expand_ms']}ms | generate={latency['generate_ms']}ms | "
        f"total={latency['total_ms']}ms"
    )
    return result


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print('Cách dùng: python query.py "Nội dung câu hỏi của bạn" [dense|hybrid]')
        sys.exit(1)
    mode_arg = "hybrid"
    args = sys.argv[1:]
    if args[-1].lower() in ("dense", "hybrid"):
        mode_arg = args[-1].lower()
        args = args[:-1]
    user_question = " ".join(args)
    ask(user_question, top_k=5, mode=mode_arg)