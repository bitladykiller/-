"""Northwind Cypher 示例检索器。

这个模块只做一件事：根据用户问题，从预置 Cypher 示例中挑出最相关的几条。
示例数据和相关性打分规则都留在同一处，方便直接阅读和调整。
"""
from __future__ import annotations

import re
from itertools import chain
from typing import TypedDict


class CypherExample(TypedDict):
    """单条 Cypher 示例的数据结构。"""

    question: str
    cypher: str


_IMPORTANT_PATTERNS: list[tuple[str, str]] = [
    (r"产品|商品", "产品"),
    (r"类别|分类", "类别"),
    (r"供应商", "供应商"),
    (r"订单", "订单"),
    (r"客户", "客户"),
    (r"员工", "员工"),
    (r"物流|配送", "物流"),
    (r"评价|评论", "评价"),
    (r"手册|说明书", "手册"),
]

_EXAMPLES_BY_CATEGORY: dict[str, list[CypherExample]] = {
    "产品查询": [
        {
            "question": "查询所有智能音箱类产品",
            "cypher": """MATCH (p:Product)-[:BELONGS_TO]->(c:Category)
    WHERE c.CategoryName = '智能音箱'
    RETURN p.ProductName, p.UnitPrice, p.UnitsInStock""",
        },
        {
            "question": "查找库存少于20的产品",
            "cypher": """MATCH (p:Product)
    WHERE p.UnitsInStock < 20
    RETURN p.ProductName, p.UnitsInStock
    ORDER BY p.UnitsInStock""",
        },
        {
            "question": "哪些产品的单价高于5000元？",
            "cypher": """MATCH (p:Product)
    WHERE p.UnitPrice > 5000
    RETURN p.ProductName, p.UnitPrice
    ORDER BY p.UnitPrice DESC""",
        },
    ],
    "产品类别": [
        {
            "question": "智能家居有哪些产品类别？",
            "cypher": """MATCH (c:Category)
    RETURN c.CategoryName, c.Description""",
        },
        {
            "question": "智能灯具类别下有哪些产品？",
            "cypher": """MATCH (p:Product)-[:BELONGS_TO]->(c:Category)
    WHERE c.CategoryName = '智能灯具'
    RETURN p.ProductName, p.UnitPrice""",
        },
    ],
    "供应商相关": [
        {
            "question": "供应商小米智能家居提供了哪些产品？",
            "cypher": """MATCH (p:Product)-[:SUPPLIED_BY]->(s:Supplier)
    WHERE s.CompanyName = '小米智能家居'
    RETURN p.ProductName, p.QuantityPerUnit, p.UnitPrice""",
        },
        {
            "question": "中国供应商提供了哪些产品？",
            "cypher": """MATCH (p:Product)-[:SUPPLIED_BY]->(s:Supplier)
    WHERE s.Country = '中国'
    RETURN s.CompanyName, p.ProductName, p.UnitPrice""",
        },
    ],
    "订单查询": [
        {
            "question": "订单1001包含哪些产品？",
            "cypher": """MATCH (o:Order)-[:CONTAINS]->(p:Product)
    WHERE o.OrderID = 1001
    RETURN p.ProductName, p.UnitPrice, o.OrderDate""",
        },
        {
            "question": "谁处理了订单1001？",
            "cypher": """MATCH (o:Order)<-[:PROCESSED]-(e:Employee)
    WHERE o.OrderID = 1001
    RETURN e.FirstName, e.LastName, e.Title""",
        },
        {
            "question": "客户AB123下了哪些订单？",
            "cypher": """MATCH (o:Order)<-[:PLACED]-(c:Customer)
    WHERE c.CustomerID = 'AB123'
    RETURN o.OrderID, o.OrderDate, o.ShippedDate
    ORDER BY o.OrderDate DESC""",
        },
    ],
    "员工查询": [
        {
            "question": "李明处理了哪些订单？",
            "cypher": """MATCH (o:Order)<-[:PROCESSED]-(e:Employee)
    WHERE e.FirstName = '明' AND e.LastName = '李'
    RETURN o.OrderID, o.OrderDate, o.ShippedDate
    ORDER BY o.OrderDate DESC""",
        },
        {
            "question": "谁是张伟的下属？",
            "cypher": """MATCH (e1:Employee)-[:REPORTS_TO]->(e2:Employee)
    WHERE e2.FirstName = '伟' AND e2.LastName = '张'
    RETURN e1.FirstName, e1.LastName, e1.Title""",
        },
    ],
    "物流查询": [
        {
            "question": "订单1001是通过哪个物流公司配送的？",
            "cypher": """MATCH (o:Order)-[:SHIPPED_VIA]->(s:Shipper)
    WHERE o.OrderID = 1001
    RETURN s.CompanyName, s.Phone, o.ShippedDate""",
        },
        {
            "question": "顺丰速运负责配送了哪些订单？",
            "cypher": """MATCH (o:Order)-[:SHIPPED_VIA]->(s:Shipper)
    WHERE s.CompanyName = '顺丰速运'
    RETURN o.OrderID, o.ShipName, o.ShipAddress, o.ShipCity, o.ShippedDate
    LIMIT 10""",
        },
    ],
    "客户查询": [
        {
            "question": "哪些客户来自北京？",
            "cypher": """MATCH (c:Customer)
    WHERE c.City = '北京'
    RETURN c.CompanyName, c.ContactName, c.Phone""",
        },
        {
            "question": "客户科技创新公司的订单都配送到哪里？",
            "cypher": """MATCH (o:Order)<-[:PLACED]-(c:Customer)
    WHERE c.CompanyName = '科技创新公司'
    RETURN o.OrderID, o.ShipAddress, o.ShipCity, o.ShipCountry""",
        },
    ],
    "复杂查询": [
        {
            "question": "销售最多的智能家居产品是什么？",
            "cypher": """MATCH (o:Order)-[rel:CONTAINS]->(p:Product)
    WITH p.ProductName AS product, SUM(rel.Quantity) AS total_quantity
    RETURN product, total_quantity
    ORDER BY total_quantity DESC
    LIMIT 5""",
        },
        {
            "question": "订单1001中的产品分别由哪些供应商提供？",
            "cypher": """MATCH (o:Order)-[:CONTAINS]->(p:Product)-[:SUPPLIED_BY]->(s:Supplier)
    WHERE o.OrderID = 1001
    RETURN p.ProductName, s.CompanyName, s.ContactName, s.Phone""",
        },
        {
            "question": "王强处理的订单中包含了哪些智能音箱类产品？",
            "cypher": """MATCH (e:Employee)<-[:PROCESSED]-(o:Order)-[:CONTAINS]->(p:Product)-[:BELONGS_TO]->(c:Category)
    WHERE e.LastName = '王' AND e.FirstName = '强' AND c.CategoryName = '智能音箱'
    RETURN DISTINCT p.ProductName, p.UnitPrice, o.OrderID
    ORDER BY p.ProductName""",
        },
    ],
    "产品评价和使用说明": [
        {
            "question": "查询产品小米智能音箱Pro的评价",
            "cypher": """MATCH (p:Product)<-[:ABOUT]-(r:Review)
    WHERE p.ProductName = '小米 智能音箱 Pro'
    RETURN r.ReviewText, r.Rating, r.ReviewDate
    ORDER BY r.ReviewDate DESC""",
        },
        {
            "question": "哪些智能门锁产品的评价超过4.5分？",
            "cypher": """MATCH (p:Product)-[:BELONGS_TO]->(c:Category), (p)<-[:ABOUT]-(r:Review)
    WHERE c.CategoryName = '智能门锁' AND r.Rating > 4.5
    RETURN p.ProductName, AVG(r.Rating) AS 平均评分, COUNT(r) AS 评价数量
    ORDER BY 平均评分 DESC""",
        },
    ],
    "订单统计": [
        {
            "question": "每个月的订单数量统计",
            "cypher": """MATCH (o:Order)
    WITH SUBSTRING(o.OrderDate, 0, 7) AS month, COUNT(o) AS order_count
    RETURN month AS 月份, order_count AS 订单数量
    ORDER BY 月份""",
        },
        {
            "question": "每个类别产品的销售金额",
            "cypher": """MATCH (o:Order)-[rel:CONTAINS]->(p:Product)-[:BELONGS_TO]->(c:Category)
    WITH c.CategoryName AS category, SUM(rel.UnitPrice * rel.Quantity * (1-rel.Discount)) AS total_sales
    RETURN category AS 类别, total_sales AS 销售总额
    ORDER BY 销售总额 DESC""",
        },
    ],
    "地理分析": [
        {
            "question": "各城市的客户数量统计",
            "cypher": """MATCH (c:Customer)
    WITH c.City AS city, COUNT(c) AS customer_count
    RETURN city AS 城市, customer_count AS 客户数
    ORDER BY 客户数 DESC
    LIMIT 10""",
        },
        {
            "question": "查找每个省份的订单数和销售额",
            "cypher": """MATCH (c:Customer)-[:PLACED]->(o:Order)-[rel:CONTAINS]->(p:Product)
    WITH c.Region AS province, COUNT(DISTINCT o) AS order_count,
        SUM(rel.UnitPrice * rel.Quantity * (1-rel.Discount)) AS sales
    RETURN province AS 省份, order_count AS 订单数, sales AS 销售额
    ORDER BY 销售额 DESC""",
        },
    ],
}


class NorthwindCypherRetriever:
    """基于 Northwind 示例集的 Cypher 检索器。"""

    def get_examples(self, query: str, k: int = 5) -> str:
        """根据用户查询返回相关的 Cypher 示例。"""
        examples = list(chain.from_iterable(_EXAMPLES_BY_CATEGORY.values()))
        query_words = set(re.findall(r"\w+", query.lower()))
        scored_examples: list[tuple[CypherExample, int]] = []

        for example in examples:
            score = 0
            example_words = set(re.findall(r"\w+", example["question"].lower()))

            overlap = len(query_words.intersection(example_words))
            if overlap > 0:
                score += overlap * 2

            for pattern, category in _IMPORTANT_PATTERNS:
                if re.search(pattern, query):
                    if re.search(pattern, example["question"]):
                        score += 3
                    for cat, cat_examples in _EXAMPLES_BY_CATEGORY.items():
                        if category in cat and example in cat_examples:
                            score += 2

            scored_examples.append((example, score))

        scored_examples.sort(key=lambda item: item[1], reverse=True)
        selected_examples = [example for example, _ in scored_examples[:k]]
        return "\n\n".join(
            f"Question: {example['question']}\nCypher: {example['cypher']}"
            for example in selected_examples
        )
