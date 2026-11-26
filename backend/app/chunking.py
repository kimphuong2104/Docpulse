# File: chunker.py
import re
from dataclasses import dataclass, field
from typing import Any


@dataclass
class ChunkItem:
    chunk_index: int
    section_title: str
    content: str
    metadata: dict[str, Any] = field(default_factory=dict)


# Các từ đơn tiếng Việt hợp lệ có 1 chữ cái in hoa để tránh ghép nhầm khi xử lý drop cap
VALID_SINGLE_CHAR_WORDS = {
    "A", "À", "Á", "Ả", "Ã", "Ạ",
    "E", "È", "É", "Ẻ", "Ẽ", "Ẹ",
    "I", "Ì", "Í", "Ỉ", "Ĩ", "Ị",
    "O", "Ò", "Ó", "Ỏ", "Õ", "Ọ",
    "Ô", "Ồ", "Ố", "Ổ", "Ỗ", "Ộ",
    "Ơ", "Ờ", "Ớ", "Ở", "Ỡ", "Ợ",
    "U", "Ù", "Ú", "Ủ", "Ũ", "Ụ",
    "Ư", "Ừ", "Ứ", "Ử", "Ữ", "Ự",
    "Y", "Ỳ", "Ý", "Ỷ", "Ỹ", "Ỵ",
}


def _fix_drop_caps(text: str) -> str:
    """Ghép các chữ cái đầu đoạn bị tách khoảng trắng kiểu 'T ập sách' -> 'Tập sách'."""
    def repl(match: re.Match) -> str:
        first_char = match.group(1)
        second_char = match.group(2)
        if first_char.upper() in VALID_SINGLE_CHAR_WORDS:
            return match.group(0)
        return f"{first_char}{second_char}"

    return re.sub(
        r"(?m)^([A-ZÀÁẢÃẠĂẰẮẲẴẶÂẦẤẨẪẬĐÈÉẺẼẸÊỀẾỂỄỆÌÍỈĨỊÒÓỎÕỌÔỒỐỔỖỘƠỜỚỞỠỢÙÚỦŨỤƯỪỨỬỮỰỲÝỶỸỴ])\s+([a-zàáảãạăằắẳẵặâầấẩẫậđèéẻẽẹêềếểễệìíỉĩịòóỏõọôồốổỗộơờớởỡợùúủũụưừứửữựỳýỷỹỵ])",
        repl,
        text,
    )


def clean_markdown(raw_md: str) -> str:
    """Làm sạch nhiễu phổ biến từ Markdown do Docling xuất ra."""
    text = raw_md.replace("\r\n", "\n")

    # 1. Xóa các dòng placeholder hình ảnh <!-- image -->
    text = re.sub(r"(?m)^\s*<!--\s*image\s*-->\s*$", "", text)

    # 2. Xóa các dòng chỉ chứa số trang kiểu '. 5 .' hoặc số đứng riêng lẻ
    text = re.sub(r"(?m)^\s*\.\s*\d+\s*\.\s*$", "", text)
    text = re.sub(r"(?m)^\s*\d+\s*$", "", text)

    # 3. Chuẩn hóa drop cap đầu dòng
    text = _fix_drop_caps(text)

    # 4. Nối các đoạn bị ngắt trang giữa câu
    lines = text.split("\n")
    merged_lines: list[str] = []
    for line in lines:
        stripped = line.strip()
        if not stripped:
            merged_lines.append("")
            continue

        is_special_block = stripped.startswith(("#", "-", "*", "|", ">"))
        if (
            merged_lines
            and merged_lines[-1] != ""
            and not merged_lines[-1].lstrip().startswith(("#", "-", "*", "|", ">"))
            and not is_special_block
            and not re.search(r'[.!?…:;”"]\s*$', merged_lines[-1])
        ):
            merged_lines[-1] = f"{merged_lines[-1].rstrip()} {stripped}"
        else:
            merged_lines.append(stripped)

    text = "\n".join(merged_lines)

    # 5. Gom nhiều dòng trống liên tiếp thành tối đa 2 dòng trống
    text = re.sub(r"\n{3,}", "\n\n", text).strip()
    return text


def _heading_path_for_body(section_title: str, body: str) -> str | None:
    """Ghép tiêu đề ## của mục với tiêu đề ### xuất hiện trong phần thân."""
    h2 = None if not section_title or section_title == "General" else section_title.strip()
    h3: str | None = None
    for line in body.splitlines():
        stripped = line.strip()
        if stripped.startswith("### "):
            h3 = stripped[4:].strip() or None
        elif stripped.startswith("## "):
            h2 = stripped[3:].strip() or None
            h3 = None
    parts = [part for part in (h2, h3) if part]
    return " > ".join(parts) if parts else None


def _chunk_metadata(
    filename: str,
    chunk_index: int,
    content: str,
    heading_path: str | None,
) -> dict[str, Any]:
    return {
        "filename": filename,
        "chunk_index": chunk_index,
        "char_count": len(content),
        "heading_path": heading_path,
    }


def split_markdown_into_chunks(
    cleaned_md: str,
    max_chars: int = 900,
    overlap_chars: int = 100,
    filename: str = "",
) -> list[ChunkItem]:
    """
    Chia Markdown theo tiêu đề ##, giữ nguyên khối bảng Markdown (| ... |)
    và giới hạn độ dài chunk vừa phải để nhúng vector.
    Mỗi chunk kèm metadata: filename, chunk_index, char_count, heading_path.
    """
    sections: list[tuple[str, str]] = []
    current_title = "General"
    current_lines: list[str] = []

    for line in cleaned_md.split("\n"):
        if re.match(r"^##\s+", line):
            if current_lines:
                sections.append((current_title, "\n".join(current_lines).strip()))
            current_title = re.sub(r"^##\s+", "", line).strip()
            current_lines = []
        else:
            current_lines.append(line)

    if current_lines:
        sections.append((current_title, "\n".join(current_lines).strip()))

    chunks: list[ChunkItem] = []
    chunk_idx = 0

    for sec_title, sec_body in sections:
        if not sec_body:
            continue

        raw_paragraphs = [p.strip() for p in re.split(r"\n\s*\n", sec_body) if p.strip()]
        blocks: list[str] = []
        table_buffer: list[str] = []

        for para in raw_paragraphs:
            is_table = all(l.strip().startswith("|") for l in para.splitlines() if l.strip())
            if is_table:
                table_buffer.append(para)
            else:
                if table_buffer:
                    blocks.append("\n\n".join(table_buffer))
                    table_buffer = []
                blocks.append(para)

        if table_buffer:
            blocks.append("\n\n".join(table_buffer))

        def append_chunk(body: str) -> None:
            nonlocal chunk_idx
            content = f"[{sec_title}]\n{body}"
            chunks.append(
                ChunkItem(
                    chunk_index=chunk_idx,
                    section_title=sec_title,
                    content=content,
                    metadata=_chunk_metadata(
                        filename,
                        chunk_idx,
                        content,
                        _heading_path_for_body(sec_title, body),
                    ),
                )
            )
            chunk_idx += 1

        buffer = ""
        for block in blocks:
            candidate = f"{buffer}\n\n{block}".strip() if buffer else block
            if len(candidate) <= max_chars:
                buffer = candidate
            else:
                if buffer:
                    append_chunk(buffer)
                    tail = buffer[-overlap_chars:] if overlap_chars > 0 else ""
                    buffer = f"{tail}\n\n{block}".strip() if tail else block
                else:
                    append_chunk(block)
                    buffer = ""

        if buffer:
            append_chunk(buffer)

    return chunks