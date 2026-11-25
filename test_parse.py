# File: test_parse.py
from pathlib import Path
from chunker import clean_markdown, split_markdown_into_chunks
from document_parser import DoclingDocumentParser


def main() -> None:
    sample_file = "sample_report.pdf"
    output_dir = Path("parsed_output")
    output_dir.mkdir(exist_ok=True)

    parser = DoclingDocumentParser(enable_ocr=False, high_accuracy_tables=True)
    doc = parser.parse(sample_file)

    cleaned_md = clean_markdown(doc.markdown)
    cleaned_path = output_dir / f"{Path(doc.filename).stem}_cleaned.md"
    cleaned_path.write_text(cleaned_md, encoding="utf-8")

    chunks = split_markdown_into_chunks(cleaned_md)
    print(f"File: {doc.filename} | Số chunks: {len(chunks)}")
    print(f"Đã lưu bản đã làm sạch tại: {cleaned_path}")
    for c in chunks[:3]:
        print(f"\n--- Chunk #{c.chunk_index} | Mục: {c.section_title} ---")
        print(c.content[:220])


if __name__ == "__main__":
    main()