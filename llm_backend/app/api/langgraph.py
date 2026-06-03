"""
LangGraph Agent API。
v3.7: 图片在 API 层解析为文本上下文，注入 query。无 checkpointer。
"""
from __future__ import annotations

import json
import os
import re
from datetime import datetime
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, HTTPException, UploadFile, File, Form
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from app.lg_agent.lg_states import InputState
from app.lg_agent.utils import new_uuid
from app.lg_agent.lg_builder import graph
from app.core.logger import get_logger

logger = get_logger(__name__)

router = APIRouter(tags=["langgraph"])


def _sanitize_path_component(name: str) -> str:
    return re.sub(r'[^a-zA-Z0-9_-]', '_', name or "unknown")


async def _parse_image_to_context(image_path: str) -> str:
    """将图片解析为文本上下文（调用 Vision API）。

    v3.15 修复：原实现使用 asyncio.run() 在已有事件循环中调用，
    会抛出 RuntimeError: This event loop is already running。
    改为直接 await 协程。
    """
    from app.core.config import settings
    api_key = settings.VISION_API_KEY
    base_url = settings.VISION_BASE_URL
    vision_model = settings.VISION_MODEL
    if not api_key or not base_url or not vision_model:
        return ""

    import base64, io
    from PIL import Image as PILImage
    import aiohttp

    try:
        # 图片处理（CPU 密集，但单张图片通常 < 100ms）
        with PILImage.open(image_path) as img:
            max_size = 1024
            width, height = img.size
            ratio = min(max_size / width, max_size / height)
            if width <= max_size and height <= max_size:
                resized_img = img
            else:
                resized_img = img.resize((int(width * ratio), int(height * ratio)), PILImage.LANCZOS)
            img_byte_arr = io.BytesIO()
            if resized_img.mode != 'RGB':
                resized_img = resized_img.convert('RGB')
            resized_img.save(img_byte_arr, format='JPEG', quality=85)
            img_byte_arr.seek(0)
            image_data = base64.b64encode(img_byte_arr.read()).decode('utf-8')

        headers = {"Content-Type": "application/json", "Authorization": f"Bearer {api_key}"}
        payload = {
            "model": vision_model,
            "messages": [{"role": "user", "content": [
                {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{image_data}"}},
                {"type": "text", "text": "请用中文详细描述这张图片的内容。"}
            ]}],
            "max_tokens": 1000,
            "temperature": 0.3,
        }
        async with aiohttp.ClientSession() as session:
            async with session.post(f"{base_url}/chat/completions", headers=headers,
                                    json=payload, timeout=60) as resp:
                if resp.status == 200:
                    result = await resp.json()
                    return result["choices"][0]["message"]["content"]
        return ""
    except Exception:
        logger.error("图片解析失败 | path=%s", image_path, exc_info=True)
        return ""


async def _stream_graph_response(graph_stream):
    async for c, metadata in graph_stream:
        if c.content and not c.additional_kwargs.get("tool_calls") \
                and "research_plan" not in metadata.get("tags", []):
            content_json = json.dumps(c.content, ensure_ascii=False)
            yield f"data: {content_json}\n\n"


@router.post("/langgraph/query")
async def langgraph_query(
    query: str = Form(...),
    user_id: int = Form(...),
    conversation_id: Optional[str] = Form(None),
    image: Optional[UploadFile] = File(None),
):
    try:
        # 图片预处理：在 API 层解析为文本上下文
        image_context = ""
        if image:
            image_dir = Path("uploads/images")
            if conversation_id:
                image_dir = image_dir / _sanitize_path_component(conversation_id)
            image_dir.mkdir(parents=True, exist_ok=True)

            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            original_name, _ = os.path.splitext(image.filename)
            new_filename = f"{original_name}_{timestamp}{os.path.splitext(image.filename)[1]}"
            image_path = image_dir / new_filename

            content = await image.read()
            with open(image_path, "wb") as f:
                f.write(content)

            image_context = await _parse_image_to_context(str(image_path))

        # 拼接图片上下文到用户 query
        if image_context:
            query = f"[图片描述：{image_context}]\n\n用户问题：{query}"

        thread_id = conversation_id if conversation_id else new_uuid()
        thread_config = {
            "configurable": {
                "thread_id": thread_id,
                "user_id": user_id,
            }
        }

        input_state = InputState(messages=query)
        graph_stream = graph.astream(input=input_state, stream_mode="messages", config=thread_config)

        response = StreamingResponse(
            _stream_graph_response(graph_stream),
            media_type="text/event-stream",
        )
        response.headers["X-Conversation-ID"] = thread_id
        return response

    except HTTPException:
        raise
    except Exception:
        raise HTTPException(status_code=500, detail="Internal server error")
