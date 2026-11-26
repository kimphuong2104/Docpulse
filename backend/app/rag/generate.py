from typing import Any

from google import genai
from openai import OpenAI

from app.config import LLM_API_KEY, LLM_MODEL, LLM_PROVIDER, OPENAI_BASE_URL
from app.rag.retrieve import as_metadata


def _format_source_label(idx: int, ch: dict[str, Any]) -> str:
    meta = as_metadata(ch.get("metadata"))
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
    history: list[dict[str, str]] | None = None,
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
        meta = as_metadata(ch.get("metadata"))
        source_blocks.append(f"[{_format_source_label(idx, ch)}]\n{ch['content']}")
        citations.append(
            {
                "source_id": idx,
                "chunk_id": ch.get("chunk_id"),
                "document_id": ch.get("document_id"),
                "filename": ch.get("filename"),
                "chunk_index": ch.get("chunk_index"),
                "heading_path": meta.get("heading_path"),
                "content": ch.get("content"),
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
        "4. Trả lời ngắn gọn, rõ ràng, đúng trọng tâm câu hỏi bằng tiếng Việt.\n"
        "5. Lịch sử hội thoại chỉ dùng để hiểu câu hỏi đang nối tiếp. Không lấy sự kiện từ lịch sử "
        "nếu sự kiện đó không xuất hiện trong NGỮ CẢNH."
    )
    history_block = ""
    if history:
        lines = []
        for message in history:
            speaker = "Người dùng" if message["role"] == "user" else "Trợ lý"
            lines.append(f"{speaker}: {message['content']}")
        history_block = "LỊCH SỬ HỘI THOẠI (không phải nguồn sự kiện):\n" + "\n".join(lines) + "\n\n"
    user_prompt = (
        f"NGỮ CẢNH:\n{context_text}\n\n"
        f"{history_block}"
        f"CÂU HỎI GỐC:\n{question}\n\n"
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
