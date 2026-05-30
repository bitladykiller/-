from typing import List, Dict, AsyncGenerator, Optional
import aiohttp
import json
from app.core.config import settings
from app.services.base_llm_service import BaseLLMService
from app.core.logger import get_logger

logger = get_logger(__name__)


class OllamaService(BaseLLMService):
    def __init__(self):
        self.base_url = settings.OLLAMA_BASE_URL
        self.chat_model = settings.OLLAMA_CHAT_MODEL
        self.reason_model = settings.OLLAMA_REASON_MODEL

    async def generate_stream(
        self,
        messages: List[Dict],
        user_id: Optional[int] = None,
        conversation_id: Optional[int] = None,
    ) -> AsyncGenerator[str, None]:
        try:
            model = self.chat_model
            async with aiohttp.ClientSession() as session:
                async with session.post(
                    f"{self.base_url}/api/chat",
                    json={
                        "model": model,
                        "messages": messages,
                        "stream": True,
                        "keep_alive": -1,
                        "options": {"temperature": 0.7}
                    }
                ) as response:
                    async for line in response.content:
                        if line:
                            try:
                                chunk = json.loads(line)
                                if content := chunk.get("message", {}).get("content"):
                                    yield f"data: {json.dumps(content, ensure_ascii=False)}\n\n"
                            except json.JSONDecodeError:
                                continue

        except Exception as e:
            logger.error(
                f"OllamaService 流式生成异常 | user_id={user_id} "
                f"conversation_id={conversation_id} model={self.chat_model} "
                f"base_url={self.base_url} | {e}",
                exc_info=True,
            )
            yield f"data: {json.dumps('', ensure_ascii=False)}\n\n"

    async def generate(self, messages: List[Dict]) -> str:
        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(
                    f"{self.base_url}/api/chat",
                    json={
                        "model": self.chat_model,
                        "messages": messages,
                        "stream": False,
                        "keep_alive": -1,
                        "options": {"temperature": 0.7}
                    }
                ) as response:
                    result = await response.json()
                    return result["message"]["content"]
        except Exception as e:
            logger.error(
                f"OllamaService 非流式生成异常 | model={self.chat_model} "
                f"base_url={self.base_url} | {e}",
                exc_info=True,
            )
            raise
