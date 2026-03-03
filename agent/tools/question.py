"""
Question tool — ask the user for clarification mid-task.

Uses LangGraph interrupt() (>= 0.2.57) to pause graph execution until the
user responds via the 'agent:resume' Socket.IO event.

Falls back to returning a formatted question string if LangGraph interrupt
is not available (older versions).
"""
import logging
from langchain_core.tools import tool
from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)


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


def _format_question(text: str, options: list[str], multiple: bool) -> str:
    """Format question for display."""
    parts = [f"**Question:** {text}"]
    if options:
        parts.append("")
        for i, opt in enumerate(options, 1):
            parts.append(f"  {i}. {opt}")
        hint = ("*(Select one or more options, or type your own answer)*"
                if multiple else "*(Select an option or type your own answer)*")
        parts.append(f"\n{hint}")
    else:
        parts.append("\n*(Please type your answer)*")
    return "\n".join(parts)


@tool(args_schema=QuestionArgs)
async def question(text: str, options: list[str] = [], multiple: bool = False) -> str:
    """Ask the user a question and wait for their response before continuing.

    Use this when you need to:
    1. Clarify ambiguous instructions before starting work
    2. Get user preferences or decisions at a fork point
    3. Offer implementation choices (e.g., library A vs library B)
    4. Confirm before destructive actions (file deletion, db drop, force-push)

    If options are provided, user picks from the list.
    If no options, user types a free-form answer.

    **Important:** After calling this tool, execution pauses until the user
    responds. The user's answer is returned as a string.

    Args:
        text: The question to ask.
        options: Optional answer choices (1-6 items).
        multiple: Allow selecting multiple options (only with options list).

    Returns:
        The user's answer as a string.
    """
    display_text = _format_question(text, options, multiple)

    try:
        from langgraph.types import interrupt as _lg_interrupt
        # Pause graph execution. Resumes when server calls Command(resume=answer).
        payload = {
            "text": text,
            "options": options,
            "multiple": multiple,
            "display": display_text,
        }
        answer = _lg_interrupt(payload)
        return str(answer)
    except ImportError:
        # Older LangGraph: return formatted question; user answers in next message.
        logger.debug("LangGraph interrupt not available; returning question as text.")
        return display_text + "\n\n*(Please answer in your next message)*"
