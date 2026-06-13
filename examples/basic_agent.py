"""基础 Agent 使用示例。

展示如何使用 deepseek-agent 进行简单的问答对话。
"""
import asyncio
from llm_backend.app.lg_agent.facade import build_agent_graph
from llm_backend.app.core.config import get_settings

async def basic_agent_example():
    """基础 Agent 使用示例。"""
    settings = get_settings()

    # 构建 Agent 图
    agent = await build_agent_graph(
        tenant_id="example_tenant",
        user_id="example_user",
        session_id="example_session",
    )

    # 执行对话
    result = await agent.invoke({
        "user_input": "你好，请介绍一下你自己",
        "tenant_id": "example_tenant",
        "user_id": "example_user",
        "session_id": "example_session",
    })

    print(f"用户输入: {result['user_input']}")
    print(f"Agent 回复: {result['response']}")

if __name__ == "__main__":
    asyncio.run(basic_agent_example())