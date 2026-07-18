"""Neo4j 连接缓存测试。

测试通过 AppContainer 管理 Neo4jGraph 连接。
"""

import asyncio

import app.chat.infrastructure.kg.neo4j_conn as kg_conn


class FakeGraph:
    def __init__(self, *, fail_on_query: bool = False, **kwargs) -> None:
        self.fail_on_query = fail_on_query
        self.kwargs = kwargs
        self.query_calls: list[str] = []

    def query(self, statement: str) -> None:
        self.query_calls.append(statement)
        if self.fail_on_query:
            raise RuntimeError("query failed")


class FakeLogger:
    def __init__(self) -> None:
        self.infos: list[str] = []
        self.warnings: list[str] = []
        self.errors: list[tuple[str, bool]] = []

    def info(self, message: str) -> None:
        self.infos.append(message)

    def warning(self, message: str) -> None:
        self.warnings.append(message)

    def error(self, message: str, *args, **kwargs) -> None:
        self.errors.append((message, kwargs.get("exc_info", False)))


class FakeContainer:
    def __init__(self) -> None:
        self.neo4j_graph = None
        self.neo4j_last_health_check_ts = 0.0


def _run(awaitable):
    return asyncio.run(awaitable)


def test_get_neo4j_graph_creates_and_caches_healthy_connection(monkeypatch) -> None:
    created_graphs: list[FakeGraph] = []
    logger = FakeLogger()
    container = FakeContainer()

    def fake_neo4j_graph(**kwargs):
        graph = FakeGraph(**kwargs)
        created_graphs.append(graph)
        return graph

    monkeypatch.setattr(kg_conn, "Neo4jGraph", fake_neo4j_graph)
    monkeypatch.setattr(kg_conn, "logger", logger)
    monkeypatch.setattr(kg_conn.time, "monotonic", lambda: 100.0)

    first = _get_neo4j_graph(container)

    assert first is created_graphs[0]
    assert created_graphs[0].query_calls == ["RETURN 1"]
    assert container.neo4j_graph is first
    assert container.neo4j_last_health_check_ts == 100.0
    assert logger.warnings == []

    monkeypatch.setattr(kg_conn.time, "monotonic", lambda: 110.0)
    second = _get_neo4j_graph(container)

    assert second is first
    assert created_graphs[0].query_calls == ["RETURN 1"]


def test_get_neo4j_graph_reconnects_when_stale_cache_fails_health_check(
    monkeypatch,
) -> None:
    stale_graph = FakeGraph(fail_on_query=True)
    fresh_graphs: list[FakeGraph] = []
    logger = FakeLogger()
    container = FakeContainer()
    container.neo4j_graph = stale_graph
    container.neo4j_last_health_check_ts = 0.0

    def fake_neo4j_graph(**kwargs):
        graph = FakeGraph(**kwargs)
        fresh_graphs.append(graph)
        return graph

    monkeypatch.setattr(kg_conn, "Neo4jGraph", fake_neo4j_graph)
    monkeypatch.setattr(kg_conn, "logger", logger)
    monkeypatch.setattr(kg_conn.time, "monotonic", lambda: 100.0)

    graph = _get_neo4j_graph(container)

    assert stale_graph.query_calls == ["RETURN 1"]
    assert graph is fresh_graphs[0]
    assert fresh_graphs[0].query_calls == ["RETURN 1"]
    assert container.neo4j_graph is graph
    assert container.neo4j_last_health_check_ts == 100.0
    assert logger.infos == ["[neo4j] 缓存连接失效，尝试重连"]
    assert logger.warnings == ["[neo4j] 连接失败，连接可能已断开"]


def test_get_neo4j_graph_returns_none_when_creation_fails(monkeypatch) -> None:
    logger = FakeLogger()
    container = FakeContainer()

    def failing_neo4j_graph(**kwargs):
        raise RuntimeError("connect failed")

    monkeypatch.setattr(kg_conn, "Neo4jGraph", failing_neo4j_graph)
    monkeypatch.setattr(kg_conn, "logger", logger)
    monkeypatch.setattr(kg_conn.time, "monotonic", lambda: 100.0)

    graph = _get_neo4j_graph(container)

    assert graph is None
    assert container.neo4j_graph is None
    assert logger.errors == [("[neo4j] 连接失败，KG 查询将不可用", True)]


def test_get_neo4j_graph_returns_none_when_fresh_connection_fails_health_check(
    monkeypatch,
) -> None:
    logger = FakeLogger()
    created_graphs: list[FakeGraph] = []
    container = FakeContainer()

    def fake_neo4j_graph(**kwargs):
        graph = FakeGraph(fail_on_query=True, **kwargs)
        created_graphs.append(graph)
        return graph

    monkeypatch.setattr(kg_conn, "Neo4jGraph", fake_neo4j_graph)
    monkeypatch.setattr(kg_conn, "logger", logger)
    monkeypatch.setattr(kg_conn.time, "monotonic", lambda: 100.0)

    graph = _get_neo4j_graph(container)

    assert graph is None
    assert created_graphs[0].query_calls == ["RETURN 1"]
    assert container.neo4j_graph is None
    assert logger.warnings == ["[neo4j] 健康检查失败，连接可能已断开"]
    assert logger.errors == [("[neo4j] 新建连接健康检查失败", False)]


def _get_neo4j_graph(container):
    """直接调用内部函数，绕过 get_container 异步调用。"""
    return kg_conn._get_neo4j_graph(container)