from abc import ABC, abstractmethod
from typing import List, Dict, AsyncGenerator, Optional, Callable


class BaseLLMService(ABC):
    """LLM 服务抽象基类，定义所有 LLM 服务实现的公共接口。

    所有 LLM 服务（如 DeepseekService、OllamaService）必须继承此基类
    并实现 generate_stream 和 generate 方法，确保不同后端之间可以无缝切换。
    """

    @abstractmethod
    async def generate_stream(
        self,
        messages: List[Dict],
        user_id: Optional[int] = None,
        conversation_id: Optional[int] = None,
        on_complete: Optional[Callable] = None,
    ) -> AsyncGenerator[str, None]:
        """流式生成回复。

        Args:
            messages: 对话消息列表，格式为 [{"role": "user", "content": "..."}]
            user_id: 可选的用户 ID，用于缓存等场景
            conversation_id: 可选的会话 ID，用于持久化存储
            on_complete: 可选的回调函数，在生成完成后执行（如保存消息到数据库）

        Yields:
            str: SSE 格式的流式数据块，格式为 "data: {...}\\n\\n"
        """
        yield  # pragma: no cover

    @abstractmethod
    async def generate(self, messages: List[Dict]) -> str:
        """非流式生成回复。

        Args:
            messages: 对话消息列表，格式为 [{"role": "user", "content": "..."}]

        Returns:
            str: 模型生成的完整回复文本

        Raises:
            Exception: 当 LLM 调用失败时抛出异常
        """
        ...
