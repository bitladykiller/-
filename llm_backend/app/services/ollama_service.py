from typing import List, Dict, AsyncGenerator, Optional, Callable
import aiohttp
import json
from app.core.config import settings
from app.services.base_llm_service import BaseLLMService


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
        on_complete: Optional[Callable] = None
    ) -> AsyncGenerator[str, None]:
        try:
            model = self.chat_model
            full_response: list[str] = []
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
                                    full_response.append(content)
                                    yield f"data: {json.dumps(content, ensure_ascii=False)}\n\n"
                            except json.JSONDecodeError:
                                continue

            if on_complete and user_id is not None and conversation_id is not None:
                complete_response = "".join(full_response)
                await on_complete(user_id, conversation_id, messages, complete_response)

        except Exception as e:
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
            raise
