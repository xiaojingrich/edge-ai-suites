"""
Orchestrator — Central orchestration layer for all chat interactions.

Replaces OpenClaw's routing+orchestration role when running locally.
Manages conversation lifecycle, intent classification, and agent dispatch.

Adding a new agent:
    1. Create the agent handler (async function or method)
    2. Register it: orchestrator.register_handler("agent_name", handler)
    3. Add keyword/LLM patterns in intent_router.py
"""

import json
import logging
from typing import Callable, Optional

from fastapi.responses import StreamingResponse, JSONResponse

from components.intent_router import IntentRouter, RoutingResult
from components.report_agent.conversation import ConversationManager
from dto.report_dto import AgentChatRequest

logger = logging.getLogger(__name__)

MAX_HISTORY_CHARS = 4000


class Orchestrator:
    """
    Central orchestration layer.

    Stateless per-request — safe for concurrent use as a singleton.
    """

    def __init__(self):
        self._handlers: dict[str, Callable] = {}
        self._register_default_handlers()

    def register_handler(self, agent_name: str, handler: Callable) -> None:
        """Register a handler for an agent type."""
        self._handlers[agent_name] = handler
        logger.info(f"[Orchestrator] Registered handler for agent: {agent_name}")

    async def handle_chat(self, request: AgentChatRequest):
        """
        Main entry point for /chat.

        1. Resolves session
        2. Manages conversation
        3. Classifies intent
        4. Dispatches to handler
        """
        session_id = self._resolve_session(request)
        conversation_id, conv_manager = self._manage_conversation(session_id, request.conversation_id)

        conv_manager.add_message(conversation_id, "user", request.message)

        # If output_format already set (e.g., from OpenClaw), skip classification
        if request.output_format:
            request.conversation_id = conversation_id
            request.session_id = session_id
            return await self._dispatch("report", request, conversation_id, conv_manager)

        # Classify intent
        routing = self._classify_intent(request.message)

        # Dispatch
        request.conversation_id = conversation_id
        request.session_id = session_id
        if routing:
            request.output_format = routing.output_format
            return await self._dispatch(routing.agent, request, conversation_id, conv_manager)
        else:
            return await self._dispatch("report", request, conversation_id, conv_manager)

    def _resolve_session(self, request: AgentChatRequest) -> str:
        if request.session_id:
            return request.session_id
        from utils.session_manager import get_latest_session_id
        return get_latest_session_id() or "default"

    def _manage_conversation(self, session_id: str, conversation_id: Optional[str]) -> tuple:
        conv_manager = ConversationManager(session_id)
        if conversation_id:
            if conv_manager.get_conversation(conversation_id) is None:
                conversation_id = conv_manager.create_conversation()
        else:
            conversation_id = conv_manager.create_conversation()
        return conversation_id, conv_manager

    def _classify_intent(self, message: str) -> Optional[RoutingResult]:
        from utils.config_loader import config as app_config

        router_config = getattr(app_config, 'router', None)
        router_enabled = getattr(router_config, 'enabled', False) if router_config else False

        if not router_enabled:
            return None

        router_mode = getattr(router_config, 'mode', 'keyword')
        model = self._get_model()
        intent_router = IntentRouter(mode=router_mode, model=model if router_mode == "llm" else None)
        return intent_router.route(message)

    async def _dispatch(self, agent: str, request: AgentChatRequest,
                        conversation_id: str, conv_manager: ConversationManager):
        handler = self._handlers.get(agent)
        if handler:
            return await handler(request, conversation_id, conv_manager)
        return JSONResponse(
            content={"error": f"Agent '{agent}' is not yet implemented."},
            status_code=501,
        )

    # --- Built-in handlers ---

    async def _handle_general(self, request: AgentChatRequest,
                              conversation_id: str, conv_manager: ConversationManager):
        model = self._get_model()
        if model is None:
            return JSONResponse(content={"error": "LLM model not loaded."}, status_code=503)

        chat_history = conv_manager.get_chat_history(conversation_id)
        return StreamingResponse(
            self._general_chat_stream(request.message, model, chat_history, conversation_id, conv_manager),
            media_type="application/json",
        )

    async def _handle_report(self, request: AgentChatRequest,
                             conversation_id: str, conv_manager: ConversationManager):
        from api.endpoints import agent_chat
        return await agent_chat(request)

    # --- Utilities ---

    def _get_model(self):
        from components.summarizer_component import SummarizerComponent
        return SummarizerComponent._model

    def _trim_history(self, chat_history: list) -> list:
        recent = chat_history[-10:]
        trimmed = []
        total_chars = 0
        for msg in reversed(recent):
            content = msg["content"]
            if total_chars + len(content) > MAX_HISTORY_CHARS:
                remaining = MAX_HISTORY_CHARS - total_chars
                if remaining > 100:
                    trimmed.insert(0, {"role": msg["role"], "content": content[-remaining:]})
                break
            trimmed.insert(0, {"role": msg["role"], "content": content})
            total_chars += len(content)
        return trimmed

    async def _general_chat_stream(self, message: str, model, chat_history: list,
                                   conversation_id: str, conv_manager: ConversationManager):
        messages = [
            {"role": "system", "content": "You are a helpful classroom assistant. Answer the user's question concisely."},
        ]

        trimmed = self._trim_history(chat_history)
        for msg in trimmed:
            messages.append(msg)

        if not chat_history or chat_history[-1].get("content") != message:
            messages.append({"role": "user", "content": message})

        prompt = model.tokenizer.apply_chat_template(
            messages,
            tokenize=False,
            add_generation_prompt=True,
        )
        streamer = model.generate(prompt, stream=True)
        full_response = ""
        for token in streamer:
            full_response += token
            yield json.dumps({"token": token, "conversation_id": conversation_id}) + "\n"

        conv_manager.add_message(conversation_id, "assistant", full_response)

    def _register_default_handlers(self):
        self._handlers["general"] = self._handle_general
        self._handlers["report"] = self._handle_report


# Module-level singleton
orchestrator = Orchestrator()
