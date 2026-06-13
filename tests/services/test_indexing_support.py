from pathlib import Path

from app.services.indexing_support import (
    EMPTY_DOCUMENT_MESSAGE,
    FILE_NOT_FOUND_MESSAGE,
    MISSING_DEPENDENCY_MESSAGE,
    build_doc_id,
    build_empty_document_result,
    build_missing_dependency_result,
    coerce_user_id,
    normalize_optional_path,
    resolve_source,
    resolve_source_or_error,
)


def test_normalize_optional_path_and_coerce_user_id_handle_invalid_values() -> None:
    assert normalize_optional_path(Path("demo.pdf")) == Path("demo.pdf")
    assert normalize_optional_path(" demo.pdf ") == Path("demo.pdf")
    assert normalize_optional_path("") is None
    assert normalize_optional_path(123) is None

    assert coerce_user_id(7) == 7
    assert coerce_user_id("9") == 9
    assert coerce_user_id(True) == 0
    assert coerce_user_id("u-1") == 0


def test_resolve_source_normalizes_path_and_user_id() -> None:
    source = resolve_source({"path": "demo.pdf", "user_id": "15"})

    assert source == {"path": Path("demo.pdf"), "user_id": 15}


def test_resolve_source_or_error_returns_missing_file_result(tmp_path: Path) -> None:
    source, error = resolve_source_or_error({"path": str(tmp_path / "missing.pdf")})

    assert source == {"path": tmp_path / "missing.pdf", "user_id": 0}
    assert error == {"status": "error", "message": FILE_NOT_FOUND_MESSAGE}


def test_resolve_source_or_error_rejects_unsupported_extension(tmp_path: Path) -> None:
    file_path = tmp_path / "demo.txt"
    file_path.write_text("hello", encoding="utf-8")

    source, error = resolve_source_or_error({"path": str(file_path), "user_id": 1})

    assert source == {"path": file_path, "user_id": 1}
    assert error == {"status": "error", "message": "不支持的文件类型: .txt"}


def test_build_doc_id_and_result_helpers_return_stable_shapes() -> None:
    doc_id = build_doc_id(5)

    assert doc_id.startswith("upload_5_")
    assert len(doc_id) == len("upload_5_") + 8
    assert build_empty_document_result() == {
        "status": "success",
        "chunks": 0,
        "message": EMPTY_DOCUMENT_MESSAGE,
    }
    assert build_missing_dependency_result({"path": "demo.pdf", "user_id": 3}) == {
        "status": "warning",
        "message": MISSING_DEPENDENCY_MESSAGE,
        "file_info": {"path": "demo.pdf", "user_id": 3},
    }
