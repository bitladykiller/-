"""长期记忆抽取器。

职责：
1. 调用 LLM 从当前对话中抽取可复用的长期记忆
2. 过滤无价值内容和敏感信息
3. 将结果拆分为语义记忆与结构化画像
"""

import json
import re

from app.knowledge.domain.schemas import (
    MemoryExtractorResult,
    SessionSummary,
    UserProfileData,
)
from app.knowledge.infrastructure.config import (
    COMPILED_SENSITIVE_PATTERNS,
    LONG_TERM_MEMORY_TYPE_VALUES,
)
from app.knowledge.infrastructure.profile.profile_payload_support import (
    normalize_profile_data,
)
from app.shared.core.json_utils import extract_first_json_object
from app.shared.core.logger import get_logger

logger = get_logger(__name__)
_GREETING_MESSAGES = frozenset(
    {
        "你好",
        "谢谢",
        "不客气",
        "再见",
        "好的",
        "嗯",
        "哦",
        "哈哈",
        "呵呵",
    }
)
_PHONE_PATTERN = re.compile(r"1[3-9]\d{9}")
_ID_CARD_PATTERN = re.compile(r"\d{17}[\dXx]")
_BANK_CARD_PATTERN = re.compile(r"\d{16,19}")
_EMAIL_PATTERN = re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+")


class MemoryExtractor:
    """长期记忆抽取编排层。"""

    def __init__(self, llm_client):
        self.llm_client = llm_client

    async def extract(
        self,
        user_message: str,
        assistant_message: str,
        session_summary: SessionSummary | None = None,
    ) -> tuple[list[MemoryExtractorResult], UserProfileData]:
        """抽取语义记忆 + 结构化画像。

        Returns: (semantic_memories: List[MemoryExtractorResult], profile_data: UserProfileData)
        - semantic_memories: 存入 Milvus
        - profile_data: 存入 MySQL（preferred_brand, budget_range, preferred_category, tags, facts）
        """
        try:
            summary_text = session_summary.content.strip() if session_summary else ""
            summary_block = f"\n当前会话摘要：{summary_text}" if summary_text else ""

            prompt = f"""你是长期记忆抽取助手。从客服对话中判断是否有值得写入长期记忆的信息。

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
            response = await self.llm_client.ainvoke(prompt)
            raw = str(response.text)

            try:
                payload = extract_first_json_object(raw)
                parsed_json = json.loads(payload) if payload is not None else None
                parsed = parsed_json if isinstance(parsed_json, dict) else {}
            except Exception as exc:
                logger.debug("[memory] JSON 解析失败: %s", exc)
                parsed = {}

            semantic: list[MemoryExtractorResult] = []
            for item in parsed.get("semantic", []):
                if not isinstance(item, dict):
                    continue

                masked_content = str(item.get("content", "") or "")
                masked_content = _PHONE_PATTERN.sub(
                    lambda match: match.group()[:3] + "****" + match.group()[-4:],
                    masked_content,
                )
                masked_content = _ID_CARD_PATTERN.sub(
                    lambda match: match.group()[:4] + "**********" + match.group()[-4:],
                    masked_content,
                )
                masked_content = _BANK_CARD_PATTERN.sub(
                    lambda match: match.group()[:4] + " **** **** " + match.group()[-4:],
                    masked_content,
                )
                masked_content = _EMAIL_PATTERN.sub(
                    lambda match: (
                        match.group().partition("@")[0][:3]
                        + "***@"
                        + match.group().partition("@")[2]
                    ),
                    masked_content,
                )
                memory_type = item.get("memory_type", "")
                if not masked_content or len(masked_content) < 10:
                    continue
                if any(pattern.search(masked_content) for pattern in COMPILED_SENSITIVE_PATTERNS):
                    continue
                if masked_content.strip() in _GREETING_MESSAGES:
                    continue
                if memory_type not in LONG_TERM_MEMORY_TYPE_VALUES:
                    continue

                semantic.append(
                    MemoryExtractorResult(
                        memory_type=memory_type,
                        content=masked_content,
                    )
                )
            profile = normalize_profile_data(parsed.get("profile"))
            return semantic, profile
        except Exception as exc:
            logger.debug("[memory] LLM 响应解析失败: %s", exc)
            return [], {}
