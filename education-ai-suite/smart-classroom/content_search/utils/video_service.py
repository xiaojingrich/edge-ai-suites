#
# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0
#

import os
import httpx
import json
import traceback
from typing import Optional


DEFAULT_VIDEO_SUMMARY_PROMPTS = {
    "en": (
        "These images are consecutive frames from the same video chunk. "
        "Write one concise English chunk summary that covers key actions/objects/changes "
        "and any text visible on a blackboard, whiteboard, or slides (mention only if present), "
        "avoids repetition, and describes notable changes in order if present."
    ),
    "zh": (
        "以下图像是同一段视频片段中的连续帧。"
        "请用简体中文写一段简洁的整体总结，涵盖关键动作/物体/变化，"
        "以及黑板、白板或幻灯片上的文字（仅在出现时提及），"
        "避免重复，如有明显变化请按顺序描述。"
    ),
}


def get_default_video_summary_prompt() -> str:
    language = os.getenv("APP_LANGUAGE", "en").lower()
    return DEFAULT_VIDEO_SUMMARY_PROMPTS.get(language, DEFAULT_VIDEO_SUMMARY_PROMPTS["en"])

class VideoService:
    def __init__(self):
        host = os.getenv("PREPROCESS_HOST", "127.0.0.1")
        port = os.getenv("PREPROCESS_PORT", "8001")
        self.base_url = f"http://{host}:{port}"
        self.timeout = 900.0

    async def trigger_summarization(
        self,
        file_key: str,
        bucket_name: str,
        tags: list = None,
        prompt: Optional[str] = None,
        chunk_duration: int = None,
        run_id: str = None
    ):
        url = f"{self.base_url}/preprocess"

        payload = {
            "file_key": file_key,
            "reuse_existing": True,
            "tags": tags
        }

        payload["prompt"] = prompt or get_default_video_summary_prompt()

        if chunk_duration is not None:
            payload["chunk_duration_s"] = chunk_duration

        if run_id is not None:
            payload["run_id"] = run_id

        print(f"[VideoService] Calling -> {url}")
        print(f"[VideoService] Payload: {json.dumps(payload, ensure_ascii=False)}")

        try:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                async with client.stream("POST", url, json=payload) as response:
                    if response.status_code != 200:
                        content = await response.aread()
                        return {"error": f"HTTP {response.status_code}", "detail": content.decode()}

                    last_data = {}
                    async for line in response.aiter_lines():
                        if line.strip():
                            try:
                                chunk_data = json.loads(line)
                                if chunk_data.get("type") == "chunk":
                                    print(f"  > Processing {chunk_data.get('chunk_id')}...")
                                last_data = chunk_data
                            except:
                                continue
                    return last_data

        except Exception as e:
            traceback.print_exc()
            return {"error": "Connection failed", "message": str(e)}

video_service = VideoService()