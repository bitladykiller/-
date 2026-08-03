"""Embedding 工厂单测：唯一配置来源 + 进程内共享。"""

from __future__ import annotations

import sys
import types
from typing import Any

import app.shared.core.embeddings as embeddings_module
from app.shared.core.embeddings import (
    create_embedding_model,
    get_embedding_model,
    reset_embedding_model,
)


class FakeConfig:
    def __init__(self, embedding_type: str = "ollama") -> None:
        self.EMBEDDING_TYPE = embedding_type
        self.EMBEDDING_MODEL = "bge-m3"
        self.OLLAMA_BASE_URL = "http://ollama:11434"


def _install_fake_ollama(monkeypatch) -> list[dict[str, Any]]:
    built: list[dict[str, Any]] = []

    class FakeOllamaEmbeddings:
        def __init__(self, model: str, base_url: str) -> None:
            built.append({"model": model, "base_url": base_url})

    module = types.ModuleType("langchain_ollama")
    module.OllamaEmbeddings = FakeOllamaEmbeddings  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "langchain_ollama", module)
    return built


def _install_fake_huggingface(monkeypatch) -> list[dict[str, Any]]:
    built: list[dict[str, Any]] = []

    class FakeHuggingFaceEmbeddings:
        def __init__(self, model_name: str) -> None:
            built.append({"model_name": model_name})

    module = types.ModuleType("langchain_community.embeddings")
    module.HuggingFaceEmbeddings = FakeHuggingFaceEmbeddings  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "langchain_community.embeddings", module)
    return built


def test_create_embedding_model_uses_ollama_settings(monkeypatch) -> None:
    built = _install_fake_ollama(monkeypatch)

    create_embedding_model(FakeConfig("ollama"))

    assert built == [{"model": "bge-m3", "base_url": "http://ollama:11434"}]


def test_create_embedding_model_falls_back_to_huggingface(monkeypatch) -> None:
    built = _install_fake_huggingface(monkeypatch)

    create_embedding_model(FakeConfig("huggingface"))

    assert built == [{"model_name": "bge-m3"}]


def test_get_embedding_model_is_shared_across_callers(monkeypatch) -> None:
    """LTM 与 RAG 必须拿到同一个实例：向量同源 + 权重不重复占内存。"""
    calls: list[int] = []

    def counting_factory() -> object:
        calls.append(1)
        return object()

    reset_embedding_model()
    monkeypatch.setattr(embeddings_module, "create_embedding_model", counting_factory)

    first = get_embedding_model()
    second = get_embedding_model()

    assert first is second
    assert len(calls) == 1
    reset_embedding_model()


def test_reset_embedding_model_forces_rebuild(monkeypatch) -> None:
    reset_embedding_model()
    monkeypatch.setattr(embeddings_module, "create_embedding_model", lambda: object())

    first = get_embedding_model()
    reset_embedding_model()
    second = get_embedding_model()

    assert first is not second
    reset_embedding_model()
