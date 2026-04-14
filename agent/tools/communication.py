"""
Tools: communication — reply_to_user for sending messages, and request_user_input
for asking the user a clarifying question mid-task.
"""
import sys
from langchain_core.tools import tool
from pydantic import BaseModel, Field

from agent.tools.truncation import truncate_output


@tool
def reply_to_user(message: str) -> str:
    """Send a message back to the user. Use this tool for ALL communication.

    You MUST use this tool whenever you want to:
    - Greet the user (e.g. "Hi", "Hello")
    - Answer a question
    - Explain what you found after using other tools
    - Report progress or results
    - Ask for clarification

    The message should be formatted in Markdown for rich display.

    Args:
        message: The text message to display to the user. Supports Markdown.

    Returns:
        Confirmation that the message was delivered.
    """
    return "[Message delivered]"


class RequestUserInputArgs(BaseModel):
    question: str = Field(description="The question to ask the user.")
    context: str = Field(default="", description="Optional context explaining why this information is needed.")


@tool(args_schema=RequestUserInputArgs)
def request_user_input(question: str, context: str = "") -> str:
    """Ask the user a clarifying question and wait for their response.

    Use this when you need information from the user to proceed (e.g., preferences,
    missing context, approval for a risky action). In headless/CI mode (no TTY),
    returns a message that the user is unavailable rather than blocking.

    Args:
        question: The question to ask the user.
        context: Optional context explaining why this information is needed.

    Returns:
        The user's answer, or an unavailability notice in non-interactive mode.
    """
    if not sys.stdin.isatty():
        return f"[User input unavailable — not running in an interactive terminal] Question was: {question}"

    prompt = f"\n❓ {question}"
    if context:
        prompt = f"\n📋 Context: {context}\n{prompt}"
    prompt += "\n> "

    try:
        answer = input(prompt).strip()
        return answer if answer else "(no response)"
    except (EOFError, KeyboardInterrupt):
        return "(user cancelled)"
