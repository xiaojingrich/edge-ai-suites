"""
Conversation manager for multi-turn Agent chat.
"""

import os
import json
import time
import logging
from typing import Optional
from utils.runtime_config_loader import RuntimeConfig
from utils.storage_manager import StorageManager

logger = logging.getLogger(__name__)


class ConversationManager:
    """
    Manages multi-turn chat conversations with the Report Agent.

    Each conversation stores:
    - Chat history (user + assistant messages)
    - Agent's collected observations (persisted across turns)
    - Conversation metadata
    """

    def __init__(self, session_id: str):
        self.session_id = session_id
        project_config = RuntimeConfig.get_section("Project")
        self.conversations_dir = os.path.join(
            project_config.get("location"),
            project_config.get("name"),
            session_id,
            ".conversations",
        )
        os.makedirs(self.conversations_dir, exist_ok=True)

    def create_conversation(self) -> str:
        """Create a new conversation, return conversation_id."""
        conversation_id = f"conv_{int(time.time())}_{os.urandom(4).hex()}"
        conv_path = self._get_conv_path(conversation_id)

        data = {
            "conversation_id": conversation_id,
            "session_id": self.session_id,
            "created_at": time.strftime("%Y-%m-%d %H:%M:%S"),
            "messages": [],
            "agent_observations": [],
        }

        StorageManager.save(conv_path, json.dumps(data, ensure_ascii=False, indent=2), append=False)
        return conversation_id

    def get_conversation(self, conversation_id: str) -> Optional[dict]:
        """Load a conversation by ID."""
        conv_path = self._get_conv_path(conversation_id)
        if not os.path.exists(conv_path):
            return None

        content = StorageManager.read_text_file(conv_path)
        try:
            return json.loads(content)
        except json.JSONDecodeError:
            return None

    def add_message(self, conversation_id: str, role: str, content: str) -> None:
        """Add a message to the conversation history."""
        conv = self.get_conversation(conversation_id)
        if conv is None:
            return

        conv["messages"].append({
            "role": role,
            "content": content,
            "timestamp": time.strftime("%Y-%m-%d %H:%M:%S"),
        })

        self._save_conversation(conversation_id, conv)

    def add_observations(self, conversation_id: str, observations: list) -> None:
        """Persist agent observations for use in follow-up turns."""
        conv = self.get_conversation(conversation_id)
        if conv is None:
            return

        existing = set(conv.get("agent_observations", []))
        for obs in observations:
            if obs not in existing:
                conv["agent_observations"].append(obs)

        self._save_conversation(conversation_id, conv)

    def get_chat_history(self, conversation_id: str) -> list:
        """Get the message history for building LLM context."""
        conv = self.get_conversation(conversation_id)
        if conv is None:
            return []
        return conv.get("messages", [])

    def get_observations(self, conversation_id: str) -> list:
        """Get previously collected observations."""
        conv = self.get_conversation(conversation_id)
        if conv is None:
            return []
        return conv.get("agent_observations", [])

    def _get_conv_path(self, conversation_id: str) -> str:
        return os.path.join(self.conversations_dir, f"{conversation_id}.json")

    def _save_conversation(self, conversation_id: str, data: dict) -> None:
        conv_path = self._get_conv_path(conversation_id)
        StorageManager.save(conv_path, json.dumps(data, ensure_ascii=False, indent=2), append=False)