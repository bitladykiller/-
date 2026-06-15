import pytest
import rag_doc_parser.pipeline as pipeline
from rag_doc_parser.config import ParserConfig
from rag_doc_parser.exceptions import DocumentParseError, UnsupportedFileTypeError
from rag_doc_parser.markdown.block_parser import BlockParser
from rag_doc_parser.markdown.heading_parser import HeadingParser
from rag_doc_parser.models import (
    DocumentChunk,
    MarkdownBlock,
    MarkdownSection,
)
from rag_doc_parser.parsers.docling_pdf_parser import DoclingPDFParser
from rag_doc_parser.splitters.text_splitter import TextSplitter


def _build_section() -> MarkdownSection:
    return MarkdownSection(
        section_path="Title",
        content="ignored",
    )


class _FakeCleaner:
    def clean(self, markdown: str) -> str:
        return markdown


class _FakeHeadingParser:
    def parse(self, markdown: str):
        return [_build_section()]


def test_heading_parser_builds_nested_section_paths() -> None:
    sections = HeadingParser().parse(
        "# 一级标题\n"
        "一级内容\n"
        "## 二级标题\n"
        "二级内容\n"
        "### 三级标题\n"
        "三级内容\n"
    )

    assert [section.section_path for section in sections] == [
        "一级标题",
        "一级标题 > 二级标题",
        "一级标题 > 二级标题 > 三级标题",
    ]


def test_heading_parser_uses_default_title_for_headingless_markdown() -> None:
    sections = HeadingParser().parse("没有标题的正文")

    assert len(sections) == 1
    assert sections[0].section_path == "Untitled"
    assert sections[0].content == "没有标题的正文"


def test_block_parser_sets_language_metadata_for_code_blocks() -> None:
    blocks = BlockParser().parse(
        MarkdownSection(
            section_path="Title",
            content="```python\nprint(1)\n```\n\n普通段落",
        )
    )

    assert [(block.block_type, block.metadata) for block in blocks] == [
        ("code", {"language": "python"}),
        ("text", {}),
    ]


def test_docling_pdf_parser_formats_picture_annotation_without_fake_optional_index() -> None:
    parser = object.__new__(DoclingPDFParser)

    unnamed = parser._format_picture_annotation(
        type("Picture", (), {"caption": None, "text": None, "description": "desc"})(),
        index=0,
    )
    numbered = parser._format_picture_annotation(
        type("Picture", (), {"caption": None, "text": None, "description": "desc"})(),
        index=2,
    )

    assert unnamed == ":::image_caption\n\ndesc\n:::"
    assert numbered == ":::image_caption\ntitle: 图片 2\n\ndesc\n:::"


def test_docling_pdf_parser_reads_fixed_vlm_api_key_env(monkeypatch) -> None:
    monkeypatch.setenv("VLM_API_KEY", "secret-key")

    parser = DoclingPDFParser(ParserConfig())

    assert parser._vlm_api_key == "secret-key"


def test_text_splitter_applies_overlap_to_following_chunks() -> None:
    splitter = TextSplitter(chunk_size=8, chunk_overlap=2)

    chunks = splitter.split("abcd efgh ijkl")

    assert chunks == ["abcd", "cdefgh", "ghijkl"]


def test_parse_document_builds_default_config_from_env(monkeypatch) -> None:
    captured: dict[str, ParserConfig] = {}

    class FakePDFParser:
        def __init__(self, config: ParserConfig) -> None:
            captured["config"] = config

        def parse(self, file_path: str) -> str:
            return "raw markdown"

    class EmptyBlockParser:
        def parse(self, section: MarkdownSection):
            return []

    monkeypatch.setenv("VLM_API_BASE_URL", "https://vlm.example/v1/chat/completions")
    monkeypatch.setenv("VLM_MODEL", "fake-vlm")
    monkeypatch.setattr(pipeline, "DoclingPDFParser", FakePDFParser)
    monkeypatch.setattr(pipeline, "MarkdownCleaner", _FakeCleaner)
    monkeypatch.setattr(pipeline, "HeadingParser", _FakeHeadingParser)
    monkeypatch.setattr(pipeline, "BlockParser", EmptyBlockParser)

    assert pipeline.parse_document("demo.pdf", doc_id="doc-env") == []
    assert captured["config"].vlm_api_base_url == "https://vlm.example/v1/chat/completions"
    assert captured["config"].vlm_model == "fake-vlm"


def test_parse_document_always_runs_markdown_cleaner(monkeypatch) -> None:
    clean_calls: list[str] = []

    class FakePDFParser:
        def __init__(self, config: ParserConfig) -> None:
            self.config = config

        def parse(self, file_path: str) -> str:
            return "raw markdown"

    class TrackingCleaner:
        def clean(self, markdown: str) -> str:
            clean_calls.append(markdown)
            return markdown

    class EmptyBlockParser:
        def parse(self, section: MarkdownSection):
            return []

    monkeypatch.setattr(pipeline, "DoclingPDFParser", FakePDFParser)
    monkeypatch.setattr(pipeline, "MarkdownCleaner", TrackingCleaner)
    monkeypatch.setattr(pipeline, "HeadingParser", _FakeHeadingParser)
    monkeypatch.setattr(pipeline, "BlockParser", EmptyBlockParser)

    assert pipeline.parse_document("demo.pdf", doc_id="doc-clean", config=ParserConfig()) == []
    assert clean_calls == ["raw markdown"]


def test_parse_document_routes_blocks_to_expected_splitters(monkeypatch) -> None:
    calls: dict[str, object] = {
        "table_split": [],
        "code_split": [],
        "text_split": [],
    }

    class FakePDFParser:
        def __init__(self, config: ParserConfig) -> None:
            self.config = config

        def parse(self, file_path: str) -> str:
            return "raw markdown"

    class FakeBlockParser:
        def parse(self, section: MarkdownSection):
            return [
                MarkdownBlock(
                    block_id="tbl",
                    block_type="table",
                    content="|h|\n|---|\n|a|",
                    section_path=section.section_path,
                ),
                MarkdownBlock(
                    block_id="code",
                    block_type="code",
                    content="```python\nprint(1)\n```",
                    section_path=section.section_path,
                    metadata={"language": "python"},
                ),
                MarkdownBlock(
                    block_id="txt",
                    block_type="text",
                    content="plain text",
                    section_path=section.section_path,
                ),
                MarkdownBlock(
                    block_id="img",
                    block_type="image_caption",
                    content="x" * 50,
                    section_path=section.section_path,
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

        def parse(self, file_path: str) -> str:
            parse_calls.append("docling")
            raise DocumentParseError("boom", file_path=file_path, parser_name="docling")

    class FallbackDocxParser:
        def __init__(self, config: ParserConfig) -> None:
            self.config = config

        def parse(self, file_path: str) -> str:
            parse_calls.append("fallback")
            return "fallback markdown"

    class FakeBlockParser:
        def parse(self, section: MarkdownSection):
            return [
                MarkdownBlock(
                    block_id="txt",
                    block_type="text",
                    content="fallback text",
                    section_path=section.section_path,
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
