"""
Cypher 生成策略 — 策略模式显式化。

v3.16: 原实现中模板匹配 vs LLM 生成的选择逻辑隐式嵌入在 LangGraph 图拓扑中。
重构为显式策略类，每个策略实现统一的 CypherStrategy 接口，
策略优先级在注册时决定，新增策略无需修改图结构。

设计模式：Strategy（策略模式）
选择原因：Cypher 生成有两种不同方式（模板匹配 vs LLM 生成），
它们的输入/输出相同但内部实现完全不同，天然适合策略模式。
优点：新增策略只需实现接口并注册，不修改现有代码。
缺点：增加了一层抽象，在只有两种策略时性价比有限，
但如果未来需要加入缓存命中策略或 RAG+LLM 混合策略，此设计就体现价值。
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any, Dict, List, Optional


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
        self, task: str, **kwargs
    ) -> Dict[str, Any]:
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

    将 28 个预定义模板编码为向量，用户问题通过 bge-m3 embedding 匹配最相似模板。
    相似度 > 阈值时直接执行模板中的 Cypher，必要时用 LLM 提取参数替换占位符。

    延迟：~50-100ms（embedding + 向量搜索 + 参数提取）
    准确率：高（仅适用于已覆盖的常见查询场景）
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

    async def generate(self, task: str, **kwargs) -> Dict[str, Any]:
        """尝试匹配预定义模板。

        Returns:
            命中时：{"statement": cypher, "records": [...], "steps": ["predefined_match"],
                      "next_action_cypher": "execute_cypher"}
            未命中时：{"task": task, "steps": ["predefined_match"],
                       "next_action_cypher": "generate"}
        """
        matches = self._matcher.match_query(task, top_k=1)
        if matches and matches[0]["similarity"] > self._threshold:
            cypher = matches[0]["cypher"]
            try:
                params = self._matcher.extract_parameters(
                    task, matches[0]["query_name"], llm=self._llm,
                )
                records = self._graph.query(
                    cypher, params={k: str(v) for k, v in params.items()}
                )
            except Exception:
                records = []
            return {
                "statement": cypher,
                "records": records,
                "steps": ["predefined_match"],
                "next_action_cypher": "execute_cypher",
            }
        return {
            "task": task,
            "steps": ["predefined_match"],
            "next_action_cypher": "generate",
        }

    def can_handle(self, task: str) -> bool:
        """总是返回 True — 模板匹配速度快，优先尝试。"""
        return True


# ================================================================== #
# 策略 2：LLM 生成 + 5 层验证（弹性路径，~600ms）
# ================================================================== #

class LLMGenerationStrategy(CypherStrategy):
    """基于 LLM 的 Cypher 生成策略 + 多层验证。

    在模板匹配未命中时使用。流程：
    1. LLM 根据 schema + examples 生成 Cypher
    2. 5 层验证（语法/写保护/关系方向/语义/模式）
    3. 如有错误且未达最大尝试次数，LLM 修正后重新验证
    4. 通过验证后执行

    延迟：~500-800ms（LLM 生成 + 验证 + 可能的修正循环）
    准确率：高（覆盖所有场景，多层验证保障正确性）
    """

    name = "llm_generation"

    def __init__(
        self,
        generate_node,
        validate_node,
        correct_node,
        execute_node,
    ):
        """注入 LLM 生成链路的各节点。

        Args:
            generate_node: Text2Cypher 生成节点。
            validate_node: 多层验证节点。
            correct_node: 错误修正节点。
            execute_node: Cypher 执行节点。
        """
        self._generate = generate_node
        self._validate = validate_node
        self._correct = correct_node
        self._execute = execute_node

    async def generate(self, task: str, **kwargs) -> Dict[str, Any]:
        """通过 LLM 生成 Cypher 查询。

        由于 LLM 生成路径涉及多轮验证和修正循环，
        此方法主要用于 LangGraph 图中的后继处理。
        在实际使用中，此策略的各节点由 LangGraph 编排。
        """
        return await self._generate({"task": task})


# ================================================================== #
# 策略注册表
# ================================================================== #

class CypherStrategyRegistry:
    """Cypher 策略注册表 — 按优先级管理多个策略。

    使用方式：
      registry = CypherStrategyRegistry()
      registry.register(PredefinedTemplateStrategy(...), priority=0)
      registry.register(LLMGenerationStrategy(...), priority=1)
      result = await registry.execute(task)
    """

    def __init__(self):
        self._strategies: List[tuple[int, CypherStrategy]] = []

    def register(self, strategy: CypherStrategy, priority: int = 0):
        """注册一个策略。priority 越小越优先。"""
        self._strategies.append((priority, strategy))
        self._strategies.sort(key=lambda x: x[0])

    async def execute(self, task: str) -> Dict[str, Any]:
        """按优先级依次尝试策略，返回第一个命中的结果。

        如果所有策略都未命中，返回最后一个策略的未命中状态。
        """
        last_result = {}
        for _, strategy in self._strategies:
            if strategy.can_handle(task):
                result = await strategy.generate(task)
                last_result = result
                # 如果策略返回了 records（非 None），说明命中
                if result.get("records") is not None:
                    return result
        return last_result
