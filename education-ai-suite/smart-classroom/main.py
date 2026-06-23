import os
os.environ.setdefault("GLOG_minloglevel", "2")
os.environ.setdefault("FLAGS_minloglevel", "2")

import sys
from utils import system_checker

from utils.logger_config import setup_logger
setup_logger()

from fastapi import FastAPI
from api.endpoints import register_routes
from api.llm_serving import register_llm_routes
from utils.runtime_config_loader import RuntimeConfig
from utils.ensure_model import ensure_model, get_model_path
from utils.preload_models import preload_models
import logging
from fastapi.middleware.cors import CORSMiddleware
import os
import subprocess
import signal
from fastapi.staticfiles import StaticFiles
from starlette.responses import FileResponse
from pathlib import Path
from components.va.media_service import MediaService
from utils.config_loader import config
from mcp_server.server import mcp as mcp_server
import threading
import uvicorn


logger = logging.getLogger(__name__)

app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],   # For Testing ["*"]
    allow_credentials=True,          # cookies/auth allowed
    allow_methods=["*"],             # allow all HTTP methods
    allow_headers=["*"],             # allow all headers
    expose_headers=["x-session-id"]  # expose custom headers if needed
)

register_routes(app)
register_llm_routes(app)

def system_check():
    if (not system_checker.check_system_requirements()) and (not system_checker.show_warning_and_prompt_user_to_continue()):
        sys.exit(1)


LLM_SERVICE_PORT = int(os.environ.get("LLM_SERVICE_PORT", "9905"))
_llm_service_process = None


def start_llm_service():
    """Start the LLM serving process as a subprocess."""
    global _llm_service_process

    model_path = get_model_path()
    device = config.models.summarizer.device
    max_new_tokens = str(config.models.summarizer.max_new_tokens)
    use_ov_genai = "true" if config.app.use_ov_genai else "false"

    env = os.environ.copy()
    env["LLM_MODEL_PATH"] = model_path
    env["LLM_DEVICE"] = device
    env["LLM_PORT"] = str(LLM_SERVICE_PORT)
    env["LLM_MAX_NEW_TOKENS"] = max_new_tokens
    env["LLM_USE_OV_GENAI"] = use_ov_genai

    cmd = [
        sys.executable, "-m", "uvicorn",
        "services.llm_serving.app:app",
        "--host", "127.0.0.1",
        "--port", str(LLM_SERVICE_PORT),
    ]

    logger.info(f"Starting LLM service on port {LLM_SERVICE_PORT} (model: {model_path}, device: {device})")
    _llm_service_process = subprocess.Popen(
        cmd,
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1,
    )

    def _log_output():
        for line in _llm_service_process.stdout:
            logger.info(f"[LLM Service] {line.rstrip()}")

    threading.Thread(target=_log_output, daemon=True).start()
    logger.info(f"LLM service process started (pid={_llm_service_process.pid})")


def stop_llm_service():
    """Stop the LLM service subprocess."""
    global _llm_service_process
    if _llm_service_process and _llm_service_process.poll() is None:
        logger.info("Stopping LLM service...")
        _llm_service_process.terminate()
        try:
            _llm_service_process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            _llm_service_process.kill()
        logger.info("LLM service stopped.")
    _llm_service_process = None


if __name__ == "__main__":

    #system_check()
    RuntimeConfig.ensure_config_exists()

    # Start the persistent LLM serving subprocess (port 9905) before preloading,
    # so the OpenVINO model loads in parallel while other models are prepared.
    start_llm_service()

    preload_models()

    media_service = MediaService()
    media_service.launch_server()

    # Start MCP server
    MCP_PORT = int(os.environ.get("MCP_SERVER_PORT", "8100"))
    threading.Thread(
        target=lambda: mcp_server.run(transport="sse"),
        daemon=True,
    ).start()
    logger.info(f"MCP server started on port {MCP_PORT}")

    def _cleanup(signum, frame):
        stop_llm_service()
        sys.exit(0)

    signal.signal(signal.SIGINT, _cleanup)
    signal.signal(signal.SIGTERM, _cleanup)

    logger.info("App started, Starting Server...")
    uvicorn.run("main:app", host="127.0.0.1", port=8000, reload=False)
