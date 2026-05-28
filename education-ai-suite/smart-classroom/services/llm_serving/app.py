"""
LLM Serving Service — Persistent FastAPI service for LLM inference.

Loads the OpenVINO LLM model once at startup and serves requests via
OpenAI-compatible chat completions API. This avoids repeated model
loading/unloading on each request.

Endpoint: POST /v1/chat/completions
Health:   GET  /health
"""

import gc
import json
import logging
import os
import sys
import threading
import time
import uuid
from contextlib import asynccontextmanager
from typing import Optional

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel, Field

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(name)s] %(levelname)s: %(message)s")
logger = logging.getLogger("llm_serving")

LLM_HOST = os.environ.get("LLM_HOST", "127.0.0.1")
LLM_PORT = int(os.environ.get("LLM_PORT", "8899"))
LLM_MODEL_PATH = os.environ.get("LLM_MODEL_PATH", "")
LLM_DEVICE = os.environ.get("LLM_DEVICE", "GPU")
LLM_MAX_NEW_TOKENS = int(os.environ.get("LLM_MAX_NEW_TOKENS", "5120"))
LLM_USE_OV_GENAI = os.environ.get("LLM_USE_OV_GENAI", "false").lower() in ("true", "1", "yes")

model_lock = threading.Lock()
pipe = None
tokenizer = None
model_ready = False


def _load_model():
    global pipe, tokenizer, model_ready

    logger.info(f"Loading LLM model from: {LLM_MODEL_PATH}, device: {LLM_DEVICE}, use_ov_genai: {LLM_USE_OV_GENAI}")

    if LLM_USE_OV_GENAI:
        import openvino_genai as ov_genai
        from transformers import AutoTokenizer

        tokenizer = AutoTokenizer.from_pretrained(LLM_MODEL_PATH)
        pipe = ov_genai.LLMPipeline(LLM_MODEL_PATH, device=LLM_DEVICE)
    else:
        from transformers import AutoTokenizer
        from optimum.intel.openvino import OVModelForCausalLM

        tokenizer = AutoTokenizer.from_pretrained(
            LLM_MODEL_PATH, trust_remote_code=True, fix_mistral_regex=True
        )
        if tokenizer.pad_token is None:
            tokenizer.pad_token = tokenizer.eos_token

        pipe = OVModelForCausalLM.from_pretrained(
            LLM_MODEL_PATH, device=LLM_DEVICE, use_cache=True
        )

    model_ready = True
    logger.info("LLM model loaded successfully and ready to serve.")


@asynccontextmanager
async def lifespan(app: FastAPI):
    _load_model()
    yield
    global pipe, tokenizer, model_ready
    logger.info("Shutting down LLM service, releasing model...")
    model_ready = False
    del pipe
    del tokenizer
    gc.collect()


app = FastAPI(lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


class ChatMessage(BaseModel):
    role: str
    content: str


class ChatCompletionRequest(BaseModel):
    model: Optional[str] = None
    messages: list[ChatMessage]
    temperature: Optional[float] = 0.7
    max_tokens: Optional[int] = Field(default=None, alias="max_tokens")
    stream: Optional[bool] = False
    top_p: Optional[float] = 0.9
    top_k: Optional[int] = 50
    do_sample: Optional[bool] = True
    raw_prompt: Optional[bool] = False


@app.post("/v1/chat/completions")
async def chat_completions(request: ChatCompletionRequest):
    if not model_ready:
        return JSONResponse(status_code=503, content={"error": "Model not loaded yet."})

    max_tokens = request.max_tokens or LLM_MAX_NEW_TOKENS
    request_id = f"chatcmpl-{uuid.uuid4().hex[:12]}"
    created = int(time.time())
    model_name = os.path.basename(LLM_MODEL_PATH)

    if request.raw_prompt:
        prompt = request.messages[0].content
    else:
        prompt = tokenizer.apply_chat_template(
            [{"role": m.role, "content": m.content} for m in request.messages],
            tokenize=False,
            add_generation_prompt=True,
        )

    if request.stream:
        return StreamingResponse(
            _stream_generate(prompt, request, max_tokens, request_id, created, model_name),
            media_type="text/event-stream",
        )

    if not model_lock.acquire(blocking=False):
        return JSONResponse(status_code=429, content={"error": "Model is busy. Try again later."})

    try:
        result = _generate_sync(prompt, request, max_tokens)
    finally:
        model_lock.release()

    return {
        "id": request_id,
        "object": "chat.completion",
        "created": created,
        "model": model_name,
        "choices": [
            {
                "index": 0,
                "message": {"role": "assistant", "content": result},
                "finish_reason": "stop",
            }
        ],
        "usage": {
            "prompt_tokens": len(prompt) // 4,
            "completion_tokens": len(result) // 4,
            "total_tokens": (len(prompt) + len(result)) // 4,
        },
    }


def _generate_sync(prompt: str, request: ChatCompletionRequest, max_tokens: int) -> str:
    if LLM_USE_OV_GENAI:
        result = pipe.generate(
            prompt,
            max_new_tokens=max_tokens,
            temperature=request.temperature or 0.7,
            do_sample=request.do_sample if request.do_sample is not None else True,
        )
        return result
    else:
        inputs = tokenizer(prompt, return_tensors="pt")
        try:
            output = pipe.generate(
                input_ids=inputs.input_ids,
                max_new_tokens=max_tokens,
                do_sample=request.do_sample if request.do_sample is not None else True,
                temperature=max(request.temperature or 0.7, 0.1),
                top_p=request.top_p or 0.9,
                top_k=request.top_k or 50,
                pad_token_id=tokenizer.eos_token_id,
                eos_token_id=tokenizer.eos_token_id,
            )
        except ValueError as e:
            if "inf" in str(e) or "nan" in str(e):
                logger.warning(f"Sampling failed ({e}), retrying with greedy decoding...")
                output = pipe.generate(
                    input_ids=inputs.input_ids,
                    max_new_tokens=max_tokens,
                    do_sample=False,
                    pad_token_id=tokenizer.eos_token_id,
                    eos_token_id=tokenizer.eos_token_id,
                )
            else:
                raise
        generated_ids = output[:, inputs.input_ids.shape[1]:]
        return tokenizer.batch_decode(generated_ids, skip_special_tokens=True)[0]


async def _stream_generate(prompt, request, max_tokens, request_id, created, model_name):
    if not model_lock.acquire(blocking=False):
        yield f"data: {json.dumps({'error': 'Model is busy'})}\n\n"
        return

    try:
        if LLM_USE_OV_GENAI:
            from utils.ov_genai_util import YieldingTextStreamer

            streamer = YieldingTextStreamer(tokenizer)

            def run():
                try:
                    pipe.generate(
                        prompt,
                        streamer=streamer,
                        max_new_tokens=max_tokens,
                        temperature=request.temperature or 0.7,
                        do_sample=request.do_sample if request.do_sample is not None else True,
                    )
                except Exception as e:
                    logger.error(f"Stream generation error: {e}")
                finally:
                    streamer.end()

            threading.Thread(target=run, daemon=True).start()
            for token in streamer:
                chunk = {
                    "id": request_id,
                    "object": "chat.completion.chunk",
                    "created": created,
                    "model": model_name,
                    "choices": [{"index": 0, "delta": {"content": token}, "finish_reason": None}],
                }
                yield f"data: {json.dumps(chunk)}\n\n"
        else:
            from transformers import TextIteratorStreamer

            streamer = TextIteratorStreamer(tokenizer, skip_special_tokens=True, skip_prompt=True)
            inputs = tokenizer(prompt, return_tensors="pt")

            def run():
                try:
                    pipe.generate(
                        input_ids=inputs.input_ids,
                        max_new_tokens=max_tokens,
                        do_sample=request.do_sample if request.do_sample is not None else True,
                        temperature=max(request.temperature or 0.7, 0.1),
                        top_p=request.top_p or 0.9,
                        top_k=request.top_k or 50,
                        pad_token_id=tokenizer.eos_token_id,
                        eos_token_id=tokenizer.eos_token_id,
                        streamer=streamer,
                    )
                except ValueError as e:
                    if "inf" in str(e) or "nan" in str(e):
                        logger.warning(f"Sampling failed ({e}), retrying with greedy decoding...")
                        pipe.generate(
                            input_ids=inputs.input_ids,
                            max_new_tokens=max_tokens,
                            do_sample=False,
                            pad_token_id=tokenizer.eos_token_id,
                            eos_token_id=tokenizer.eos_token_id,
                            streamer=streamer,
                        )
                    else:
                        logger.error(f"Stream generation error: {e}")
                except Exception as e:
                    logger.error(f"Stream generation error: {e}")

            threading.Thread(target=run, daemon=True).start()
            for token in streamer:
                chunk = {
                    "id": request_id,
                    "object": "chat.completion.chunk",
                    "created": created,
                    "model": model_name,
                    "choices": [{"index": 0, "delta": {"content": token}, "finish_reason": None}],
                }
                yield f"data: {json.dumps(chunk)}\n\n"

        final = {
            "id": request_id,
            "object": "chat.completion.chunk",
            "created": created,
            "model": model_name,
            "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}],
        }
        yield f"data: {json.dumps(final)}\n\n"
        yield "data: [DONE]\n\n"
    finally:
        model_lock.release()


@app.get("/health")
async def health_check():
    if model_ready:
        return JSONResponse(status_code=200, content={"status": "healthy", "model": os.path.basename(LLM_MODEL_PATH)})
    return JSONResponse(status_code=503, content={"status": "model not ready"})
