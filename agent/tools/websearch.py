"""
Web Search tool — search the web for documentation, solutions, and current info.
Uses DuckDuckGo (free, no API key needed).
"""
import logging
from langchain_core.tools import tool
from pydantic import BaseModel, Field
from agent.tools.truncation import truncate_output

logger = logging.getLogger(__name__)

# Check availability at module load — avoids wasting a tool call at runtime
try:
    from duckduckgo_search import DDGS as _DDGS
    _DDGS_AVAILABLE = True
except ImportError:
    _DDGS = None  # type: ignore
    _DDGS_AVAILABLE = False
    logger.warning(
        "[web_search] duckduckgo_search not installed — web_search tool will "
        "report unavailable. Run: pip install duckduckgo_search"
    )


class WebSearchArgs(BaseModel):
    query: str = Field(description="Search query.")
    num_results: int = Field(default=5, ge=1, le=20, description="Number of results.")


@tool(args_schema=WebSearchArgs)
def web_search(query: str, num_results: int = 5) -> str:
    """Search the web for information, documentation, or solutions.

    Use this when you need:
    - Current/up-to-date information beyond your training data
    - Documentation for libraries or frameworks
    - Solutions to specific error messages
    - Best practices and community recommendations

    The current year should be included in queries about recent events.

    Args:
        query: Search query string.
        num_results: Number of results to return (1-20).
    """
    if not _DDGS_AVAILABLE:
        return (
            "Error: duckduckgo_search is not installed. "
            "Run: pip install duckduckgo_search\n"
            "Then restart the server."
        )

    try:
        with _DDGS() as ddgs:
            # Try default backend first
            results = list(ddgs.text(query, max_results=num_results))
            
            # Fallback to 'lite' backend if no results (often fixes region/language issues)
            if not results:
                logger.info(f"No results with default backend for '{query}', trying 'lite' backend")
                try:
                    results = list(ddgs.text(query, backend="lite", max_results=num_results))
                except Exception as e:
                    logger.warning(f"Lite backend fallback failed: {e}")

        if not results:
            return f"No results found for: {query}"

        lines = [f"🔍 Search results for: {query}\n"]
        for i, r in enumerate(results, 1):
            title = r.get("title", "No title")
            href = r.get("href", "")
            body = r.get("body", "")[:200]
            lines.append(f"{i}. **{title}**")
            lines.append(f"   {href}")
            lines.append(f"   {body}")
            lines.append("")

        return truncate_output("\n".join(lines))

    except Exception as e:
        logger.error(f"Web search failed: {e}")
        return f"Search failed: {str(e)}"
