"""长期记忆抽取器。

职责：
1. 调用 LLM 从当前对话中抽取可复用的长期记忆
2. 过滤无价值内容和敏感信息
3. 将结果拆分为语义记忆与结构化画像
"""

from __future__ import annotations

from app.memory.config import (
    compiled_sensitive_patterns,
)
from app.memory.memory_extractor_support import (
    build_semantic_memories,
    extract_response_text,
    extract_summary_text,
    mask_sensitive_info,
    parse_llm_response,
    should_save_memory_content,
)
from app.memory.profile_utils import normalize_profile_data
from app.memory.schemas import (
    MemoryExtractorResult,
    SessionSummary,
    UserProfileData,
)


class MemoryExtractor:
    """长期记忆抽取编排层。"""

    def __init__(self, llm_client):
        self.llm_client = llm_client
        self.sensitive_patterns = compiled_sensitive_patterns()

    async def extract(
        self,
        user_message: str,
        assistant_message: str,
        session_summary: str | SessionSummary | None = None,
    ) -> tuple[list[MemoryExtractorResult], UserProfileData]:
        """抽取语义记忆 + 结构化画像。

        Returns: (semantic_memories: List[MemoryExtractorResult], profile_data: UserProfileData)
        - semantic_memories: 存入 Milvus
        - profile_data: 存入 MySQL（preferred_brand, budget_range, preferred_category, tags, facts）
        """
        try:
            prompt = self._build_extract_prompt(
                user_message, assistant_message, session_summary
            )
            response = await self.llm_client.ainvoke(prompt)
            raw = extract_response_text(response)
            parsed = parse_llm_response(raw)
            semantic = build_semantic_memories(
                parsed,
                sensitive_patterns=self.sensitive_patterns,
            )
            profile = normalize_profile_data(parsed.get("profile"))
            return semantic, profile
        except Exception:
            return [], {}

    def should_save(self, content: str) -> bool:
        """兼容旧调用方：判断抽取内容是否值得保存为长期记忆。"""
        return should_save_memory_content(content, self.sensitive_patterns)

    def mask_sensitive_info(self, content: str) -> str:
        """兼容旧调用方：对可保留内容做脱敏。"""
        return mask_sensitive_info(content)

    def _build_extract_prompt(
        self,
        user_message: str,
        assistant_message: str,
        session_summary: str | SessionSummary | None = None,
    ) -> str:
        summary_text = extract_summary_text(session_summary)
        summary_block = f"\n当前会话摘要：{summary_text}" if summary_text else ""

        return f"""你是长期记忆抽取助手。从客服对话中判断是否有值得写入长期记忆的信息。

**重要：如果本轮对话只是普通寒暄、简单问答、临时咨询，没有任何长期记忆价值，直接返回空 JSON {{}}。**

只有用户明确表达了以下信息时才抽取：

【A. 语义记忆（存入 Milvus，语义检索）】
- issue_history：用户遇到的具体问题（如"门铃连接不上WiFi"、"订单10248延迟了"）
- solution_note：确认有效的解决方案（如"重置路由器后门铃恢复正常"）

以下内容不需要抽取：
- 普通寒暄（"你好"、"谢谢"、"再见"）
- 临时查询（"今天天气怎么样"）
- 一次性的简单问答（"智能门铃多少钱"、"有货吗"）
- 密码、验证码、身份证号等敏感信息
- 推测、猜测、不确定的信息

【B. 结构化画像（存入 MySQL，精确查询）】
从对话中提取用户**明确表达**的个人信息：
- preferred_brand: 偏好品牌（google/apple/xiaomi/huawei 或 null），如"我喜欢谷歌的产品"→"google"
- budget_range: 预算范围（"0-1000"/"1000-3000"/"3000-5000"/"5000+" 或 null），如"预算三千以内"→"0-3000"
- preferred_category: 偏好品类（"智能门铃"/"智能音箱" 等 或 null）
- tags: 标签数组，如 ["price_sensitive","early_adopter"]
- facts: 事实数组，每项 {{"key":"workplace","value":"ali"}}；常见 key: workplace, family_size, pet, expertise

{summary_block}

用户消息：{user_message}
助手回复：{assistant_message}

输出JSON（无长期记忆价值时返回空对象 {{}}）：
{{
  "semantic": [],
  "profile": {{}}
}}
只输出JSON，不要其他内容。"""
