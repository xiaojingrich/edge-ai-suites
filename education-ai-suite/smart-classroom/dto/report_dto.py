from pydantic import BaseModel
from typing import Optional, List


class ReportRequest(BaseModel):
    session_id: str
    query: Optional[str] = None


class ChatMessage(BaseModel):
    role: str  # "user" or "assistant"
    content: str


class AgentChatRequest(BaseModel):
    session_id: Optional[str] = None  # None = use latest session
    message: str  # User's current message
    conversation_id: Optional[str] = None  # For multi-turn, None = new conversation
    output_format: Optional[str] = None  # "report" or "chat", from OpenClaw routing. None = agent decides.