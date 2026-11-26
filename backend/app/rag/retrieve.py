import json
from typing import Any

from app.db import connect


def as_metadata(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    if isinstance(value, str) and value:
        return json.loads(value)
    return {}


def _build_filter_clause(
    filename: str | None = None,
    heading_contains: str | None = None,
    namespace: str | None = None,
) -> tuple[str, list[Any]]:
    clauses: list[str] = []
    params: list[Any] = []
    if filename:
        clauses.append("d.filename = %s")
        params.append(filename)
    if namespace:
        clauses.append("d.namespace = %s")
        params.append(namespace)
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
    namespace: str | None = None,
) -> list[dict[str, Any]]:
    filter_sql, filter_params = _build_filter_clause(filename, heading_contains, namespace)
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
    with connect(vectors=True) as conn:
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
            "metadata": as_metadata(r[5]),
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
    namespace: str | None = None,
) -> list[dict[str, Any]]:
    filter_sql, filter_params = _build_filter_clause(filename, heading_contains, namespace)
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

    with connect(vectors=True) as conn:
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
            "metadata": as_metadata(r[5]),
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
    namespace: str | None = None,
) -> list[dict[str, Any]]:
    """Trả về đúng top-k chunk truy xuất, chưa mở rộng chunk lân cận."""
    if mode == "dense" or not question.strip():
        return retrieve_dense_chunks(
            query_vector=query_vector,
            top_k=top_k,
            filename=filename,
            heading_contains=heading_contains,
            namespace=namespace,
        )
    return retrieve_hybrid_chunks(
        question=question,
        query_vector=query_vector,
        top_k=top_k,
        filename=filename,
        heading_contains=heading_contains,
        namespace=namespace,
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
    with connect() as conn:
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
                "metadata": as_metadata(row[5]),
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
