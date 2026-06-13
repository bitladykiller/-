"""Cypher 快速路径策略。

这个模块负责：
- 封装预定义模板命中后的快速执行路径
- 给 Text2Cypher 图提供统一的策略调用接口

这个模块不负责：
- LLM 生成 / 校验 / 修正链路
- LangGraph 图组装
- Neo4j schema 管理
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any


class CypherStrategy(ABC):
    """Cypher 生成策略抽象基类。

    所有 Cypher 生成方式（模板匹配、LLM 生成、缓存命中...）
    都实现此接口。Agent 只依赖接口，不关心具体实现。
    """

    @property
    @abstractmethod
    def name(self) -> str:
        """策略名称，用于日志和调试。"""
        ...

    @abstractmethod
    async def generate(
        self,
        task: str,
        **kwargs: Any,
    ) -> dict[str, Any]:
        """执行策略，返回 Cypher 查询结果或状态。

        Args:
            task: 用户的自然语言查询问题。
            **kwargs: 策略特定的额外参数。

        Returns:
            字典，必须包含：
            - 如果命中：{"records": [...], "statement": "...", "steps": ["..."]}
            - 如果未命中：{"task": "...", "steps": ["..."], "next_action_cypher": "..."}
              其中 next_action_cypher 指示下一步走哪个策略。
        """
        ...

    def can_handle(self, task: str) -> bool:
        """判断此策略是否能处理该任务。默认总返回 True。"""
        return True


# ================================================================== #
# 策略 1：预定义模板匹配（快速路径，<100ms）
# ================================================================== #

class PredefinedTemplateStrategy(CypherStrategy):
    """基于语义向量匹配的预定义 Cypher 模板策略。

    WHY：
    已覆盖的高频查询不需要走完整的 LLM 生成链路。
    先尝试模板匹配可以把常见问题收敛到更稳定、更低延迟的路径。
    """

    name = "predefined_template"

    def __init__(
        self,
        matcher,
        graph,
        llm,
        similarity_threshold: float = 0.6,
    ):
        """初始化模板策略。

        Args:
            matcher: create_vector_query_matcher 返回的匹配器实例。
            graph: Neo4jGraph 实例。
            llm: 用于参数提取的 LLM 实例。
            similarity_threshold: 相似度阈值，高于此值视为命中。
        """
        self._matcher = matcher
        self._graph = graph
        self._llm = llm
        self._threshold = similarity_threshold

    def _build_fallback_result(self, task: str) -> dict[str, Any]:
        """返回模板未命中时的统一状态。"""
        return {
            "task": task,
            "steps": ["predefined_match"],
            "next_action_cypher": "generate",
        }

    def _extract_records(self, task: str, match: dict[str, Any]) -> list[Any]:
        """命中模板后提取参数并执行 Cypher。失败时返回空记录。"""
        cypher = match["cypher"]
        try:
            params = self._matcher.extract_parameters(
                task,
                match["query_name"],
                llm=self._llm,
            )
            return self._graph.query(
                cypher,
                params={key: str(value) for key, value in params.items()},
            )
        except Exception:
            return []

    async def generate(
        self,
        task: str,
        **kwargs: Any,
    ) -> dict[str, Any]:
        """尝试匹配预定义模板。

        Returns:
            命中时：{"statement": cypher, "records": [...], "steps": ["predefined_match"],
                      "next_action_cypher": "execute_cypher"}
            未命中时：{"task": task, "steps": ["predefined_match"],
                       "next_action_cypher": "generate"}
        """
        matches = self._matcher.match_query(task, top_k=1)
        if not matches or matches[0]["similarity"] <= self._threshold:
            return self._build_fallback_result(task)

        best_match = matches[0]
        return {
            "statement": best_match["cypher"],
            "records": self._extract_records(task, best_match),
            "steps": ["predefined_match"],
            "next_action_cypher": "execute_cypher",
        }

    def can_handle(self, task: str) -> bool:
        """总是返回 True — 模板匹配速度快，优先尝试。"""
        return True
