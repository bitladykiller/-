# RAG 文档解析与切分模块

将 PDF / DOCX 原始文件解析为适合向量数据库入库的 DocumentChunk 列表。

## 功能

- PDF 使用 Docling 解析（版面分析、阅读顺序、标题/表格/代码/图片识别）
- DOCX 优先使用 Docling，失败后用 python-docx fallback
- 图片/图表通过外部 VLM API 生成中文描述
- 按多级标题切分章节
- 按内容类型（文本/表格/代码/图片说明）细分
- 文本按分隔符优先级递归切分，支持 overlap
- 长表格按行拆分并重复表头
- 长代码块按行拆分并重新包裹
- 输出 `raw_text`（干净原文）和 `embedding_text`（带标题路径）双字段

## 安装

```bash
pip install -r requirements.txt
```

## 环境变量

```bash
# VLM 图片描述 API（OpenAI 兼容接口）
export VLM_API_KEY="your-api-key"
export VLM_API_BASE_URL="https://your-openai-compatible-endpoint/v1/chat/completions"
export VLM_MODEL="your-vlm-model"
```

如果不设置 VLM 环境变量，图片描述功能会自动降级关闭。

## 命令行使用

```bash
python -m rag_doc_parser.cli --file ./example.pdf --output ./chunks.json
python -m rag_doc_parser.cli --file ./report.docx --output ./chunks.json --no-picture-description
```

## Python 使用

```python
from rag_doc_parser.pipeline import parse_document

chunks = parse_document("./example.pdf")
for chunk in chunks:
    print(chunk.chunk_type, chunk.section_path, chunk.raw_text[:100])
```

## 说明

- 本项目只负责文档解析和 chunk 生成
- 不包含向量数据库写入和检索逻辑
- PDF 图片描述依赖外部 VLM API
- API Key 必须通过环境变量配置，不可硬编码
- DOCX 解析有 Docling → python-docx 两级 fallback
