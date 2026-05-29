from pydantic import BaseModel
from pydantic import Field


class cypher_query(BaseModel):
    """如果用户问的是关于产品价格、库存、规格等，则使用这个工具，生成Cypher查询语句进行查询"""

    task: str = Field(..., description="The task the Cypher query must answer.")

class predefined_cypher(BaseModel):
    """这个工具包含预定义的Cypher查询语句，用于快速响应各种电商场景的查询需求。
    
    根据用户问题的类型，可以选择以下类别的查询：
    ...
    请根据用户的问题选择最合适的查询，并根据需要替换查询中的参数值。
    """

    query: str = Field(..., description="query the graph must include the question")
    parameters: dict = Field(..., description="parameters for the query to Neo4j")

class rag_document_query(BaseModel):
    """如果用户问的问题是关于产品的故障、售后、保修、维修、退换货以及评价等，需要查询文档知识库，则使用这个工具。
    
    该工具会通过向量检索 + BM25 + RRF 融合 + Reranker 从文档知识库中检索相关片段。"""
    query: str = Field(..., description="query for the document knowledge base")


class real_time_network_query(BaseModel):
    """如果用户问的问题是关于一些实时的产品有效信息需要联网检索的话，则使用这个工具"""
    query: str = Field(..., description="query the network must include the question")


