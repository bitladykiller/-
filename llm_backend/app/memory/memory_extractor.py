"""
长期记忆抽取器。
从当前对话中判断是否有值得写入长期记忆的信息。
过滤敏感信息，只抽取有复用价值的信息。
"""
import re
import json
from typing import List, Optional
from app.memory.config import LONG_TERM_MEMORY_TYPES, SENSITIVE_PATTERNS
from app.memory.schemas import MemoryExtractorResult


class MemoryExtractor:
    def __init__(self, llm_client):
        self.llm_client = llm_client
        self.sensitive_patterns = [
            re.compile(p, re.IGNORECASE) for p in SENSITIVE_PATTERNS
        ]

    async def extract(
        self, user_message: str, assistant_message: str, session_summary=None
    ):
        """抽取语义记忆 + 结构化画像。

        Returns: (semantic_memories: List[MemoryExtractorResult], profile_data: dict)
        - semantic_memories: 存入 Milvus
        - profile_data: 存入 MySQL（preferred_brand, budget_range, tags, facts）
        """
        try:
            prompt = self._build_extract_prompt(
                user_message, assistant_message, session_summary
            )
            response = await self.llm_client.ainvoke(prompt)
            raw = response.content if hasattr(response, "content") else str(response)
            parsed = self._parse_llm_response(raw)

            # 解析语义记忆
            semantic = []
            for item in parsed.get("semantic", []):
                content = item.get("content", "")
                if not self.should_save(content):
                    continue
                content = self.mask_sensitive_info(content)
                if content:
                    typ = item.get("memory_type", "")
                    if typ in ("issue_history", "solution_note"):
                        semantic.append(
                            MemoryExtractorResult(
                                memory_type=typ, content=content,
                                reason=item.get("reason"),
                            )
                        )

            # 解析结构化画像
            profile = parsed.get("profile", {}) or {}
            if not isinstance(profile, dict):
                profile = {}

            return semantic, profile
        except Exception:
            return [], {}

    def should_save(self, content: str) -> bool:
        if len(content) < 10:
            return False
        for pattern in self.sensitive_patterns:
            if pattern.search(content):
                return False
        greetings = ["你好", "谢谢", "不客气", "再见", "好的", "嗯", "哦", "哈哈", "呵呵"]
        if content.strip() in greetings:
            return False
        return True

    def mask_sensitive_info(self, content: str) -> str:
        content = re.sub(r"1[3-9]\d{9}", lambda m: m.group()[:3] + "****" + m.group()[-4:], content)
        content = re.sub(r"\d{17}[\dXx]", lambda m: m.group()[:4] + "**********" + m.group()[-4:], content)
        content = re.sub(r"\d{16,19}", lambda m: m.group()[:4] + " **** **** " + m.group()[-4:], content)
        content = re.sub(r"[\w.+-]+@[\w-]+\.[\w.-]+", lambda m: m.group()[:3] + "***@" + m.group().split("@")[-1], content)
        return content

    def _build_extract_prompt(
        self, user_message: str, assistant_message: str, session_summary=None
    ) -> str:
        summary_text = ""
        if session_summary:
            summary_text = f"\n当前会话摘要：{session_summary}"

        return f"""你是长期记忆抽取助手。从客服对话中抽取两类信息：

【A. 语义记忆（存入 Milvus，语义检索）】
类型：
1. issue_history（历史问题）：用户遇到的具体问题
2. solution_note（有效方案）：确认有效的解决方案

不要抽取：密码/验证码/身份证等敏感信息、寒暄、临时询问、猜测。

【B. 结构化画像（存入 MySQL，精确查询）】
从对话中提取用户明确表达的：
- preferred_brand: 偏好品牌（google/apple/xiaomi/huawei 或 null）
- budget_range: 预算范围（"0-1000"/"1000-3000"/"3000-5000"/"5000+" 或 null）
- preferred_category: 偏好品类（"智能门铃"/"智能音箱" 等，不要加"智能"前缀不必要的）
- tags: 标签数组，如 ["price_sensitive","early_adopter","smart_home"]
- facts: 事实数组，每项 {{"key":"workplace","value":"ali"}}；只输出 user_facts 表的 key-value
  常见 key: workplace, family_size, pet, expertise, shopping_frequency

{summary_text}

用户消息：{user_message}
助手回复：{assistant_message}

输出JSON：
{{
  "semantic": [
    {{"memory_type": "issue_history"|"solution_note", "content": "...", "reason": "..."}}
  ],
  "profile": {{
    "preferred_brand": "google"|null,
    "budget_range": "3000-5000"|null,
    "preferred_category": "智能门铃"|null,
    "tags": ["smart_home"],
    "facts": [{{"key":"workplace","value":"ali"}}]
  }}
}}
只输出JSON，不要其他内容。"""

    def _parse_llm_response(self, response: str) -> dict:
        try:
            match = re.search(r"\{.*\}", response, re.DOTALL)
            if not match:
                return {}
            parsed = json.loads(match.group())
            if not isinstance(parsed, dict):
                return {}
            return parsed
        except Exception:
            return {}
