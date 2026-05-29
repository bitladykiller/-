from app.lg_agent.lg_states import AgentState, InputState, Router, GradeHallucinations
from app.lg_agent.lg_prompts import (
    ROUTER_SYSTEM_PROMPT, GET_ADDITIONAL_SYSTEM_PROMPT,
    GENERAL_QUERY_SYSTEM_PROMPT, GET_IMAGE_SYSTEM_PROMPT,
    GUARDRAILS_SYSTEM_PROMPT, RAGSEARCH_SYSTEM_PROMPT, CHECK_HALLUCINATIONS,
)
from langchain_core.runnables import RunnableConfig
from langchain_deepseek import ChatDeepSeek
from langchain_ollama import ChatOllama
from app.core.config import settings, ServiceType
from app.security import wrap_user_message
from typing import cast, Literal, List, Dict, Any, Optional
from langchain_core.messages import BaseMessage, AIMessage
from langgraph.graph import END, START, StateGraph
from app.lg_agent.kg_sub_graph.agentic_rag_agents.retrievers.cypher_examples.northwind_retriever import NorthwindCypherRetriever
from app.lg_agent.kg_sub_graph.agentic_rag_agents.workflows.multi_agent.multi_tool import create_multi_tool_workflow
from app.lg_agent.kg_sub_graph.kg_neo4j_conn import get_neo4j_graph
from pydantic import BaseModel, Field
from app.lg_agent.kg_sub_graph.agentic_rag_agents.components.utils.utils import retrieve_and_parse_schema_from_graph_for_prompts
from langchain_core.prompts import ChatPromptTemplate
import base64
import aiohttp
from pathlib import Path
from app.memory.redis_short_term_memory import RedisShortTermMemory
from app.memory.simple_long_term_memory import SimpleLongTermMemory
from app.memory.memory_extractor import MemoryExtractor
from app.memory.memory_middleware import MemoryMiddleware
from app.memory.prompt_builder import build_memory_injection_prompt, build_summary_injection_prompt


SCOPE_DESCRIPTION = """
个人电商经营范围：智能家居产品，包括但不限于：
- 智能照明（灯泡、灯带、开关）
- 智能安防（摄像头、门锁、传感器）
- 智能控制（温控器、遥控器、集线器）
- 智能音箱（语音助手、音响）
- 智能厨电（电饭煲、冰箱、洗碗机）
- 智能清洁（扫地机器人、洗衣机）

不包含：服装、鞋类、体育用品、化妆品、食品等非智能家居产品。
"""


class AdditionalGuardrailsOutput(BaseModel):
    decision: Literal["end", "continue"] = Field(
        description="Decision on whether the question is related to the graph contents."
    )


def _create_agent_model(temperature: float = 0.7):
    """创建 Agent 模型实例，每个节点使用独立温度。"""
    if settings.AGENT_SERVICE == ServiceType.DEEPSEEK:
        return ChatDeepSeek(api_key=settings.DEEPSEEK_API_KEY, model_name=settings.DEEPSEEK_MODEL, temperature=temperature)
    return ChatOllama(model=settings.OLLAMA_AGENT_MODEL, base_url=settings.OLLAMA_BASE_URL, temperature=temperature)


# 按使用场景分离温度：
# 路由分类需要确定性（0.1），闲聊需要创造力（0.7），Guardrails 需要确定性（0.1）
_agent_model = _create_agent_model(0.7)              # 通用 / 闲聊
_cypher_model = _create_agent_model(0.2)              # KG 子图（Cypher 生成需要精确性）
_router_model = _create_agent_model(0.1)              # 路由分类
_guardrails_model = _create_agent_model(0.1)          # Guardrails 校验
_infogap_model = _create_agent_model(0.3)             # 追问生成（需要一点创造性）

# Memory middleware singleton
_memory_middleware_instance: Optional[MemoryMiddleware] = None


def _get_memory_middleware() -> Optional[MemoryMiddleware]:
    global _memory_middleware_instance
    if _memory_middleware_instance is not None:
        return _memory_middleware_instance
    try:
        import redis.asyncio as redis
        from pymilvus import MilvusClient
        redis_client = redis.from_url(settings.REDIS_URL, decode_responses=True)
        redis_stm = RedisShortTermMemory(redis_client)
        if settings.EMBEDDING_TYPE == "ollama":
            from langchain_ollama import OllamaEmbeddings
            embedding_model = OllamaEmbeddings(model=settings.EMBEDDING_MODEL, base_url=settings.OLLAMA_BASE_URL)
        else:
            from langchain_community.embeddings import HuggingFaceEmbeddings
            embedding_model = HuggingFaceEmbeddings(model_name=settings.EMBEDDING_MODEL)
        milvus_client = MilvusClient(uri=settings.MILVUS_URL)
        milvus_ltm = SimpleLongTermMemory(milvus_client=milvus_client, embedding_model=embedding_model, collection_name=settings.MILVUS_COLLECTION_NAME)
        if settings.AGENT_SERVICE == ServiceType.DEEPSEEK:
            extractor_llm = ChatDeepSeek(api_key=settings.DEEPSEEK_API_KEY, model_name=settings.DEEPSEEK_MODEL, temperature=0.3)
        else:
            extractor_llm = ChatOllama(model=settings.OLLAMA_AGENT_MODEL, base_url=settings.OLLAMA_BASE_URL, temperature=0.3)
        memory_extractor = MemoryExtractor(llm_client=extractor_llm)
        _memory_middleware_instance = MemoryMiddleware(redis_stm=redis_stm, milvus_ltm=milvus_ltm, memory_extractor=memory_extractor)
        # 启动时异步检查各层连接健康（结果存 _healthy 字典，各层独立检查互不影响）
        import asyncio
        asyncio.ensure_future(_memory_middleware_instance.health_check())
        return _memory_middleware_instance
    except Exception:
        return None


def build_memory_context(session_summary, recent_messages, long_term_memories, user_profile=None) -> str:
    parts = []
    # v3.2: 用户画像（MySQL，精确字段）
    if user_profile and isinstance(user_profile, dict):
        profile_lines = []
        if user_profile.get("preferred_brand"):
            profile_lines.append(f"偏好品牌: {user_profile['preferred_brand']}")
        if user_profile.get("budget_range"):
            profile_lines.append(f"预算范围: {user_profile['budget_range']}")
        if user_profile.get("preferred_category"):
            profile_lines.append(f"偏好品类: {user_profile['preferred_category']}")
        if user_profile.get("tags"):
            profile_lines.append(f"标签: {', '.join(user_profile['tags'])}")
        for fact in (user_profile.get("facts") or []):
            profile_lines.append(f"{fact.get('key','')}: {fact.get('value','')}")
        if profile_lines:
            parts.append("【用户画像】\n" + "\n".join(profile_lines))
    if recent_messages:
        messages_text = ""
        for msg in recent_messages:
            role = "用户" if msg.role == "user" else "助手"
            messages_text += f"[{role}]: {msg.content}\n"
        parts.append(f"【最近对话记录】\n{messages_text}")
    summary_text = build_summary_injection_prompt(session_summary)
    if summary_text:
        parts.append(summary_text)
    ltm_text = build_memory_injection_prompt(long_term_memories)
    if ltm_text:
        parts.append(ltm_text)
    if not parts:
        return ""
    return "\n\n" + "\n\n".join(parts)


def _build_safe_messages(system_prompt: str, messages: list) -> list:
    """构建安全的消息列表：将最后一条用户消息包裹在 XML 标签中。

    原始消息保留给记忆存储，此函数仅用于构建发送给 LLM 的 prompt。
    """
    safe = [{"role": "system", "content": system_prompt}]
    for msg in messages:
        role = msg.get("role", msg.type if hasattr(msg, "type") else "user")
        content = msg.get("content", "") if isinstance(msg, dict) else getattr(msg, "content", "")
        if role == "user":
            wrapped, _ = wrap_user_message(content)
            safe.append({"role": "user", "content": wrapped})
        else:
            safe.append({"role": role, "content": content})
    return safe


async def analyze_and_route_query(state: AgentState, *, config: RunnableConfig) -> dict:
    messages = _build_safe_messages(ROUTER_SYSTEM_PROMPT, state.messages)
    response = cast(Router, await _router_model.with_structured_output(Router).ainvoke(messages))
    return {"router": response}


def route_query(state: AgentState) -> Literal["respond_to_general_query", "get_additional_info", "create_research_plan", "create_image_query", "create_file_query"]:
    if hasattr(state, "config") and state.config and state.config.get("configurable", {}).get("image_path"):
        return "create_image_query"
    _type = state.router["type"]
    if _type == "general-query":
        return "respond_to_general_query"
    elif _type == "additional-query":
        return "get_additional_info"
    elif _type == "graphrag-query":
        return "create_research_plan"
    elif _type == "image-query":
        return "create_image_query"
    elif _type == "file-query":
        return "create_file_query"
    else:
        raise ValueError(f"Unknown router type {_type}")


async def respond_to_general_query(state: AgentState, *, config: RunnableConfig) -> Dict[str, List[BaseMessage]]:
    system_prompt = GENERAL_QUERY_SYSTEM_PROMPT.format(logic=state.router["logic"])
    middleware = _get_memory_middleware()
    if middleware is not None:
        try:
            configurable = config.get("configurable", {})
            user_message = state.messages[-1].content if state.messages else ""
            memory_state = await middleware.before_agent(
                tenant_id=configurable.get("tenant_id", "default"),
                user_id=configurable.get("user_id", "anonymous"),
                session_id=configurable.get("thread_id", "default"),
                user_input=user_message,
            )
            memory_context = build_memory_context(memory_state.session_summary, memory_state.recent_messages, memory_state.long_term_memories, memory_state.user_profile)
            if memory_context:
                system_prompt += memory_context
        except Exception:
            pass
    messages = _build_safe_messages(system_prompt, state.messages)
    response = await _agent_model.ainvoke(messages)
    return {"messages": [response]}


async def get_additional_info(state: AgentState, *, config: RunnableConfig) -> Dict[str, List[BaseMessage]]:
    neo4j_graph = None
    try:
        neo4j_graph = get_neo4j_graph()
    except Exception:
        pass

    scope_description = SCOPE_DESCRIPTION
    scope_context = f"参考此范围描述来决策:\n{scope_description}"
    graph_context = f"\n参考图表结构来回答:\n{retrieve_and_parse_schema_from_graph_for_prompts(neo4j_graph)}" if neo4j_graph is not None else ""
    message = scope_context + graph_context + "\nQuestion: {question}"

    full_system_prompt = ChatPromptTemplate.from_messages([
        ("system", GUARDRAILS_SYSTEM_PROMPT),
        ("human", message),
    ])
    guardrails_chain = full_system_prompt | _guardrails_model.with_structured_output(AdditionalGuardrailsOutput)
    raw_question = state.messages[-1].content if state.messages else ""
    safe_question, _ = wrap_user_message(raw_question)
    guardrails_output = await guardrails_chain.ainvoke({"question": safe_question})

    if guardrails_output.decision == "end":
        return {"messages": [AIMessage(content="抱歉，我家暂时没有这方面的商品，可以在别家看看哦~")]}

    system_prompt = GET_ADDITIONAL_SYSTEM_PROMPT.format(logic=state.router["logic"])
    middleware = _get_memory_middleware()
    if middleware is not None:
        try:
            configurable = config.get("configurable", {})
            user_message = state.messages[-1].content if state.messages else ""
            memory_state = await middleware.before_agent(
                tenant_id=configurable.get("tenant_id", "default"),
                user_id=configurable.get("user_id", "anonymous"),
                session_id=configurable.get("thread_id", "default"),
                user_input=user_message,
            )
            memory_context = build_memory_context(memory_state.session_summary, memory_state.recent_messages, memory_state.long_term_memories, memory_state.user_profile)
            if memory_context:
                system_prompt += memory_context
        except Exception:
            pass
    messages = _build_safe_messages(system_prompt, state.messages)
    response = await _infogap_model.ainvoke(messages)
    return {"messages": [response]}


async def create_image_query(state: AgentState, *, config: RunnableConfig) -> Dict[str, List[BaseMessage]]:
    image_path = config.get("configurable", {}).get("image_path", None)
    if not image_path or not Path(image_path).exists():
        return {"messages": [AIMessage(content="抱歉，我无法查看这张图片，请重新上传。")]}
    api_key = settings.VISION_API_KEY
    base_url = settings.VISION_BASE_URL
    vision_model = settings.VISION_MODEL
    if not api_key or not base_url or not vision_model:
        return {"messages": [AIMessage(content="抱歉，我无法查看这张图片，请重新上传。")]}
    try:
        from PIL import Image
        import io
        with Image.open(image_path) as img:
            max_size = 1024
            width, height = img.size
            ratio = min(max_size / width, max_size / height)
            if width <= max_size and height <= max_size:
                resized_img = img
            else:
                resized_img = img.resize((int(width * ratio), int(height * ratio)), Image.LANCZOS)
            img_byte_arr = io.BytesIO()
            if resized_img.mode != 'RGB':
                resized_img = resized_img.convert('RGB')
            resized_img.save(img_byte_arr, format='JPEG', quality=85)
            img_byte_arr.seek(0)
            image_data = base64.b64encode(img_byte_arr.read()).decode('utf-8')
        headers = {"Content-Type": "application/json", "Authorization": f"Bearer {api_key}"}
        payload = {"model": vision_model, "messages": [{"role": "user", "content": [{"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{image_data}"}}]}], "max_tokens": 4000, "temperature": 0.7}
        async with aiohttp.ClientSession() as session:
            async with session.post(f"{base_url}/chat/completions", headers=headers, json=payload, timeout=60) as http_response:
                if http_response.status == 200:
                    result = await http_response.json()
                    image_description = result["choices"][0]["message"]["content"]
                    system_prompt = GET_IMAGE_SYSTEM_PROMPT.format(image_description=image_description)
                    messages = [{"role": "system", "content": system_prompt}] + state.messages
                    answer = await _agent_model.ainvoke(messages)
                    return {"messages": [answer]}
                else:
                    return {"messages": [AIMessage(content="抱歉，我无法查看这张图片，请重新上传。")]}
    except Exception:
        return {"messages": [AIMessage(content="抱歉，我无法查看这张图片，请重新上传。")]}


async def create_file_query(state: AgentState, *, config: RunnableConfig) -> Dict[str, List[BaseMessage]]:
    return {"messages": [AIMessage(content="抱歉，文件解析功能正在开发中，暂时不支持文件查询。请尝试用文字描述您的问题，我会尽力帮您解答。")]}


async def create_research_plan(state: AgentState, *, config: RunnableConfig) -> Dict[str, List[str] | str]:
    neo4j_graph = None
    try:
        neo4j_graph = get_neo4j_graph()
    except Exception:
        pass
    if neo4j_graph is None:
        return {"messages": [AIMessage(content="抱歉，知识库服务暂时不可用，请稍后重试。")]}
    cypher_retriever = NorthwindCypherRetriever()
    from app.lg_agent.kg_sub_graph.kg_tools_list import cypher_query, predefined_cypher, rag_document_query
    tool_schemas: List[type[BaseModel]] = [cypher_query, predefined_cypher, rag_document_query]
    from app.lg_agent.kg_sub_graph.agentic_rag_agents.components.predefined_cypher.cypher_dict import predefined_cypher_dict
    scope_description = SCOPE_DESCRIPTION
    # 使用低温模型（0.2）：Cypher 生成需要精确性，非创意性
    multi_tool_workflow = create_multi_tool_workflow(
        llm=_cypher_model, graph=neo4j_graph, tool_schemas=tool_schemas,
        predefined_cypher_dict=predefined_cypher_dict, cypher_example_retriever=cypher_retriever,
        scope_description=scope_description, llm_cypher_validation=True,
    )
    last_message = state.messages[-1].content if state.messages else ""
    input_state = {"question": last_message, "data": [], "history": []}
    response = await multi_tool_workflow.ainvoke(input_state)
    # 返回两条消息：状态提示 + 实际回复，父图 streaming 逐条输出
    return {"messages": [
        AIMessage(content="正在查询知识库...  "),
        AIMessage(content=response["answer"]),
    ]}


async def check_hallucinations(state: AgentState, *, config: RunnableConfig) -> dict:
    system_prompt = CHECK_HALLUCINATIONS.format(documents=state.documents, generation=state.messages[-1])
    messages = [{"role": "system", "content": system_prompt}] + state.messages
    response = cast(GradeHallucinations, await _guardrails_model.with_structured_output(GradeHallucinations).ainvoke(messages))
    return {"hallucination": response}


async def after_response(state: AgentState, *, config: RunnableConfig) -> dict:
    middleware = _get_memory_middleware()
    if middleware is None:
        return {}
    try:
        configurable = config.get("configurable", {})
        tenant_id = configurable.get("tenant_id", "default")
        user_id = configurable.get("user_id", "anonymous")
        session_id = configurable.get("thread_id", "default")
        user_message = state.messages[-2].content if len(state.messages) >= 2 else ""
        assistant_message = state.messages[-1].content if state.messages else ""
        if user_message and assistant_message:
            await middleware.after_agent(tenant_id=tenant_id, user_id=user_id, session_id=session_id, user_message=user_message, assistant_message=assistant_message)
    except Exception:
        pass
    return {}


builder = StateGraph(AgentState, input=InputState)
builder.add_node(analyze_and_route_query)
builder.add_node(respond_to_general_query)
builder.add_node(get_additional_info)
builder.add_node("create_research_plan", create_research_plan)
builder.add_node(create_image_query)
builder.add_node(create_file_query)
builder.add_node("after_response", after_response)

builder.add_edge(START, "analyze_and_route_query")
builder.add_conditional_edges("analyze_and_route_query", route_query)
builder.add_edge("respond_to_general_query", "after_response")
builder.add_edge("get_additional_info", "after_response")
builder.add_edge("create_image_query", "after_response")
builder.add_edge("create_file_query", "after_response")
builder.add_edge("after_response", END)
builder.add_edge("create_research_plan", "after_response")

graph = builder.compile()
