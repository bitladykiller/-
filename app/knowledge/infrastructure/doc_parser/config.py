"""
RAG 文档解析与切分模块 — 配置管理。

定义 ParserConfig，支持从环境变量和默认值初始化。
支持参数校验。
"""

from __future__ import annotations

import os
from dataclasses import dataclass


def _env_or(key: str, default: str | None = None) -> str | None:
    """从环境变量读取配置，未设置返回 default。"""
    return os.environ.get(key, default)


@dataclass
class ParserConfig:
    """RAG 文档解析器配置。

    通过 from_env() 类方法从环境变量读取 VLM 相关配置。
    其他字段使用合理的默认值。
    """

    # ------------------------------------------------------------------ #
    # 切分配置
    # ------------------------------------------------------------------ #
    text_chunk_size: int = 700
    text_chunk_overlap: int = 100
    max_table_rows_per_chunk: int = 50
    max_code_lines_per_chunk: int = 120

    # ------------------------------------------------------------------ #
    # 功能开关
    # ------------------------------------------------------------------ #
    enable_markdown_cleaning: bool = True
    enable_table_split: bool = True
    enable_code_split: bool = True

    # ------------------------------------------------------------------ #
    # Docling PDF 配置
    # ------------------------------------------------------------------ #
    docling_generate_picture_images: bool = True
    docling_images_scale: float = 2.0
    docling_do_picture_description: bool = True
    docling_do_picture_classification: bool = True
    docling_enable_remote_services: bool = True

    # ------------------------------------------------------------------ #
    # VLM API 配置（用于图片描述）
    # ------------------------------------------------------------------ #
    vlm_api_base_url: str | None = None
    vlm_api_key_env: str = "VLM_API_KEY"
    vlm_model: str | None = None
    vlm_timeout: int = 90
    vlm_max_tokens: int = 500
    vlm_temperature: float = 0.0

    # ------------------------------------------------------------------ #
    # 图片描述 Prompt
    # ------------------------------------------------------------------ #
    picture_description_prompt: str = (
        "请用中文准确描述这张文档图片。要求：\n"
        "1. 如果是图表，请说明图表类型、坐标轴、图例、关键数值、趋势和结论。\n"
        "2. 如果是流程图，请按步骤说明流程。\n"
        "3. 如果是结构图，请说明核心模块和连接关系。\n"
        "4. 如果包含公式，请尽量保留公式内容。\n"
        "5. 不要编造图片中不存在的信息。\n"
        "6. 描述控制在 3 到 6 句话。"
    )

    def __post_init__(self):
        """构造后参数校验。"""
        if self.text_chunk_size <= 0:
            raise ValueError("text_chunk_size 必须 > 0")
        if not (0 <= self.text_chunk_overlap < self.text_chunk_size):
            raise ValueError(
                "text_chunk_overlap 必须满足 0 <= overlap < chunk_size"
            )
        if self.max_table_rows_per_chunk <= 0:
            raise ValueError("max_table_rows_per_chunk 必须 > 0")
        if self.max_code_lines_per_chunk <= 0:
            raise ValueError("max_code_lines_per_chunk 必须 > 0")
        if self.docling_images_scale <= 0:
            raise ValueError("docling_images_scale 必须 > 0")
        if self.vlm_timeout <= 0:
            raise ValueError("vlm_timeout 必须 > 0")

    @classmethod
    def from_env(cls) -> ParserConfig:
        """从环境变量创建配置实例。

        读取：
        - VLM_API_BASE_URL → vlm_api_base_url
        - VLM_API_KEY（环境变量名由 vlm_api_key_env 决定，默认 "VLM_API_KEY"）
        - VLM_MODEL → vlm_model

        其他字段使用 dataclass 默认值。
        """
        return cls(
            vlm_api_base_url=_env_or("VLM_API_BASE_URL"),
            vlm_model=_env_or("VLM_MODEL"),
        )
