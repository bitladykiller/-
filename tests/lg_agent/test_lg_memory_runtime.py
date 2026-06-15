import asyncio
import sys
import types

import app.chat.infrastructure.memory_bridge.context as memory_context
import app.chat.infrastructure.memory_bridge.runtime as lg_memory_runtime


class FakeMiddleware:
    def __init__(self) -> None:
        self.redis_closed = False
        self.milvus_closed = False

        class FakeRedisClient:
            def __init__(inner_self, outer) -> None:
                inner_self._outer = outer

            async def close(inner_self) -> None:
                inner_self._outer.redis_closed = True

        class FakeMilvusClient:
            def __init__(inner_self, outer) -> None:
                inner_self._outer = outer

            def close(inner_self) -> None:
                inner_self._outer.milvus_closed = True

        self.redis_stm = type("FakeRedisSTM", (), {"redis": FakeRedisClient(self)})()
        self.milvus_ltm = type(
            "FakeMilvusLTM",
            (),
            {"milvus_client": FakeMilvusClient(self)},
        )()


def _run(awaitable):
    return asyncio.run(awaitable)


def install_fake_runtime_dependencies(
    monkeypatch,
    *,
    fail_memory_middleware: bool = False,
):
    calls: dict[str, list[object]] = {
        "redis_from_url": [],
        "milvus_uri": [],
    }

    class FakeRedisClient:
        def __init__(self, url: str, decode_responses: bool) -> None:
            self.url = url
            self.decode_responses = decode_responses

    class FakeRedisSTM:
        def __init__(self, redis_client: FakeRedisClient) -> None:
            self.redis = redis_client

    class FakeMilvusClient:
        def __init__(self, uri: str) -> None:
            calls["milvus_uri"].append(uri)
            self.uri = uri

        def close(self) -> None:
            return None

    class FakeEmbeddingModel:
        def __init__(self, **kwargs) -> None:
            self.kwargs = kwargs

    class FakeChatOllama:
        def __init__(self, **kwargs) -> None:
            self.kwargs = kwargs

    class FakeSimpleLongTermMemory:
        def __init__(self, *, milvus_client, embedding_model, collection_name: str) -> None:
            self.milvus_client = milvus_client
            self.embedding_model = embedding_model
            self.collection_name = collection_name

    class FakeMemoryExtractor:
        def __init__(self, *, llm_client) -> None:
            self.llm_client = llm_client

    class FakeConstructedMemoryMiddleware:
        def __init__(self, *, redis_stm, milvus_ltm, memory_extractor) -> None:
            if fail_memory_middleware:
                raise RuntimeError("boom")
            self.redis_stm = redis_stm
            self.milvus_ltm = milvus_ltm
            self.memory_extractor = memory_extractor

    redis_asyncio = types.ModuleType("redis.asyncio")
    redis_asyncio.from_url = lambda url, decode_responses=True: (
        calls["redis_from_url"].append((url, decode_responses))
        or FakeRedisClient(url, decode_responses)
    )
    redis_module = types.ModuleType("redis")
    redis_module.asyncio = redis_asyncio
    monkeypatch.setitem(sys.modules, "redis", redis_module)
    monkeypatch.setitem(sys.modules, "redis.asyncio", redis_asyncio)

    pymilvus_module = types.ModuleType("pymilvus")
    pymilvus_module.MilvusClient = FakeMilvusClient
    monkeypatch.setitem(sys.modules, "pymilvus", pymilvus_module)

    langchain_ollama_module = types.ModuleType("langchain_ollama")
    langchain_ollama_module.OllamaEmbeddings = FakeEmbeddingModel
    langchain_ollama_module.ChatOllama = FakeChatOllama
    monkeypatch.setitem(sys.modules, "langchain_ollama", langchain_ollama_module)

    stm_module = types.ModuleType("app.knowledge.infrastructure.stm.redis_short_term_memory")
    stm_module.RedisShortTermMemory = FakeRedisSTM
    monkeypatch.setitem(
        sys.modules,
        "app.knowledge.infrastructure.stm.redis_short_term_memory",
        stm_module,
    )

    ltm_module = types.ModuleType("app.knowledge.infrastructure.ltm.simple_long_term_memory")
    ltm_module.SimpleLongTermMemory = FakeSimpleLongTermMemory
    monkeypatch.setitem(
        sys.modules,
        "app.knowledge.infrastructure.ltm.simple_long_term_memory",
        ltm_module,
    )

    extractor_module = types.ModuleType("app.knowledge.infrastructure.orchestration.memory_extractor")
    extractor_module.MemoryExtractor = FakeMemoryExtractor
    monkeypatch.setitem(
        sys.modules,
        "app.knowledge.infrastructure.orchestration.memory_extractor",
        extractor_module,
    )

    middleware_module = types.ModuleType("app.knowledge.infrastructure.orchestration.memory_middleware")
    middleware_module.MemoryMiddleware = FakeConstructedMemoryMiddleware
    monkeypatch.setitem(
        sys.modules,
        "app.knowledge.infrastructure.orchestration.memory_middleware",
        middleware_module,
    )

    monkeypatch.setattr(lg_memory_runtime.settings._business, "EMBEDDING_TYPE", "ollama")
    monkeypatch.setattr(lg_memory_runtime.settings._business, "EMBEDDING_MODEL", "fake-embedding")
    monkeypatch.setattr(lg_memory_runtime.settings._business, "OLLAMA_BASE_URL", "http://ollama.local")
    monkeypatch.setattr(
        lg_memory_runtime.settings._business,
        "AGENT_SERVICE",
        lg_memory_runtime.ServiceType.OLLAMA,
    )
    monkeypatch.setattr(lg_memory_runtime.settings._business, "OLLAMA_AGENT_MODEL", "fake-agent")
    monkeypatch.setattr(
        lg_memory_runtime.settings._business,
        "MILVUS_COLLECTION_NAME",
        "memory_collection",
    )
    monkeypatch.setattr(lg_memory_runtime.settings._infra, "REDIS_HOST", "fake-redis")
    monkeypatch.setattr(lg_memory_runtime.settings._infra, "REDIS_PORT", 6380)
    monkeypatch.setattr(lg_memory_runtime.settings._infra, "REDIS_DB", 7)
    monkeypatch.setattr(lg_memory_runtime.settings._infra, "REDIS_PASSWORD", "")
    monkeypatch.setattr(lg_memory_runtime.settings._infra, "MILVUS_HOST", "fake-milvus")
    monkeypatch.setattr(lg_memory_runtime.settings._infra, "MILVUS_PORT", 19531)

    return calls
def test_get_memory_middleware_caches_created_instance(monkeypatch) -> None:
    calls = install_fake_runtime_dependencies(monkeypatch)
    monkeypatch.setattr(lg_memory_runtime, "_memory_middleware_instance", None)

    first = _run(lg_memory_runtime.get_memory_middleware())
    second = _run(lg_memory_runtime.get_memory_middleware())

    assert first is second
    assert first is not None
    assert calls["redis_from_url"] == [("redis://fake-redis:6380/7", True)]
    assert calls["milvus_uri"] == ["fake-milvus:19531"]
    assert first.milvus_ltm.collection_name == "memory_collection"
    assert first.memory_extractor.llm_client.kwargs == {
        "model": "fake-agent",
        "base_url": "http://ollama.local",
        "temperature": 0.3,
    }


def test_get_memory_middleware_logs_and_returns_none_on_failure(monkeypatch) -> None:
    messages: list[tuple[str, bool]] = []
    monkeypatch.setattr(lg_memory_runtime, "_memory_middleware_instance", None)

    class FakeLogger:
        def error(self, message: str, *args, **kwargs) -> None:
            messages.append((message, kwargs.get("exc_info", False)))

    install_fake_runtime_dependencies(monkeypatch, fail_memory_middleware=True)
    monkeypatch.setattr(lg_memory_runtime, "logger", FakeLogger())

    result = _run(lg_memory_runtime.get_memory_middleware())

    assert result is None
    assert messages == [("MemoryMiddleware 初始化失败，将以无记忆模式运行", True)]


def test_close_memory_middleware_closes_resources_and_resets_singleton(monkeypatch) -> None:
    middleware = FakeMiddleware()
    monkeypatch.setattr(lg_memory_runtime, "_memory_middleware_instance", middleware)

    _run(lg_memory_runtime.close_memory_middleware())

    assert middleware.redis_closed is True
    assert middleware.milvus_closed is True
    assert lg_memory_runtime._memory_middleware_instance is None
