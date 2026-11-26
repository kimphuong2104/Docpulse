import hashlib
from dataclasses import dataclass
from pathlib import Path

from docling.datamodel.base_models import InputFormat
from docling.datamodel.pipeline_options import PdfPipelineOptions, TableFormerMode
from docling.document_converter import DocumentConverter, PdfFormatOption


@dataclass
class ParsedDocument:
    filename: str
    file_hash: str
    markdown: str
    num_pages: int | None = None


class DoclingDocumentParser:
    """Khởi tạo một lần để tái sử dụng model trong suốt vòng đời ứng dụng/worker."""

    def __init__(self, enable_ocr: bool = True, high_accuracy_tables: bool = True) -> None:
        pipeline_options = PdfPipelineOptions()
        pipeline_options.do_ocr = enable_ocr
        pipeline_options.do_table_structure = True
        pipeline_options.table_structure_options.do_cell_matching = True
        pipeline_options.table_structure_options.mode = (
            TableFormerMode.ACCURATE if high_accuracy_tables else TableFormerMode.FAST
        )

        self.converter = DocumentConverter(
            format_options={
                InputFormat.PDF: PdfFormatOption(pipeline_options=pipeline_options)
            }
        )

    @staticmethod
    def compute_sha256(file_path: Path) -> str:
        hasher = hashlib.sha256()
        with file_path.open("rb") as f:
            for chunk in iter(lambda: f.read(8192), b""):
                hasher.update(chunk)
        return hasher.hexdigest()

    def parse(self, file_path_str: str) -> ParsedDocument:
        file_path = Path(file_path_str)
        if not file_path.exists() or not file_path.is_file():
            raise FileNotFoundError(f"Không tìm thấy file: {file_path}")

        file_hash = self.compute_sha256(file_path)
        ext = file_path.suffix.lower()

        # Nếu là .md hoặc .txt thuần thì đọc trực tiếp cho nhanh, không cần qua Docling PDF pipeline
        if ext in {".md", ".txt"}:
            markdown_text = file_path.read_text(encoding="utf-8")
            return ParsedDocument(
                filename=file_path.name,
                file_hash=file_hash,
                markdown=markdown_text,
                num_pages=None,
            )

        conv_result = self.converter.convert(str(file_path))
        markdown_text = conv_result.document.export_to_markdown()

        # Lấy số trang nếu có thông tin từ input/pages
        num_pages = None
        if hasattr(conv_result, "input") and getattr(conv_result.input, "page_count", None):
            num_pages = conv_result.input.page_count

        return ParsedDocument(
            filename=file_path.name,
            file_hash=file_hash,
            markdown=markdown_text,
            num_pages=num_pages,
        )