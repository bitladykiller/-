"""
RAG 文档解析器 — Docling PDF 解析器。

使用 Docling 的 DocumentConverter 解析 PDF 文档。
支持 VLM（视觉语言模型）图片描述功能，兼容新旧两套 API。
"""

from __future__ import annotations

import logging
import os
import warnings

from app.knowledge.infrastructure.doc_parser.config import ParserConfig
from app.knowledge.infrastructure.doc_parser.exceptions import DoclingParseError
from app.knowledge.infrastructure.doc_parser.models import PageMarkdown, ParsedMarkdownDocument
from app.knowledge.infrastructure.doc_parser.parsers.base import BaseDocumentParser

logger = logging.getLogger(__name__)


class DoclingPDFParser(BaseDocumentParser):
    """基于 Docling 的 PDF 解析器。

    功能：
    - 使用 DocumentConverter 将 PDF 转为 Markdown。
    - 支持图片提取、图片分类、图片描述。
    - VLM API 密钥从环境变量读取，未配置时自动降级。
    - 兼容 Docling 新旧两套 VLM API 接口。

    Attributes:
        config: 解析器配置。
        parser_name: 解析器名称。
        _vlm_api_key: VLM API 密钥（从环境变量读取）。
        _remote_services_enabled: 是否启用远程服务。
    """

    def __init__(self, config: ParserConfig | None = None) -> None:
        """初始化 Docling PDF 解析器。

        检查 VLM API 密钥是否可用，决定是否启用远程服务。
        """
        super().__init__(config)
        self.parser_name = "DoclingPDFParser"

        # 从环境变量读取 VLM API 密钥
        self._vlm_api_key: str | None = os.environ.get(
            self.config.vlm_api_key_env
        )

        # 判断是否启用远程服务
        self._remote_services_enabled: bool = True
        if not self._vlm_api_key:
            logger.warning(
                "VLM API 密钥未配置（环境变量 %s），"
                "图片描述功能将被禁用，自动降级为无图片描述模式。",
                self.config.vlm_api_key_env,
            )
            self._remote_services_enabled = False

    def parse(self, file_path: str, doc_id: str) -> ParsedMarkdownDocument:
        """解析 PDF 文档为统一 Markdown 格式。

        流程：
        1. 校验文件。
        2. 构建 Docling DocumentConverter（含 PDF 管道选项）。
        3. 调用 convert 转换文档。
        4. 导出 Markdown。
        5. 提取图片标注信息，追加"图片说明"章节。
        6. 返回 ParsedMarkdownDocument。

        Args:
            file_path: PDF 文件路径。
            doc_id: 文档唯一标识。

        Returns:
            ParsedMarkdownDocument 实例。

        Raises:
            DoclingParseError: 解析失败时抛出。
        """
        self._validate_file(file_path, [".pdf"])
        logger.info("[%s] 开始解析 PDF: %s (doc_id=%s)", self.parser_name, file_path, doc_id)

        try:
            # 构建 Docling 转换器
            converter = self._build_converter()

            # 执行转换
            with warnings.catch_warnings():
                warnings.simplefilter("ignore")
                result = converter.convert(file_path)

            # 提取文档对象
            doc = result.document

            # 导出 Markdown
            markdown_text = doc.export_to_markdown()

            # 提取图片标注并追加
            picture_annotations = self._extract_picture_annotations(doc)
            if picture_annotations:
                markdown_text += "\n\n## 图片说明\n\n"
                markdown_text += "\n\n".join(picture_annotations)

            # 统计信息
            page_count = self._count_pages(doc)
            table_count = self._count_tables(doc)
            picture_count = len(picture_annotations)

            # 构建 metadata
            metadata = self._build_metadata(
                picture_count=picture_count,
                table_count=table_count,
                page_count=page_count,
                warnings=self._collect_warnings(),
            )

            # 构建 page_markdown_list（简化版：整篇文档作为一个 page）
            page_markdown_list = [
                PageMarkdown(
                    page_number=1,
                    markdown=markdown_text,
                    metadata={"source": "docling_pdf"},
                )
            ]

            logger.info(
                "[%s] PDF 解析完成: pages=%d, tables=%d, pictures=%d",
                self.parser_name, page_count, table_count, picture_count,
            )

            return ParsedMarkdownDocument(
                doc_id=doc_id,
                source_file=file_path,
                markdown=markdown_text,
                page_markdown_list=page_markdown_list,
                metadata=metadata,
            )

        except Exception as e:
            raise DoclingParseError(
                f"PDF 解析失败: {e}", file_path=file_path
            ) from e

    def _build_converter(self):
        """构建 Docling DocumentConverter，配置 PDF 管道选项。

        优先使用新 API，失败时降级到旧 API。

        Returns:
            DocumentConverter 实例。
        """
        from docling.datamodel.pipeline_options import PdfPipelineOptions
        from docling.document_converter import DocumentConverter, PdfFormatOption

        # 构建管道选项
        pipeline_options = PdfPipelineOptions()
        pipeline_options.generate_picture_images = self.config.docling_generate_picture_images
        pipeline_options.do_picture_classification = self.config.docling_do_picture_classification

        # 配置图片描述
        if self.config.docling_do_picture_description:
            self._configure_picture_description(pipeline_options)
        else:
            pipeline_options.do_picture_description = False

        # 构建转换器
        converter = DocumentConverter(
            format_options={
                "pdf": PdfFormatOption(pipeline_options=pipeline_options)
            }
        )

        return converter

    def _configure_picture_description(self, pipeline_options) -> None:
        """配置图片描述功能。

        尝试新 API（PictureDescriptionVlmEngineOptions），失败则降级到旧 API（PictureDescriptionApiOptions）。
        如果 VLM 密钥未配置，禁用图片描述。

        Args:
            pipeline_options: PdfPipelineOptions 实例。
        """
        if not self._remote_services_enabled:
            pipeline_options.do_picture_description = False
            logger.info("VLM 未配置，禁用图片描述。")
            return

        pipeline_options.do_picture_description = True

        # 尝试新 API
        try:
            from docling.datamodel.pipeline_options import (
                ApiVlmEngineOptions,
                PictureDescriptionVlmEngineOptions,
            )

            # 构建 VLM 引擎选项
            vlm_engine_options = ApiVlmEngineOptions(
                url=self.config.vlm_api_base_url or "",
                headers={"Authorization": f"Bearer {self._vlm_api_key}"},
                model=self.config.vlm_model or "",
                timeout=self.config.vlm_timeout,
                temperature=self.config.vlm_temperature,
            )

            # 构建图片描述选项
            picture_description_options = PictureDescriptionVlmEngineOptions(
                vlm_engine_options=vlm_engine_options,
                prompt=self.config.picture_description_prompt,
            )

            pipeline_options.picture_description_options = picture_description_options
            logger.info("已配置 VLM 图片描述（新 API）。")

        except (ImportError, AttributeError, TypeError) as e:
            logger.warning("新 VLM API 不可用 (%s)，尝试旧 API...", e)
            self._configure_picture_description_legacy(pipeline_options)

    def _configure_picture_description_legacy(self, pipeline_options) -> None:
        """使用旧版 API 配置图片描述。

        Args:
            pipeline_options: PdfPipelineOptions 实例。
        """
        try:
            from docling.datamodel.pipeline_options import PictureDescriptionApiOptions

            picture_description_options = PictureDescriptionApiOptions(
                url=self.config.vlm_api_base_url or "",
                headers={"Authorization": f"Bearer {self._vlm_api_key}"},
                prompt=self.config.picture_description_prompt,
                params={
                    "model": self.config.vlm_model or "",
                    "max_tokens": self.config.vlm_max_tokens,
                    "temperature": self.config.vlm_temperature,
                },
                timeout=self.config.vlm_timeout,
            )

            pipeline_options.picture_description_options = picture_description_options
            logger.info("已配置 VLM 图片描述（旧 API）。")

        except (ImportError, AttributeError, TypeError) as e:
            logger.error("旧 VLM API 也不可用 (%s)，禁用图片描述。", e)
            pipeline_options.do_picture_description = False

    def _extract_picture_annotations(self, doc) -> list[str]:
        """从 Docling 文档对象中提取图片标注（标题 + 描述）。

        遍历文档中的图片，提取 caption 和 description。

        Args:
            doc: Docling Document 对象。

        Returns:
            图片标注文本列表，每项为一段 Markdown 格式的图片说明。
        """
        annotations: list[str] = []

        try:
            # 尝试使用 iterate_items 遍历所有元素
            for item, _ in doc.iterate_items():
                item_type = getattr(item, "type", None) or getattr(item, "label", None)
                type_str = str(item_type).lower() if item_type else ""

                # 检查是否为图片类型
                if "picture" in type_str or "image" in type_str:
                    annotation = self._format_picture_annotation(item)
                    if annotation:
                        annotations.append(annotation)
        except (AttributeError, TypeError) as e:
            logger.debug("iterate_items 遍历失败 (%s)，尝试 pictures 属性...", e)
            # 降级：直接从 doc.pictures 获取
            annotations = self._extract_from_pictures_list(doc)

        # 如果 iterate_items 没找到图片，尝试 pictures 属性
        if not annotations:
            annotations = self._extract_from_pictures_list(doc)

        return annotations

    def _extract_from_pictures_list(self, doc) -> list[str]:
        """从 doc.pictures 列表提取图片标注。

        Args:
            doc: Docling Document 对象。

        Returns:
            图片标注文本列表。
        """
        annotations: list[str] = []
        pictures = getattr(doc, "pictures", None)
        if not pictures:
            return annotations

        for i, pic in enumerate(pictures, 1):
            annotation = self._format_picture_annotation(pic, index=i)
            if annotation:
                annotations.append(annotation)

        return annotations

    def _format_picture_annotation(self, item, index: int = 0) -> str | None:
        """格式化单个图片的标注信息。

        Args:
            item: Docling 图片元素。
            index: 图片序号（可选）。

        Returns:
            Markdown 格式的图片说明，无内容则返回 None。
        """
        title: str | None = None
        description: str | None = None
        classification: str | None = None

        # 提取 caption
        caption = getattr(item, "caption", None) or getattr(item, "text", None)
        if caption and str(caption).strip():
            title = str(caption).strip()
        elif index:
            title = f"图片 {index}"

        # 提取 description
        raw_description = getattr(item, "description", None)
        if raw_description and str(raw_description).strip():
            description = str(raw_description).strip()

        # 提取 classification
        raw_classification = getattr(item, "classification", None)
        if raw_classification and str(raw_classification).strip():
            classification = str(raw_classification).strip()

        if not title and not description and not classification:
            return None

        parts: list[str] = [":::image_caption"]
        if title:
            parts.append(f"title: {title}")
        if classification:
            parts.append(f"classification: {classification}")
        if description:
            parts.append("")
            parts.append(description)
        parts.append(":::")
        return "\n".join(parts)

    def _count_pages(self, doc) -> int:
        """统计文档页数。"""
        # 尝试从 pages 属性获取
        pages = getattr(doc, "pages", None)
        if pages is not None:
            try:
                return len(pages)
            except TypeError:
                pass
        return 0

    def _count_tables(self, doc) -> int:
        """统计文档中表格数量。"""
        count = 0
        try:
            for item, _ in doc.iterate_items():
                item_type = getattr(item, "type", None) or getattr(item, "label", None)
                type_str = str(item_type).lower() if item_type else ""
                if "table" in type_str:
                    count += 1
        except (AttributeError, TypeError):
            pass
        return count

    def _collect_warnings(self) -> list[str]:
        """收集当前解析状态的警告信息。"""
        warnings_list: list[str] = []
        if not self._remote_services_enabled:
            warnings_list.append(
                f"VLM API 密钥未配置（{self.config.vlm_api_key_env}），图片描述已禁用。"
            )
        return warnings_list
