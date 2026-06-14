from langchain_core.messages import AIMessage, HumanMessage

from app.chat.infrastructure.react.react import (
    build_answer_check_messages,
    build_react_response,
    needs_more_steps,
)


def test_more_steps_detection() -> None:
    assert needs_more_steps("Need more steps before finish") is True
    assert needs_more_steps("answer is enough") is False


def test_answer_check_messages_and_react_response_are_stable() -> None:
    messages = build_answer_check_messages(
        judge_system_prompt="judge",
        question="怎么修空调",
        transcript="[ai] 检索到了说明",
        candidate_answer="先检查电源",
    )
    response = build_react_response("最终答案")

    assert messages == [
        {"role": "system", "content": "judge"},
        {
            "role": "user",
            "content": (
                "用户问题：怎么修空调\n\n"
                "ReAct 过程记录：\n[ai] 检索到了说明\n\n"
                "当前候选答案：先检查电源"
            ),
        },
    ]
    assert [message.content for message in response["messages"]] == [
        "正在综合分析...",
        "最终答案",
    ]
