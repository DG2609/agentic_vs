"""
Tool: webfetch — read content from URLs.
Strips HTML to extract readable text for LLM consumption.
"""
import re
import requests
from langchain_core.tools import tool
import config
from agent.tools.truncation import truncate_output
from models.tool_schemas import WebFetchArgs


def _html_to_text(html: str) -> str:
    """Extract readable text from HTML by stripping tags."""
    # Remove script and style blocks entirely
    text = re.sub(r'<script[^>]*>.*?</script>', '', html, flags=re.DOTALL | re.IGNORECASE)
    text = re.sub(r'<style[^>]*>.*?</style>', '', text, flags=re.DOTALL | re.IGNORECASE)
    text = re.sub(r'<nav[^>]*>.*?</nav>', '', text, flags=re.DOTALL | re.IGNORECASE)
    text = re.sub(r'<footer[^>]*>.*?</footer>', '', text, flags=re.DOTALL | re.IGNORECASE)
    # Convert common block elements to newlines
    text = re.sub(r'<(?:br|hr)[^>]*/?>', '\n', text, flags=re.IGNORECASE)
    text = re.sub(r'</(?:p|div|h[1-6]|li|tr|blockquote|pre|section|article)>', '\n', text, flags=re.IGNORECASE)
    # Strip remaining tags
    text = re.sub(r'<[^>]+>', '', text)
    # Decode common entities
    text = text.replace('&amp;', '&').replace('&lt;', '<').replace('&gt;', '>')
    text = text.replace('&quot;', '"').replace('&#39;', "'").replace('&nbsp;', ' ')
    # Normalize whitespace
    text = re.sub(r'[ \t]+', ' ', text)
    text = re.sub(r'\n{3,}', '\n\n', text)
    return text.strip()


@tool(args_schema=WebFetchArgs)
def webfetch(url: str) -> str:
    """Fetch and read the textual content of a web page.
    
    Useful for reading documentation, articles, or other online resources.
    Returns extracted text content (HTML tags stripped).

    Args:
        url: The URL to fetch.

    Returns:
        The readable text content of the page or an error message.
    """
    try:
        if not url.startswith("http"):
            url = "https://" + url

        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36"
        }
        
        response = requests.get(url, headers=headers, timeout=config.TOOL_TIMEOUT)
        response.raise_for_status()
        
        content_type = response.headers.get("Content-Type", "")
        raw = response.text
        
        # Extract text from HTML
        if "html" in content_type.lower() or raw.strip().startswith("<!") or raw.strip().startswith("<html"):
            content = _html_to_text(raw)
        else:
            content = raw
             
        return truncate_output(f"📄 Content of {url} ({len(content)} chars):\n\n{content}")

    except requests.exceptions.RequestException as e:
        return f"Error fetching {url}: {e}"
