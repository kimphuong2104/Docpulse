import time
from typing import Any

from google import genai
from openai import OpenAI

from app.config import LLM_API_KEY, LLM_MODEL, LLM_PROVIDER, OPENAI_BASE_URL

REWRITE_INSTRUCTION = (
    "Bạn viết lại câu hỏi hội thoại thành MỘT câu hỏi độc lập để truy xuất tài liệu.\n"
    "Chỉ trả về câu hỏi đã viết lại. Không giải thích, không trả lời.\n"
    "Câu hỏi phải đủ chủ ngữ, thực thể và mốc thời gian nếu các thông tin đó có trong lịch sử.\n"
    "Nếu câu hỏi hiện tại đã rõ nghĩa, hoặc đã chuyển sang chủ đề khác, hãy giữ nguyên câu hỏi.\n"
    "Không thêm sự kiện không xuất hiện trong lịch sử hoặc câu hỏi hiện tại."
)


def recent_messages(history: list[dict[str, Any]] | None, limit: int) -> list[dict[str, str]]:
    cleaned: list[dict[str, str]] = []
    for item in history or []:
        role = item.get("role")
        content = str(item.get("content") or "").strip()
        if role in ("user", "assistant") and content:
            cleaned.append({"role": role, "content": content[:1200]})
    return cleaned[-limit:]


def last_turns(history: list[dict[str, str]], turns: int = 4) -> list[dict[str, str]]:
    grouped: list[list[dict[str, str]]] = []
    current: list[dict[str, str]] = []
    for message in history:
        if message["role"] == "user" and current:
            grouped.append(current)
            current = [message]
        else:
            current.append(message)
    if current:
        grouped.append(current)
    window: list[dict[str, str]] = []
    for turn in grouped[-turns:]:
        window.extend(turn)
    return window


def _format_history(history: list[dict[str, str]]) -> str:
    lines = []
    for message in history:
        speaker = "Người dùng" if message["role"] == "user" else "Trợ lý"
        lines.append(f"{speaker}: {message['content']}")
    return "\n".join(lines)


def rewrite_standalone_query(
    question: str,
    history: list[dict[str, Any]] | None = None,
) -> tuple[str, float]:
    """Viết câu hỏi hiện tại thành truy vấn độc lập. Lượt đầu không gọi LLM."""
    window = recent_messages(history, limit=6)
    if not window:
        return question, 0.0

    started = time.perf_counter()
    prompt = (
        f"LỊCH SỬ:\n{_format_history(window)}\n\n"
        f"CÂU HỎI HIỆN TẠI:\n{question}"
    )
    if LLM_PROVIDER == "gemini":
        client = genai.Client(api_key=LLM_API_KEY, http_options={"api_version": "v1beta"})
        response = client.models.generate_content(
            model=LLM_MODEL,
            contents=prompt,
            config={"system_instruction": REWRITE_INSTRUCTION, "temperature": 0.0},
        )
        rewritten = (response.text or "").strip()
    else:
        client = OpenAI(api_key=LLM_API_KEY, base_url=OPENAI_BASE_URL or None)
        response = client.chat.completions.create(
            model=LLM_MODEL,
            temperature=0.0,
            messages=[
                {"role": "system", "content": REWRITE_INSTRUCTION},
                {"role": "user", "content": prompt},
            ],
        )
        rewritten = (response.choices[0].message.content or "").strip()

    rewritten = rewritten.strip().strip('"').strip("“”").splitlines()[0].strip()
    elapsed = (time.perf_counter() - started) * 1000.0
    return rewritten or question, elapsed
