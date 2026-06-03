"""
服务层模块。

v3.17: 清理死代码。移除了 BaseLLMService → DeepseekService/OllamaService → LLMFactory
整个继承链（Agent 已统一使用 lg_models.py 的 _LazyModel + create_agent_model）。
保留以下活跃服务：
- conversation_service: 会话 CRUD（MySQL conversations 表）
- indexing_service: 文档解析/索引（调用 rag_doc_parser）
- task_queue: 异步文档解析任务（Redis 状态存储）
- user_profile_service: 用户画像管理（MySQL + Redis 缓存）
"""