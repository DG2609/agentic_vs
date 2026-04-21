from langchain_core.tools import tool


@tool
def say_hi(name: str) -> str:
    """Say hi."""
    return f"hi {name}"


__skill_tools__ = [say_hi]
