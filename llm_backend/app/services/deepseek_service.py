from typing import List, Dict, AsyncGenerator, Optional
from openai import AsyncOpenAI
from app.core.config import settings
import json
from app.services.base_llm_service import BaseLLMService
from app.core.logger import get_logger

logger = get_logger(__name__)


class DeepseekService(BaseLLMService):
    def __init__(self, model: str = "deepseek-chat"):
        self.client = AsyncOpenAI(
            api_key=settings.DEEPSEEK_API_KEY,
            base_url=settings.DEEPSEEK_BASE_URL
        )
        self.model = settings.DEEPSEEK_MODEL or model

    async def generate_stream(
        self,
        messages: List[Dict],
        user_id: Optional[int] = None,
        conversation_id: Optional[int] = None,
    ) -> AsyncGenerator[str, None]:
        try:
            full_response: list[str] = []
            response = await self.client.chat.completions.create(
                model=self.model,
                messages=messages,
                stream=True
            )

            async for chunk in response:
                if chunk.choices and chunk.choices[0].delta.content:
                    raw = chunk.choices[0].delta.content
                    full_response.append(raw)
                    yield f"data: {json.dumps(raw, ensure_ascii=False)}\n\n"

        except Exception as e:
            logger.error(
                f"DeepseekService 流式生成异常 | user_id={user_id} "
                f"conversation_id={conversation_id} model={self.model} | {e}",
                exc_info=True,
            )
            yield f"data: {json.dumps('', ensure_ascii=False)}\n\n"

    async def generate(self, messages: List[Dict]) -> str:
        try:
            response = await self.client.chat.completions.create(
                model=self.model,
                messages=messages,
                stream=False
            )
            return response.choices[0].message.content
        except Exception as e:
            logger.error(
                f"DeepseekService 非流式生成异常 | model={self.model} | {e}",
                exc_info=True,
            )
            raise
