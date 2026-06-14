import numpy as np

import app.chat.infrastructure.kg_sub_graph.agentic_rag_agents.components.predefined_cypher.utils as predefined_utils


class FakeLLM:
    def __init__(self, content: str) -> None:
        self.content = content
        self.prompts: list[object] = []

    def invoke(self, prompt):
        self.prompts.append(prompt)
        return type("Response", (), {"content": self.content})()


def test_embed_payload_and_fallback_embeddings_are_stable() -> None:
    assert predefined_utils.build_embed_payload("bge-m3", ["a", "b"]) == {
        "model": "bge-m3",
        "input": ["a", "b"],
    }
    assert predefined_utils.fallback_embeddings(2, embedding_dim=3) == [
        [0.0, 0.0, 0.0],
        [0.0, 0.0, 0.0],
    ]


def test_extract_embeddings_and_query_texts_fallback_cleanly() -> None:
    assert predefined_utils.extract_embeddings({"embeddings": [[1.0, 2.0]]}, expected_count=1) == [[1.0, 2.0]]
    assert predefined_utils.extract_embeddings({}, expected_count=2, embedding_dim=2) == [
        [0.0, 0.0],
        [0.0, 0.0],
    ]
    assert predefined_utils.build_query_texts(
        {"query_a": "MATCH ...", "query_b": "MATCH ..."},
        {"query_a": "desc a"},
    ) == (
        ["query_a", "query_b"],
        ["query_a desc a", "query_b"],
    )


def test_parameter_and_json_helpers_handle_realistic_inputs() -> None:
    template = "MATCH (p) WHERE p.ProductName = $product_name AND o.OrderID = $order_id"
    assert predefined_utils.extract_parameter_names(template) == ["product_name", "order_id"]
    assert predefined_utils.extract_parameters_with_rules("查询 小米门锁 的价格", ["product_name"]) == {
        "product_name": "小米门锁"
    }
    assert predefined_utils.extract_parameters_with_rules("订单 10248 的详情", ["order_id"]) == {
        "order_id": "10248"
    }
    payload = predefined_utils._extract_first_json_object(
        '说明 {"query_name":"a","value":"x{y}"} 结尾'
    )
    assert payload == '{"query_name":"a","value":"x{y}"}'
    assert predefined_utils.parse_json_response('前缀 {"foo":"bar"} 后缀') == {"foo": "bar"}
    assert predefined_utils.parse_json_response("没有 JSON") == {}


def test_vector_query_matcher_match_query_filters_by_similarity(monkeypatch) -> None:
    monkeypatch.setattr(predefined_utils._VectorQueryMatcher, "_embed_texts", lambda self, texts: [[1.0, 0.0], [0.0, 1.0]])
    matcher = predefined_utils._VectorQueryMatcher(
        predefined_cypher_dict={"query_a": "A", "query_b": "B"},
        query_descriptions={"query_a": "desc a", "query_b": "desc b"},
        similarity_threshold=0.5,
    )
    monkeypatch.setattr(matcher, "_embed_texts", lambda texts: [[0.9, 0.1]])

    results = matcher.match_query("查 query_a", top_k=2)

    assert len(results) == 1
    assert results[0]["query_name"] == "query_a"
    assert results[0]["cypher"] == "A"
    assert results[0]["similarity"] >= matcher.similarity_threshold


def test_vector_query_matcher_parameter_extraction_and_factory(monkeypatch) -> None:
    monkeypatch.setattr(predefined_utils._VectorQueryMatcher, "_embed_texts", lambda self, texts: [[1.0, 0.0]])
    matcher = predefined_utils.create_vector_query_matcher({"product_price_query": "MATCH (p) WHERE p.ProductName = $product_name"})

    assert matcher.query_descriptions == {"product_price_query": "product price query"}
    assert matcher.extract_parameters("查询 小米门锁 的价格", "product_price_query") == {
        "product_name": "小米门锁"
    }

    llm = FakeLLM('说明 {"product_name":"智能门锁Pro"} 尾部')
    assert matcher.extract_parameters(
        "这个产品多少钱",
        "product_price_query",
        llm=llm,
    ) == {"product_name": "智能门锁Pro"}


def test_compute_query_vectors_uses_query_text_order(monkeypatch) -> None:
    monkeypatch.setattr(
        predefined_utils._VectorQueryMatcher,
        "_embed_texts",
        lambda self, texts: [[float(index), float(index + 1)] for index, _ in enumerate(texts)],
    )
    matcher = predefined_utils._VectorQueryMatcher(
        predefined_cypher_dict={"query_a": "A", "query_b": "B"},
        query_descriptions={"query_a": "desc a", "query_b": "desc b"},
    )

    assert np.allclose(matcher.query_vectors["query_a"], np.array([0.0, 1.0]))
    assert np.allclose(matcher.query_vectors["query_b"], np.array([1.0, 2.0]))
