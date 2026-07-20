# 文档解析与切分模块

将 **Markdown / PDF / Word(DOCX)** 解析为适合向量数据库入库的 `DocumentChunk` 列表。

统一中间格式为 Markdown：PDF 与 DOCX 先转换，原生 `.md` / `.markdown` 直接读取后进入同一套清洗与分块。

## 功能

- Markdown（`.md` / `.markdown`）直读 UTF-8（兼容常见中文编码）
- PDF 使用 Docling 解析（版面分析、阅读顺序、标题/表格/代码/图片识别）
- DOCX 优先使用 Docling，失败后用 python-docx fallback
- 图片/图表可通过外部 VLM API 生成中文描述
- 按多级标题切分章节
- 按内容类型（文本/表格/代码/图片说明）细分
- 文本按分隔符优先级递归切分，支持 overlap
- 长表格按行拆分并重复表头
- 长代码块按函数/类边界拆分
- 输出 `raw_text`（干净原文）和 `embedding_text`（带标题路径）双字段

## 检索子模块

`retrieval/` 内置混合检索引擎：

- **Milvus 向量检索**：基于 embedding 的语义相似度搜索
- **BM25 关键词检索**
- **RRF 融合**：Reciprocal Rank Fusion 合并两路排序结果

## 使用方式

本模块已并入应用内部，作为库接口使用，**不再提供独立 CLI**：

```python
from app.knowledge.infrastructure.doc_parser.pipeline import parse_document

chunks = parse_document("/path/to/file.pdf")
# 或
chunks = parse_document("/path/to/notes.md")
```

业务上传链路请走：

```python
from app.knowledge.application.indexing_service import IndexingService
```

允许扩展名：`.md` / `.markdown` / `.pdf` / `.docx`。

## 主要入口

- `pipeline.py` — 解析与切分总入口
- `parsers/` — Markdown / PDF / DOCX 解析器
- `splitters/` — 文本 / 表格 / 代码切分
- `markdown/` — Markdown 结构解析
- `retrieval/` — 混合检索与向量写入

## 环境变量

```bash
# VLM 图片描述 API（OpenAI 兼容接口，可选）
export VLM_API_KEY="your-api-key"
export VLM_API_BASE_URL="https://your-openai-compatible-endpoint/v1/chat/completions"
export VLM_MODEL="your-vlm-model"
```

未设置 VLM 环境变量时，图片描述功能会自动降级关闭。API Key 必须通过环境变量配置，不可硬编码。
