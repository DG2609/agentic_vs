from langchain_core.tools import tool


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
