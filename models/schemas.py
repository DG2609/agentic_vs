"""
Pydantic data models for API requests/responses and tool results.
"""
from datetime import datetime
from pydantic import BaseModel, Field


class ChatMessage(BaseModel):
    """A single chat message from the API."""
    role: str = Field(description="Message role: user, assistant, system, tool")
    content: str = Field(description="Message content")
    timestamp: datetime = Field(default_factory=datetime.now)


class ChatRequest(BaseModel):
    """Incoming chat request from the frontend."""
    message: str = Field(description="User message text")
    thread_id: str = Field(default="default", description="Conversation thread ID")


class ToolExecution(BaseModel):
    """Represents a tool execution event for the UI."""
    tool_name: str
    tool_id: str
    status: str = "running"  # running | completed | error
    arguments: dict = Field(default_factory=dict)
    result: str = ""
    duration_ms: float = 0


class StreamChunk(BaseModel):
    """A chunk of streamed response to the frontend."""
    type: str  # "text" | "tool_start" | "tool_end" | "done" | "error"
    content: str = ""
    tool: ToolExecution | None = None


class ThreadInfo(BaseModel):
    """Metadata about a conversation thread."""
    thread_id: str
    title: str = "New Chat"
    created_at: datetime = Field(default_factory=datetime.now)
    message_count: int = 0
