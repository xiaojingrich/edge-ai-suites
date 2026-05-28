"""
OpenAI-compatible Chat Completions API endpoint.

Wraps the existing OpenVINO Qwen2.5-7B model as an OpenAI-compatible
HTTP service so that OpenClaw can use it as a local LLM provider.

Endpoint: POST /v1/chat/completions
Provider config for OpenClaw:
  "smart-classroom": {
    "baseUrl": "http://127.0.0.1:8000",
    "apiKey": "local",
    "api": "openai-completions"
  }
"""

import json
import time
import uuid
import logging
from typing import Optional

from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from components.summarizer_component import SummarizerComponent

logger = logging.getLogger(__name__)

router = APIRouter()


class ChatMessage(BaseModel):
    role: str
    content: str


class ChatCompletionRequest(BaseModel):
    model: Optional[str] = None
    messages: list[ChatMessage]
    temperature: Optional[float] = 0.7
    max_tokens: Optional[int] = Field(default=2048, alias="max_tokens")
    stream: Optional[bool] = False
    top_p: Optional[float] = 0.9
    top_k: Optional[int] = 50


class ChatCompletionChoice(BaseModel):
    index: int = 0
    message: ChatMessage
    finish_reason: str = "stop"


class ChatCompletionResponse(BaseModel):
    id: str
    object: str = "chat.completion"
    created: int
    model: str
    choices: list[ChatCompletionChoice]
    usage: dict


@router.post("/v1/chat/completions")
async def chat_completions(request: ChatCompletionRequest):
    model = SummarizerComponent._model
    if model is None:
        raise HTTPException(status_code=503, detail="LLM model not loaded yet.")

    prompt = model.tokenizer.apply_chat_template(
        [{"role": m.role, "content": m.content} for m in request.messages],
        tokenize=False,
        add_generation_prompt=True,
    )

    model_name = getattr(model, "model_name", "qwen2.5-7b")
    request_id = f"chatcmpl-{uuid.uuid4().hex[:12]}"
    created = int(time.time())

    if request.stream:
        return StreamingResponse(
            _stream_response(model, prompt, request_id, created, model_name),
            media_type="text/event-stream",
        )

    result = model.generate(prompt, stream=False)
    if not isinstance(result, str):
        result = model.tokenizer.decode(result[0], skip_special_tokens=True)
        if result.startswith(prompt):
            result = result[len(prompt):]

    return ChatCompletionResponse(
        id=request_id,
        created=created,
        model=model_name,
        choices=[
            ChatCompletionChoice(
                message=ChatMessage(role="assistant", content=result.strip()),
            )
        ],
        usage={
            "prompt_tokens": len(prompt) // 4,
            "completion_tokens": len(result) // 4,
            "total_tokens": (len(prompt) + len(result)) // 4,
        },
    )


async def _stream_response(model, prompt: str, request_id: str, created: int, model_name: str):
    streamer = model.generate(prompt, stream=True)
    for token in streamer:
        chunk = {
            "id": request_id,
            "object": "chat.completion.chunk",
            "created": created,
            "model": model_name,
            "choices": [
                {
                    "index": 0,
                    "delta": {"content": token},
                    "finish_reason": None,
                }
            ],
        }
        yield f"data: {json.dumps(chunk)}\n\n"

    final = {
        "id": request_id,
        "object": "chat.completion.chunk",
        "created": created,
        "model": model_name,
        "choices": [
            {
                "index": 0,
                "delta": {},
                "finish_reason": "stop",
            }
        ],
    }
    yield f"data: {json.dumps(final)}\n\n"
    yield "data: [DONE]\n\n"


def register_llm_routes(app):
    app.include_router(router)