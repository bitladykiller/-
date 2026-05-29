import threading

from app.core.config import settings, ServiceType
from app.services.deepseek_service import DeepseekService
from app.services.ollama_service import OllamaService
from app.services.search_service import SearchService

# 模块级单例：复用 LLM 客户端，避免每次请求新建连接
# 使用 threading.Lock 保证多线程下的单例安全
_lock = threading.Lock()
_chat_service = None
_reason_service = None
_search_service = None


def _get_chat_singleton():
    global _chat_service
    if _chat_service is None:
        with _lock:
            if _chat_service is None:
                _chat_service = (
                    DeepseekService()
                    if settings.CHAT_SERVICE == ServiceType.DEEPSEEK
                    else OllamaService()
                )
    return _chat_service


def _get_reason_singleton():
    global _reason_service
    if _reason_service is None:
        with _lock:
            if _reason_service is None:
                _reason_service = (
                    DeepseekService()
                    if settings.REASON_SERVICE == ServiceType.DEEPSEEK
                    else OllamaService()
                )
    return _reason_service


def _get_search_singleton():
    global _search_service
    if _search_service is None:
        with _lock:
            if _search_service is None:
                _search_service = SearchService()
    return _search_service


class LLMFactory:
    @staticmethod
    def create_chat_service():
        return _get_chat_singleton()

    @staticmethod
    def create_reasoner_service():
        return _get_reason_singleton()

    @staticmethod
    def create_search_service():
        return _get_search_singleton()
