"""
Question tool — ask the user for clarification mid-task.
Inspired by OpenCode's Question tool.

Uses asyncio.Event to pause agent execution until user responds.
The WebSocket handler in main.py routes answers back.
"""
import asyncio
import logging
from typing import Optional
from langchain_core.tools import tool
from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)

# Shared state for pending questions (keyed by thread_id)
_pending_questions: dict[str, dict] = {}
_answer_events: dict[str, asyncio.Event] = {}


class QuestionArgs(BaseModel):
    text: str = Field(description="The question to ask the user.")
    options: list[str] = Field(
        default_factory=list,
        description="Optional list of answer choices. If empty, user types free-form answer.",
    )
    multiple: bool = Field(
        default=False,
        description="If true, user can select multiple options.",
    )


def get_pending_question(thread_id: str) -> Optional[dict]:
    """Get the pending question for a thread (called by main.py)."""
    return _pending_questions.get(thread_id)


def submit_answer(thread_id: str, answer: str):
    """Submit user's answer (called by main.py when user responds)."""
    if thread_id in _pending_questions:
        _pending_questions[thread_id]["answer"] = answer
        if thread_id in _answer_events:
            _answer_events[thread_id].set()
        logger.info(f"Answer received for thread {thread_id}: {answer}")


@tool(args_schema=QuestionArgs)
async def question(text: str, options: list[str] = [], multiple: bool = False) -> str:
    """Ask the user a question and wait for their response.

    Use this when you need to:
    1. Clarify ambiguous instructions
    2. Get user preferences or decisions
    3. Offer implementation choices
    4. Confirm before destructive actions

    If options are provided, user picks from the list.
    If no options, user types a free-form answer.

    Args:
        text: The question to ask.
        options: Optional answer choices. Don't include "Other" — a custom input is always available.
        multiple: Allow selecting multiple options.
    """
    # For now, since we can't pause the LangGraph execution mid-stream,
    # we return a formatted question that the agent includes in its response.
    # The user answers in the next message.
    parts = [f"**Question:** {text}"]

    if options:
        parts.append("")
        for i, opt in enumerate(options, 1):
            parts.append(f"  {i}. {opt}")
        if multiple:
            parts.append("\n*(Select one or more options, or type your own answer)*")
        else:
            parts.append("\n*(Select an option or type your own answer)*")
    else:
        parts.append("\n*(Please type your answer)*")

    return "\n".join(parts)
