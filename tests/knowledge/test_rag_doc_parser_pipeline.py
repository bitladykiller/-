import app.knowledge.infrastructure.doc_parser.pipeline as pipeline
import pytest
from app.knowledge.infrastructure.doc_parser.config import ParserConfig
from app.knowledge.infrastructure.doc_parser.exceptions import (
    DocumentParseError,
    UnsupportedFileTypeError,
)
from app.knowledge.infrastructure.doc_parser.models import (
    DocumentChunk,
    MarkdownBlock,
    MarkdownSection,
    ParsedMarkdownDocument,
)


def _build_section() -> MarkdownSection:
    return MarkdownSection(
        section_id="sec-1",
        level=1,
        title="Title",
        section_path="Title",
        h1="Title",
        content="ignored",
    )


class _FakeCleaner:
    def clean(self, markdown: str) -> str:
        return markdown


class _FakeHeadingParser:
    def parse(self, markdown: str):
        return [_build_section()]


def test_parse_document_routes_blocks_to_expected_splitters(monkeypatch) -> None:
    calls: dict[str, object] = {
        "table_split": [],
        "code_split": [],
        "text_split": [],
    }

    class FakePDFParser:
        def __init__(self, config: ParserConfig) -> None:
            self.config = config

        def parse(self, file_path: str, doc_id: str) -> ParsedMarkdownDocument:
            return ParsedMarkdownDocument(
                doc_id=doc_id,
                source_file=file_path,
                markdown="raw markdown",
                metadata={"parser_name": "FakePDFParser"},
            )

    class FakeBlockParser:
        def parse(self, section: MarkdownSection):
            return [
                MarkdownBlock(
                    block_id="tbl",
                    block_type="table",
                    content="|h|\n|---|\n|a|",
                    section_path=section.section_path,
                    h1=section.h1,
                ),
                MarkdownBlock(
                    block_id="code",
                    block_type="code",
                    content="```python\nprint(1)\n```",
                    section_path=section.section_path,
                    h1=section.h1,
                    metadata={"language": "python"},
                ),
                MarkdownBlock(
                    block_id="txt",
                    block_type="text",
                    content="plain text",
                    section_path=section.section_path,
                    h1=section.h1,
                ),
                MarkdownBlock(
                    block_id="img",
                    block_type="image_caption",
                    content="x" * 50,
                    section_path=section.section_path,
                    h1=section.h1,
                ),
            ]

    class FakeTableSplitter:
        def __init__(self, max_rows_per_chunk: int) -> None:
            self.max_rows_per_chunk = max_rows_per_chunk

        def split(
            self,
            block: MarkdownBlock,
            doc_id: str,
            source_file: str,
            chunk_id_prefix: str = "",
        ):
            calls["table_split"].append(
                (block.block_id, doc_id, source_file, chunk_id_prefix, self.max_rows_per_chunk)
            )
            return [
                DocumentChunk(
                    chunk_id="table-chunk",
                    doc_id=doc_id,
                    source_file=source_file,
                    chunk_type="table",
                    section_path=block.section_path,
                    raw_text="table raw",
                    embedding_text="table embedding",
                )
            ]

    class FakeCodeSplitter:
        def __init__(self, max_lines_per_chunk: int) -> None:
            self.max_lines_per_chunk = max_lines_per_chunk

        def split(self, code_block: str, language: str = ""):
            calls["code_split"].append((code_block, language, self.max_lines_per_chunk))
            return [f"CODE::{language}::{code_block}"]

    class FakeTextSplitter:
        def __init__(self, chunk_size: int, chunk_overlap: int) -> None:
            self.chunk_size = chunk_size
            self.chunk_overlap = chunk_overlap

        def split(self, text: str):
            calls["text_split"].append((text, self.chunk_size, self.chunk_overlap))
            return [f"TEXT::{text}"]

    monkeypatch.setattr(pipeline, "DoclingPDFParser", FakePDFParser)
    monkeypatch.setattr(pipeline, "MarkdownCleaner", _FakeCleaner)
    monkeypatch.setattr(pipeline, "HeadingParser", _FakeHeadingParser)
    monkeypatch.setattr(pipeline, "BlockParser", FakeBlockParser)
    monkeypatch.setattr(pipeline, "TableSplitter", FakeTableSplitter)
    monkeypatch.setattr(pipeline, "CodeSplitter", FakeCodeSplitter)
    monkeypatch.setattr(pipeline, "TextSplitter", FakeTextSplitter)

    chunks = pipeline.parse_document(
        "demo.pdf",
        doc_id="doc-1",
        config=ParserConfig(text_chunk_size=10, text_chunk_overlap=2),
    )

    assert [chunk.chunk_type for chunk in chunks] == ["table", "code", "text", "image_caption"]
    assert chunks[0].raw_text == "table raw"
    assert chunks[1].raw_text.startswith("CODE::python::")
    assert chunks[2].raw_text == "TEXT::plain text"
    assert chunks[3].raw_text == f"TEXT::{'x' * 50}"
    assert calls["table_split"] == [
        ("tbl", "doc-1", "demo.pdf", "doc-1_tbl_", 50)
    ]
    assert calls["code_split"] == [
        ("```python\nprint(1)\n```", "python", 120)
    ]
    assert calls["text_split"] == [
        ("plain text", 10, 2),
        ("x" * 50, 10, 2),
    ]


def test_parse_document_falls_back_to_python_docx_parser(monkeypatch) -> None:
    parse_calls: list[str] = []

    class FailingDocxParser:
        def __init__(self, config: ParserConfig) -> None:
            self.config = config

        def parse(self, file_path: str, doc_id: str) -> ParsedMarkdownDocument:
            parse_calls.append("docling")
            raise DocumentParseError("boom", file_path=file_path, parser_name="docling")

    class FallbackDocxParser:
        def __init__(self, config: ParserConfig) -> None:
            self.config = config

        def parse(self, file_path: str, doc_id: str) -> ParsedMarkdownDocument:
            parse_calls.append("fallback")
            return ParsedMarkdownDocument(
                doc_id=doc_id,
                source_file=file_path,
                markdown="fallback markdown",
                metadata={"parser_name": "DocxFallbackParser"},
            )

    class FakeBlockParser:
        def parse(self, section: MarkdownSection):
            return [
                MarkdownBlock(
                    block_id="txt",
                    block_type="text",
                    content="fallback text",
                    section_path=section.section_path,
                    h1=section.h1,
                )
            ]

    class FakeTextSplitter:
        def __init__(self, chunk_size: int, chunk_overlap: int) -> None:
            self.chunk_size = chunk_size
            self.chunk_overlap = chunk_overlap

        def split(self, text: str):
            return [text]

    monkeypatch.setattr(pipeline, "DoclingDOCXParser", FailingDocxParser)
    monkeypatch.setattr(pipeline, "DocxFallbackParser", FallbackDocxParser)
    monkeypatch.setattr(pipeline, "MarkdownCleaner", _FakeCleaner)
    monkeypatch.setattr(pipeline, "HeadingParser", _FakeHeadingParser)
    monkeypatch.setattr(pipeline, "BlockParser", FakeBlockParser)
    monkeypatch.setattr(pipeline, "TextSplitter", FakeTextSplitter)

    chunks = pipeline.parse_document(
        "demo.docx",
        doc_id="doc-2",
        config=ParserConfig(),
    )

    assert parse_calls == ["docling", "fallback"]
    assert [chunk.raw_text for chunk in chunks] == ["fallback text"]


def test_parse_document_rejects_unsupported_extension() -> None:
    with pytest.raises(UnsupportedFileTypeError):
        pipeline.parse_document("demo.txt", doc_id="doc-3", config=ParserConfig())
