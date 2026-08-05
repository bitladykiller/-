import app.chat.infrastructure.kg.northwind_retriever as northwind_retriever


def test_get_examples_flattens_categories_and_formats_output(monkeypatch) -> None:
    monkeypatch.setattr(
        northwind_retriever,
        "_EXAMPLES_BY_CATEGORY",
        {
            "产品查询": [
                {"question": "查询 产品 库存", "cypher": "MATCH product-stock"},
            ],
            "订单查询": [
                {"question": "查询 订单 明细", "cypher": "MATCH order-detail"},
            ],
        },
    )
    monkeypatch.setattr(northwind_retriever, "_IMPORTANT_PATTERNS", [])

    retriever = northwind_retriever.NorthwindCypherRetriever()
    output = retriever.get_examples("查询 产品 库存", k=2)

    assert output == (
        "Question: 查询 产品 库存\nCypher: MATCH product-stock\n\n"
        "Question: 查询 订单 明细\nCypher: MATCH order-detail"
    )


def test_get_examples_respects_top_k(monkeypatch) -> None:
    monkeypatch.setattr(
        northwind_retriever,
        "_EXAMPLES_BY_CATEGORY",
        {
            "分类A": [
                {"question": "产品 价格", "cypher": "CYPHER A"},
                {"question": "产品 库存", "cypher": "CYPHER B"},
                {"question": "订单 状态", "cypher": "CYPHER C"},
            ],
        },
    )
    monkeypatch.setattr(northwind_retriever, "_IMPORTANT_PATTERNS", [])

    retriever = northwind_retriever.NorthwindCypherRetriever()
    output = retriever.get_examples("产品 库存", k=2)

    assert output.count("Question: ") == 2
    assert "CYPHER B" in output
