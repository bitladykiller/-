"""
RAG 文档解析与切分模块 — 配置管理。

定义 ParserConfig，并对配置参数做基础校验。
支持参数校验。
"""

from dataclasses import dataclass


@dataclass
class ParserConfig:
    """RAG 文档解析器配置。

    这里只保留仓库内确实存在覆写需求的切分与 VLM 连接参数。
    """

    # ------------------------------------------------------------------ #
    # 切分配置
    # ------------------------------------------------------------------ #
    text_chunk_size: int = 700
    text_chunk_overlap: int = 100
    max_table_rows_per_chunk: int = 50
    max_code_lines_per_chunk: int = 120

    # ------------------------------------------------------------------ #
    # VLM API 配置（用于图片描述）
    # ------------------------------------------------------------------ #
    vlm_api_base_url: str | None = None
    vlm_model: str | None = None

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
