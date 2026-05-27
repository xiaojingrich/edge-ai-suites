"""
Base class for all Agent Skills.

A Skill is a higher-level capability that composes multiple tools and
may invoke LLM reasoning to produce structured analysis results.
Unlike tools (which do raw data retrieval), skills perform inference and
return analytical conclusions.
"""

from abc import ABC, abstractmethod
from typing import Any


class BaseSkill(ABC):
    """Base class for agent skills."""

    name: str = "base_skill"
    description: str = "Base skill"

    def __init__(self, session_id: str, tools, model=None):
        self.session_id = session_id
        self.tools = tools  # ToolRegistry instance for data access
        self.model = model  # LLM model for reasoning (optional)

    @abstractmethod
    def execute(self, context: dict = None) -> dict:
        """
        Execute the skill and return structured results.

        Args:
            context: Optional context from previous skill results or user params

        Returns:
            dict with keys:
                - "status": "success" | "partial" | "unavailable"
                - "result": The skill's output (analysis, data, etc.)
                - "summary": One-line summary of what was produced
        """
        raise NotImplementedError

    def _call_llm(self, prompt: str) -> str:
        """Helper to call LLM synchronously for skill-level reasoning."""
        if self.model is None:
            return ""
        from utils.config_loader import config
        messages = [
            {"role": "system", "content": "You are a concise analytical assistant. Respond with structured analysis only."},
            {"role": "user", "content": prompt},
        ]
        full_prompt = self.model.tokenizer.apply_chat_template(
            messages, tokenize=False, add_generation_prompt=True
        )
        return self.model.generate(full_prompt, stream=False)