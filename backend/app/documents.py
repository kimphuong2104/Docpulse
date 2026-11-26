from typing import Any

from app.db import connect


def list_documents(namespace: str | None = None) -> dict[str, Any]:
    sql = """
        SELECT
            d.id,
            d.filename,
            d.file_hash,
            d.namespace,
            d.created_at,
            COUNT(c.id) AS chunk_count
        FROM documents d
        LEFT JOIN chunks c ON c.document_id = d.id
        WHERE (%s::text IS NULL OR d.namespace = %s)
        GROUP BY d.id
        ORDER BY d.created_at DESC;
    """
    with connect() as conn:
        with conn.cursor() as cur:
            cur.execute(sql, (namespace, namespace))
            rows = cur.fetchall()

    docs = [
        {
            "id": row[0],
            "filename": row[1],
            "file_hash": row[2],
            "namespace": row[3],
            "created_at": row[4].isoformat() if row[4] else None,
            "chunk_count": int(row[5]),
        }
        for row in rows
    ]
    return {"total": len(docs), "documents": docs}


def delete_document(document_id: int) -> tuple[int, str] | None:
    with connect() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "DELETE FROM documents WHERE id = %s RETURNING id, filename",
                (document_id,),
            )
            row = cur.fetchone()
        conn.commit()
    if not row:
        return None
    return int(row[0]), row[1]
