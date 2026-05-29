# RAG 文档解析与切分模块

将 PDF / DOCX 等原始文件解析为适合向量数据库入库的 DocumentChunk 列表。

## 功能

- PDF 使用 Docling 解析（版面分析、阅读顺序、标题/表格/代码/图片识别）
- DOCX 优先使用 Docling，失败后用 python-docx fallback
- 图片/图表通过外部 VLM API 生成中文描述
- 按多级标题切分章节
- 按内容类型（文本/表格/代码/图片说明）细分
- 文本按分隔符优先级递归切分，支持 overlap
- 长表格按行拆分并重复表头
- 长代码块按函数/类边界拆分
- 输出 `raw_text`（干净原文）和 `embedding_text`（带标题路径）双字段

## 检索模块

内置混合检索引擎，支持：
- **Milvus 向量检索**：基于 bge-m3 embedding 的语义相似度搜索
- **BM25 关键词检索**：中文逐字分词 + 英文按词切分
- **RRF 融合**：Reciprocal Rank Fusion (k=60) 合并两路排序结果
- **BGE Reranker** 精排（可选）

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
# 解析文档
python -m rag_doc_parser.cli --file ./example.pdf --output ./chunks.json

# 禁用图片描述
python -m rag_doc_parser.cli --file ./report.docx --output ./chunks.json --no-picture-description

# 构建检索索引
python -m rag_doc_parser.retrieval.cli index --input ./chunks.json

# 检索
python -m rag_doc_parser.retrieval.cli search --query "售后政策" --top-k 5
```

## Python 使用

```python
from rag_doc_parser.pipeline import parse_document

chunks = parse_document("./example.pdf")
for chunk in chunks:
    print(chunk.chunk_type, chunk.section_path, chunk.raw_text[:100])

# 检索
from rag_doc_parser.retrieval.hybrid_search import HybridSearcher
from rag_doc_parser.retrieval.config import RetrievalConfig

config = RetrievalConfig()
searcher = HybridSearcher(config)
results = searcher.search("保修条款", top_k=5)
```

## 说明

- 本项目负责文档解析、chunk 生成和混合检索
- 向量存储使用 Milvus，BM25 索引使用内存倒排索引
- PDF 图片描述依赖外部 VLM API
- API Key 必须通过环境变量配置，不可硬编码
- DOCX 解析有 Docling → python-docx 两级 fallback
