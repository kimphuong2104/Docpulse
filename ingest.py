# File: ingest.py
import json
import os
from pathlib import Path
import httpx
import psycopg
from dotenv import load_dotenv

from chunker import ChunkItem, clean_markdown, split_markdown_into_chunks
from document_parser import DoclingDocumentParser

load_dotenv()

DATABASE_URL = os.environ["DATABASE_URL"]
EMBEDDING_PROVIDER = os.getenv("EMBEDDING_PROVIDER", "gemini").lower()
EMBEDDING_API_KEY = os.environ["EMBEDDING_API_KEY"]
EMBEDDING_MODEL = os.getenv("EMBEDDING_MODEL", "gemini-embedding-001")
EMBEDDING_DIM = int(os.getenv("EMBEDDING_DIM", "3072"))


def embed_texts(texts: list[str]) -> list[list[float]]:
    """Tạo vector embedding cho danh sách đoạn văn bản."""
    if not texts:
        return []

    if EMBEDDING_PROVIDER == "gemini":
        # Gọi Gemini batchEmbedContents
        url = (
            f"https://generativelanguage.googleapis.com/v1beta/models/"
            f"{EMBEDDING_MODEL}:batchEmbedContents?key={EMBEDDING_API_KEY}"
        )
        requests_payload = [
            {
                "model": f"models/{EMBEDDING_MODEL}",
                "content": {"parts": [{"text": t}]},
                "outputDimensionality": EMBEDDING_DIM,
            }
            for t in texts
        ]
        with httpx.Client(timeout=30.0) as client:
            resp = client.post(url, json={"requests": requests_payload})
            resp.raise_for_status()
            data = resp.json()
            return [item["values"] for item in data["embeddings"]]

    # Mặc định theo chuẩn OpenAI-compatible embeddings endpoint (OpenAI / OpenRouter)
    base_url = os.getenv("EMBEDDING_BASE_URL", "https://api.openai.com/v1").rstrip("/")
    with httpx.Client(timeout=30.0) as client:
        resp = client.post(
            f"{base_url}/embeddings",
            headers={
                "Authorization": f"Bearer {EMBEDDING_API_KEY}",
                "Content-Type": "application/json",
            },
            json={"model": EMBEDDING_MODEL, "input": texts},
        )
        resp.raise_for_status()
        data = resp.json()
        # Sắp xếp đúng theo index nếu API trả về không theo thứ tự
        items = sorted(data["data"], key=lambda x: x["index"])
        return [item["embedding"] for item in items]


def to_pgvector_literal(vec: list[float]) -> str:
    """Chuyển list[float] thành chuỗi định dạng vector của pgvector: '[0.1,0.2,...]'."""
    return "[" + ",".join(f"{x:.8f}" for x in vec) + "]"


def _refresh_metadata_if_unchanged(cur, document_id: int, chunks: list[ChunkItem]) -> bool:
    """Cập nhật metadata khi nội dung chunk không đổi, để khỏi tạo lại embedding."""
    cur.execute(
        """
        SELECT chunk_index, content
        FROM chunks
        WHERE document_id = %s
        ORDER BY chunk_index
        """,
        (document_id,),
    )
    stored = cur.fetchall()
    if len(stored) != len(chunks):
        return False
    if any(row[0] != chunk.chunk_index or row[1] != chunk.content for row, chunk in zip(stored, chunks)):
        return False

    cur.executemany(
        """
        UPDATE chunks
        SET metadata = %s::jsonb
        WHERE document_id = %s AND chunk_index = %s
        """,
        [
            (json.dumps(chunk.metadata, ensure_ascii=False), document_id, chunk.chunk_index)
            for chunk in chunks
        ],
    )
    return True


_PARSER: DoclingDocumentParser | None = None


def _get_parser() -> DoclingDocumentParser:
    global _PARSER
    if _PARSER is None:
        _PARSER = DoclingDocumentParser(enable_ocr=False, high_accuracy_tables=True)
    return _PARSER


def _ingest_result(
    status: str,
    document_id: int,
    filename: str,
    namespace: str,
    file_hash: str,
    chunk_count: int,
) -> dict:
    return {
        "status": status,
        "document_id": document_id,
        "filename": filename,
        "namespace": namespace,
        "file_hash": file_hash,
        "chunk_count": chunk_count,
    }


def ingest_file(
    file_path: str,
    parser: DoclingDocumentParser,
    max_chars: int = 900,
    overlap_chars: int = 100,
    original_filename: str | None = None,
    namespace: str = "default",
) -> dict:
    """
    Parse -> Clean -> Chunk -> Embed -> Lưu vào PostgreSQL pgvector.
    Trả về thông tin tài liệu, gồm trạng thái created hoặc duplicate.
    """
    parsed = parser.parse(file_path)
    filename = original_filename or parsed.filename
    namespace = namespace.strip() or "default"

    with psycopg.connect(DATABASE_URL) as conn:
        with conn.cursor() as cur:
            cleaned_md = clean_markdown(parsed.markdown)
            chunks: list[ChunkItem] = split_markdown_into_chunks(
                cleaned_md,
                max_chars=max_chars,
                overlap_chars=overlap_chars,
                filename=filename,
            )
            if not chunks:
                raise ValueError(f"Không tạo được chunk nào từ file: {filename}")

            cur.execute(
                "SELECT id, filename, namespace FROM documents WHERE file_hash = %s",
                (parsed.file_hash,),
            )
            existing = cur.fetchone()
            if existing:
                document_id = existing[0]
                stored_namespace = existing[2] or namespace
                if _refresh_metadata_if_unchanged(cur, document_id, chunks):
                    conn.commit()
                    print(
                        f"[OK] File '{filename}' đã có (document_id={document_id}). "
                        f"Đã cập nhật metadata cho {len(chunks)} chunks."
                    )
                    return _ingest_result(
                        "duplicate",
                        document_id,
                        filename,
                        stored_namespace,
                        parsed.file_hash,
                        len(chunks),
                    )
                cur.execute("DELETE FROM chunks WHERE document_id = %s", (document_id,))
            else:
                cur.execute(
                    """
                    INSERT INTO documents (filename, file_hash, namespace)
                    VALUES (%s, %s, %s)
                    RETURNING id
                    """,
                    (filename, parsed.file_hash, namespace),
                )
                document_id = cur.fetchone()[0]
                stored_namespace = namespace

            # 2. Tạo embedding theo từng batch (ví dụ 20 chunk/lần để tránh payload quá lớn)
            vectors: list[list[float]] = []
            batch_size = 20
            for i in range(0, len(chunks), batch_size):
                batch_texts = [c.content for c in chunks[i : i + batch_size]]
                vectors.extend(embed_texts(batch_texts))

            if len(vectors) != len(chunks):
                raise RuntimeError("Số lượng vector trả về không khớp với số lượng chunks.")

            # 3. Lưu chunk, metadata JSONB và vector. Trigger DB tự điền tsv.
            chunk_rows = [
                (
                    document_id,
                    chunk.chunk_index,
                    chunk.content,
                    json.dumps(chunk.metadata, ensure_ascii=False),
                    to_pgvector_literal(vec),
                )
                for chunk, vec in zip(chunks, vectors)
            ]

            cur.executemany(
                """
                INSERT INTO chunks (document_id, chunk_index, content, metadata, embedding)
                VALUES (%s, %s, %s, %s::jsonb, %s::vector)
                ON CONFLICT (document_id, chunk_index) DO UPDATE SET
                    content = EXCLUDED.content,
                    metadata = EXCLUDED.metadata,
                    embedding = EXCLUDED.embedding
                """,
                chunk_rows,
            )

        conn.commit()
        print(
            f"[OK] Đã nạp '{filename}' -> document_id={document_id}, "
            f"tổng {len(chunks)} chunks."
        )
        return _ingest_result(
            "updated" if existing else "created",
            document_id,
            filename,
            stored_namespace,
            parsed.file_hash,
            len(chunks),
        )


def ingest_pdf_file(
    pdf_path: str,
    original_filename: str | None = None,
    namespace: str = "default",
) -> dict:
    """Điểm vào dùng chung cho CLI và API. Parser Docling được giữ lại giữa các lần gọi."""
    return ingest_file(
        pdf_path,
        _get_parser(),
        original_filename=original_filename,
        namespace=namespace,
    )


if __name__ == "__main__":
    import sys

    target_file = sys.argv[1] if len(sys.argv) > 1 else "sample_report.pdf"
    result = ingest_pdf_file(target_file, original_filename=Path(target_file).name)
    print(result)