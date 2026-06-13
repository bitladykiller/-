"""Text2Cypher 工具 schema。

这个模块负责：
- 定义给工具选择 / Tool Calling 场景使用的最小输入 schema
- 暴露稳定的 schema 入口，避免调用方直接依赖具体类名

注意：
- `text2cypher` 保持小写命名，是为了和外部工具名保持一致，
  不在这里强行改成常规类名，避免工具契约漂移。
"""

from pydantic import BaseModel, Field


class text2cypher(BaseModel):
    """默认的 Cypher 检索工具输入。"""

    task: str = Field(..., description="The task the Cypher query must answer.")


def get_text2cypher_schema() -> type[text2cypher]:
    """返回稳定的工具 schema 入口。"""
    return text2cypher
