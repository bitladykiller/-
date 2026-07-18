import json
import subprocess
import sys
import textwrap


def _probe_loaded_modules(statement: str) -> list[str]:
    script = textwrap.dedent(
        f"""
        import json
        import sys

        before = set(sys.modules)
        {statement}
        after = set(sys.modules)
        interesting = sorted(
            name
            for name in after - before
            if name.startswith(("app.chat", "app.knowledge", "langgraph", "langchain"))
        )
        print(json.dumps(interesting))
        """
    )
    completed = subprocess.run(
        [sys.executable, "-c", script],
        check=True,
        capture_output=True,
        text=True,
    )
    return json.loads(completed.stdout)


def test_importing_chat_package_does_not_eagerly_load_graph_stack() -> None:
    loaded_modules = _probe_loaded_modules("import app.chat")

    assert loaded_modules == ["app.chat"]


def test_importing_knowledge_package_stays_lightweight() -> None:
    knowledge_modules = _probe_loaded_modules("import app.knowledge")

    assert "app.knowledge.infrastructure.orchestration.memory_middleware" not in knowledge_modules
    assert "app.knowledge.facade" not in knowledge_modules


def test_importing_chat_runtime_modules_loads_only_needed_area() -> None:
    retriever_modules = _probe_loaded_modules(
        "from app.chat.infrastructure.retrievers.retriever_runtime import get_retriever"
    )
    graph_modules = _probe_loaded_modules(
        "from app.chat.infrastructure.graph.builder import graph"
    )

    assert "app.chat.infrastructure.retrievers.retriever_runtime" in retriever_modules
    assert "app.chat.infrastructure.retrievers.retrievers" not in retriever_modules
    assert "app.chat.infrastructure.graph.builder" not in retriever_modules
    assert "app.chat.infrastructure.graph.builder" in graph_modules
