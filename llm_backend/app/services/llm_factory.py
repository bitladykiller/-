import threading

from app.core.config import settings, ServiceType
from app.services.deepseek_service import DeepseekService
from app.services.ollama_service import OllamaService

_lock = threading.Lock()
_chat_service = None
_reason_service = None


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


class LLMFactory:
    @staticmethod
    def create_chat_service():
        return _get_chat_singleton()

    @staticmethod
    def create_reasoner_service():
        return _get_reason_singleton()
