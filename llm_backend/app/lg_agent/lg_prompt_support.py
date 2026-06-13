"""`lg_prompts.py` 共享的 Prompt 加载 helper。

职责：
- 承接 Prompt YAML 路径解析
- 承接 YAML 数据读取、override 过滤和默认值合并
- 统一处理 Prompt 加载失败时的降级行为

边界：
- 不定义具体 Prompt 文本常量
- 不定义公开 Prompt 常量名
- 不参与 LangGraph 节点编排
"""

from __future__ import annotations

from pathlib import Path
from typing import Protocol, TypeAlias

PromptMapping: TypeAlias = dict[str, str]


class PromptLogger(Protocol):
    """Prompt 加载 helper 需要的最小日志接口。"""

    def info(self, msg: str, *args: object, **kwargs: object) -> object: ...

    def warning(self, msg: str, *args: object, **kwargs: object) -> object: ...


def prompt_yaml_path(module_file: str | Path) -> Path:
    """根据模块文件路径返回默认 Prompt YAML 路径。"""
    return Path(module_file).parent / "lg_prompts.yaml"


def normalize_prompt_overrides(data: object) -> PromptMapping:
    """过滤 YAML 结果中的非字符串键值，收口成稳定 prompt 映射。"""
    if not isinstance(data, dict):
        return {}

    prompt_overrides: PromptMapping = {}
    for key, value in data.items():
        if isinstance(key, str) and isinstance(value, str):
            prompt_overrides[key] = value
    return prompt_overrides


def load_yaml_prompt_data(yaml_path: Path) -> object:
    """从指定 YAML 路径读取原始 Prompt 数据。"""
    import yaml

    with yaml_path.open("r", encoding="utf-8") as prompt_file:
        return yaml.safe_load(prompt_file)


def load_prompts_from_yaml(
    logger: PromptLogger,
    yaml_path: Path,
) -> PromptMapping:
    """从指定 YAML 文件加载 Prompt 覆盖值。"""
    if not yaml_path.exists():
        logger.info("lg_prompts.yaml 不存在，使用内置默认 Prompt")
        return {}

    try:
        data = load_yaml_prompt_data(yaml_path)
        if data is None:
            logger.info("lg_prompts.yaml 为空，使用内置默认 Prompt")
            return {}
        if not isinstance(data, dict):
            logger.warning("lg_prompts.yaml 格式错误，使用内置默认 Prompt")
            return {}

        prompt_overrides = normalize_prompt_overrides(data)
        logger.info("已从 lg_prompts.yaml 加载 Prompt 模板")
        return prompt_overrides
    except Exception:
        logger.warning("lg_prompts.yaml 加载失败，使用内置默认 Prompt", exc_info=True)
        return {}


def build_prompt_mapping(
    default_prompts: PromptMapping,
    prompt_overrides: PromptMapping,
) -> PromptMapping:
    """把 YAML 覆盖值和默认 Prompt 合并成稳定映射。"""
    return {**default_prompts, **prompt_overrides}


__all__ = [
    "PromptMapping",
    "build_prompt_mapping",
    "load_prompts_from_yaml",
    "load_yaml_prompt_data",
    "normalize_prompt_overrides",
    "prompt_yaml_path",
]
