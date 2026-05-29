"""
最小示例：展示 RAG 文档解析与切分的完整流程。
"""

from rag_doc_parser.pipeline import parse_document


def main():
    # 请将 example.pdf 替换为你的实际 PDF/DOCX 文件
    chunks = parse_document("./example.pdf")

    print(f"总 chunk 数: {len(chunks)}")
    print("=" * 80)

    for chunk in chunks[:5]:
        print(f"类型: {chunk.chunk_type}")
        print(f"章节: {chunk.section_path}")
        print(f"类型: {chunk.chunk_type}")
        print(f"来源: {chunk.source_file}")
        print(f"原始文本（前 300 字）:")
        print(chunk.raw_text[:300])
        print(f"向量文本（前 300 字）:")
        print(chunk.embedding_text[:300])
        print("=" * 80)

    # 统计各类型数量
    from collections import Counter
    type_counts = Counter(c.chunk_type for c in chunks)
    print("\n各类型数量:", dict(type_counts))


if __name__ == "__main__":
    main()
