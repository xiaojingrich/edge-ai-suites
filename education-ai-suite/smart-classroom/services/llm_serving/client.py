"""
LLM Service Client — Drop-in replacement for local Summarizer.

Provides the same interface (generate, acquire_model, release_model, tokenizer)
but delegates inference to the persistent LLM serving process via HTTP.
"""

import json
import logging
import os
import threading
import time
from urllib.parse import urlparse
from queue import Queue, Empty
from typing import Iterator

import requests
from transformers import AutoTokenizer

logger = logging.getLogger(__name__)

LLM_SERVICE_URL = os.environ.get("LLM_SERVICE_URL", "http://127.0.0.1:9905")


class LLMClientStreamer:
    """Iterator that yields tokens from the streaming HTTP response."""

    def __init__(self):
        self._queue: Queue = Queue()
        self._done = False
        self.total_tokens = 0

    def put(self, token: str):
        self.total_tokens += 1
        self._queue.put(token)

    def end(self):
        self._done = True
        self._queue.put(None)

    def __iter__(self):
        return self

    def __next__(self):
        while True:
            try:
                token = self._queue.get(timeout=300)
            except Empty:
                raise StopIteration
            if token is None:
                raise StopIteration
            return token


class LLMServiceClient:
    """
    HTTP client to the LLM serving service.

    Mimics the Summarizer interface so it can be used as a drop-in replacement
    in SummarizerComponent and llm_serving routes.
    """

    def __init__(self, model_path: str, base_url: str = None):
        self.base_url = base_url or LLM_SERVICE_URL
        self.model_path = model_path
        self.model_name = os.path.basename(model_path)
        self._session = requests.Session()
        # Local health and inference calls must bypass corporate/system proxies.
        parsed = urlparse(self.base_url)
        if parsed.hostname in {"127.0.0.1", "localhost", "::1"}:
            self._session.trust_env = False
        self.tokenizer = AutoTokenizer.from_pretrained(
            model_path, trust_remote_code=True, fix_mistral_regex=True
        )
        if self.tokenizer.pad_token is None:
            self.tokenizer.pad_token = self.tokenizer.eos_token

    def acquire_model(self):
        return self

    def release_model(self):
        pass

    def generate(self, prompt: str, stream: bool = True, max_new_tokens: int = None,
                 temperature: float = 0.7, top_p: float = 0.9, top_k: int = 50,
                 do_sample: bool = True):
        """
        Generate text via the LLM service.

        Args:
            prompt: The formatted prompt string (already processed by tokenizer.apply_chat_template).
            stream: If True, returns an iterator yielding tokens.
            max_new_tokens: Max tokens to generate.
            temperature: Sampling temperature.

        Returns:
            If stream=True: an iterator with .total_tokens attribute.
            If stream=False: the generated text as a string.
        """
        url = f"{self.base_url}/v1/chat/completions"

        payload = {
            "messages": [{"role": "user", "content": prompt}],
            "temperature": temperature,
            "max_tokens": max_new_tokens,
            "stream": stream,
            "top_p": top_p,
            "top_k": top_k,
            "do_sample": do_sample,
            "raw_prompt": True,
        }

        if stream:
            return self._stream_request(url, payload)
        else:
            return self._sync_request(url, payload)

    def _sync_request(self, url: str, payload: dict) -> str:
        payload["stream"] = False
        try:
            resp = self._session.post(url, json=payload, timeout=600)
            if resp.status_code == 429:
                logger.warning("LLM service busy, retrying in 2s...")
                time.sleep(2)
                resp = self._session.post(url, json=payload, timeout=600)

            if resp.status_code != 200:
                logger.error(f"LLM service error: {resp.status_code} {resp.text}")
                return f"[ERROR]: LLM service returned status {resp.status_code}"

            data = resp.json()
            return data["choices"][0]["message"]["content"]
        except requests.exceptions.ConnectionError:
            logger.error(f"Cannot connect to LLM service at {url}")
            return "[ERROR]: LLM service unavailable"
        except Exception as e:
            logger.error(f"LLM request failed: {e}")
            return f"[ERROR]: {e}"

    def _stream_request(self, url: str, payload: dict) -> LLMClientStreamer:
        payload["stream"] = True
        streamer = LLMClientStreamer()

        def _run():
            try:
                resp = self._session.post(url, json=payload, timeout=600, stream=True)
                if resp.status_code != 200:
                    logger.error(f"LLM stream error: {resp.status_code}")
                    streamer.put(f"[ERROR]: LLM service returned status {resp.status_code}")
                    streamer.end()
                    return

                for line in resp.iter_lines(decode_unicode=True):
                    if not line:
                        continue
                    if line.startswith("data: "):
                        data_str = line[6:]
                        if data_str == "[DONE]":
                            break
                        try:
                            chunk = json.loads(data_str)
                            if "error" in chunk:
                                streamer.put(f"[ERROR]: {chunk['error']}")
                                break
                            delta = chunk.get("choices", [{}])[0].get("delta", {})
                            content = delta.get("content", "")
                            if content:
                                streamer.put(content)
                        except json.JSONDecodeError:
                            continue
            except requests.exceptions.ConnectionError:
                logger.error(f"Cannot connect to LLM service at {url}")
                streamer.put("[ERROR]: LLM service unavailable")
            except Exception as e:
                logger.error(f"LLM stream request failed: {e}")
                streamer.put(f"[ERROR]: {e}")
            finally:
                streamer.end()

        threading.Thread(target=_run, daemon=True).start()
        return streamer

    def wait_until_ready(self, timeout: float = 600) -> bool:
        """Block until the LLM service is healthy or timeout."""
        deadline = time.monotonic() + timeout
        url = f"{self.base_url}/health"
        while time.monotonic() < deadline:
            try:
                resp = self._session.get(url, timeout=5)
                if resp.status_code == 200:
                    logger.info("LLM service is ready.")
                    return True
                logger.warning("LLM service health check returned %s", resp.status_code)
            except requests.exceptions.ConnectionError:
                pass
            time.sleep(2)
        logger.error(f"LLM service not ready after {timeout}s")
        return False
