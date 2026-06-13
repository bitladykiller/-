"""Agent 提示模板入口。

职责：
- 提供主图和 ReAct 链路使用的 Prompt 常量
- 优先从 `lg_prompts.yaml` 读取模板，保留硬编码默认值作为降级路径

边界：
- 这里只维护模板文本及其加载逻辑，不承载节点编排
"""
from __future__ import annotations

from app.core.logger import get_logger
from app.lg_agent.lg_prompt_defaults import DEFAULT_PROMPTS
from app.lg_agent.lg_prompt_support import (
    PromptMapping,
    build_prompt_mapping,
    load_prompts_from_yaml,
    prompt_yaml_path,
)

logger = get_logger(__name__)
_DEFAULT_PROMPTS: PromptMapping = DEFAULT_PROMPTS


# 模块级加载（import 时执行一次）
_prompt_mapping = build_prompt_mapping(
    _DEFAULT_PROMPTS,
    load_prompts_from_yaml(logger, prompt_yaml_path(__file__)),
)


def _get_prompt(prompt_key: str) -> str:
    """获取 Prompt：优先 YAML，缺失时降级为默认值。"""
    return _prompt_mapping[prompt_key]


# ================================================================== #
# 公开 Prompt 常量 — 外部模块使用时导入这些名称
# ================================================================== #

ROUTER_SYSTEM_PROMPT = _get_prompt("router_system")
RETRIEVAL_PLAN_ROUTER_PROMPT = _get_prompt("retrieval_plan_router")
GENERAL_QUERY_SYSTEM_PROMPT = _get_prompt("general_query")
GUARDRAILS_SYSTEM_PROMPT = _get_prompt("guardrails")
REACT_SYSTEM_PROMPT = _get_prompt("react_system")
REACT_ANSWER_CHECK_PROMPT = _get_prompt("react_answer_check")
