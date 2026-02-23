"""
LangGraph agent state definition using Pydantic + Annotated reducers.
"""
from typing import Annotated
from pydantic import BaseModel, Field
from langchain_core.messages import AnyMessage
from langgraph.graph.message import add_messages


class AgentState(BaseModel):
    """
    State schema for the LangGraph agent.

    Uses Annotated with add_messages reducer so that parallel tool results
    are correctly merged into the message list.
    """
    model_config = {"arbitrary_types_allowed": True}

    # Core message history — uses add_messages reducer for safe parallel updates
    messages: Annotated[list[AnyMessage], add_messages] = Field(default_factory=list)

    # Summary of older messages (populated when conversation gets long)
    summary: str = ""

    # Current working directory for file operations
    workspace: str = ""

    # Auto-generated conversation title
    title: str = ""

    # Todo list for session task tracking
    todos: list[dict] = []

    # Agent Mode ('chat', 'plan', 'code')
    mode: str = "code"
    
    # Active Agent in Swarm ('planner', 'coder')
    active_agent: str = "planner"

