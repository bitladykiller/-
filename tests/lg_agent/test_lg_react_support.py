from langchain_core.messages import AIMessage, HumanMessage

from app.lg_agent.lg_react_support import (
    REACT_RETRY_PROMPT,
    build_answer_check_messages,
    build_react_response,
    build_retry_message,
    build_retry_seed_messages,
    build_tool_error,
    build_transcript,
    dump_retriever_records,
    extract_last_answer,
    needs_more_steps,
)


def test_extract_last_answer_and_more_steps_detection() -> None:
    assert extract_last_answer([]) == "未能确定回答～"
    assert extract_last_answer([AIMessage(content="ok")]) == "ok"
    assert needs_more_steps("Need more steps before finish") is True
    assert needs_more_steps("answer is enough") is False


def test_transcript_retry_seed_and_tool_outputs_are_stable() -> None:
    messages = [
        HumanMessage(content="问题"),
        AIMessage(content="回答"),
    ]

    transcript = build_transcript(messages)

    assert "[human] 问题" in transcript
    assert "[ai] 回答" in transcript
    assert build_retry_message("信息不足") == {
        "role": "user",
        "content": f"{REACT_RETRY_PROMPT}不足原因：信息不足",
    }
    assert build_retry_seed_messages("原问题", "候选答案") == [
        {"role": "user", "content": "原问题"},
        {"role": "assistant", "content": "候选答案"},
    ]
    assert dump_retriever_records({"records": [{"name": "空调"}]}) == '[{"name": "空调"}]'
    assert build_tool_error("服务不可用") == '{"error": "服务不可用"}'


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
