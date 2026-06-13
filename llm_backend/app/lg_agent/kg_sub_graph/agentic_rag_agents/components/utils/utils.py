"""KG 子图通用文本处理工具。

这个模块负责：
- 提取 Neo4j schema 文本
- 删除对 Prompt 无帮助的内部结构段落
- 规整特殊字符，降低模板注入时的格式冲突

这个模块不负责：
- Cypher 生成
- schema 持久化
- 图数据库连接管理
"""

import regex as re
from langchain_neo4j import Neo4jGraph


# 这里保留一个小型局部 helper，避免再为了单个正则额外拆文件。
def _cypher_query_node_graph_schema() -> str:
    """匹配以 '- **CypherQuery**' 开始的段落，直到 Relationship properties 或下一节。"""
    return r"^(- \*\*CypherQuery\*\*[\s\S]+?)(^Relationship properties|- \*)"


def retrieve_and_parse_schema_from_graph_for_prompts(graph: Neo4jGraph) -> str:
    """提取并规整 Neo4j schema，供 Prompt 注入使用。

    这里的 schema 指 Neo4j 数据库的结构描述，包括：
    - 节点类型：如 Product, Category, Supplier 等
    - 节点属性：如 ProductName, UnitPrice, CategoryName 等
    - 关系类型：如 BELONGS_TO, SUPPLIED_BY, CONTAINS 等
    - 关系属性：关系上可能的属性（如有）

    提取出来的 schema 大致如下：
    Node properties:
        - **Product**: ProductID, ProductName, UnitPrice, UnitsInStock...
        - **Category**: CategoryID, CategoryName, Description...

    Relationship properties:
        - **BELONGS_TO**: 
        - **SUPPLIED_BY**: 

    WHY：
    1. 数据库结构变化时，上层 Prompt 不需要同步改硬编码 schema
    2. 给模型提供准确结构信息，可以减少错误字段和错误关系方向
    3. 清理掉无关片段后，Prompt 更短，也更聚焦
    """
    schema: str = graph.get_schema

    # 过滤掉对用户查询不相关的内部结构信息
    if "CypherQuery" in schema:
        schema = re.sub(
            _cypher_query_node_graph_schema(), r"\2", schema, flags=re.MULTILINE
        )

    # Schema 中可能含有 { }，会与 ChatPromptTemplate 的模板变量语法冲突，
    # 因此统一替换成方括号后再注入提示词。
    schema = schema.replace("{", "[").replace("}", "]")

    return schema
