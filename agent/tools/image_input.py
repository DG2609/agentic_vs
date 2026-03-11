"""
Image input tool — encode images for multimodal LLM messages.

Supports: PNG, JPG, JPEG, WebP, GIF
Works with: OpenAI (gpt-4o+), Anthropic (claude), Google (gemini)
"""

import os
import base64
import logging
import mimetypes
from langchain_core.tools import tool
from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)

SUPPORTED_EXTENSIONS = {".png", ".jpg", ".jpeg", ".webp", ".gif", ".bmp", ".svg"}
SUPPORTED_MIMES = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".webp": "image/webp",
    ".gif": "image/gif",
    ".bmp": "image/bmp",
    ".svg": "image/svg+xml",
}
MAX_IMAGE_SIZE = 20 * 1024 * 1024  # 20MB


class ImageInputArgs(BaseModel):
    """Arguments for image input."""
    image_path: str = Field(
        description="Path to the image file (absolute or relative to workspace)."
    )
    detail: str = Field(
        default="auto",
        description="Detail level for vision models: 'auto', 'low', or 'high'."
    )


@tool(args_schema=ImageInputArgs)
def image_input(image_path: str, detail: str = "auto") -> str:
    """Load an image file and encode it for multimodal LLM analysis.

    Returns a base64-encoded image ready for multimodal messages.
    The LLM can then analyze screenshots, diagrams, UI mockups, etc.
    """
    from agent.tools.utils import resolve_tool_path

    resolved = resolve_tool_path(image_path)

    if not os.path.isfile(resolved):
        return f"Error: Image file not found: {resolved}"

    ext = os.path.splitext(resolved)[1].lower()
    if ext not in SUPPORTED_EXTENSIONS:
        return f"Error: Unsupported image format '{ext}'. Supported: {', '.join(sorted(SUPPORTED_EXTENSIONS))}"

    file_size = os.path.getsize(resolved)
    if file_size > MAX_IMAGE_SIZE:
        return f"Error: Image too large ({file_size / 1024 / 1024:.1f}MB). Max: 20MB."
    if file_size == 0:
        return "Error: Image file is empty."

    try:
        with open(resolved, "rb") as f:
            image_data = f.read()
        encoded = base64.b64encode(image_data).decode("utf-8")
    except Exception as e:
        return f"Error reading image: {e}"

    mime_type = SUPPORTED_MIMES.get(ext, "image/png")

    # Return structured data the agent can use
    return (
        f"Image loaded: {os.path.basename(resolved)} "
        f"({file_size / 1024:.1f}KB, {mime_type})\n"
        f"detail={detail}\n"
        f"data:image_base64:{mime_type};{encoded[:100]}...[{len(encoded)} chars total]"
    )


def encode_image_for_message(image_path: str, detail: str = "auto") -> dict | None:
    """Encode an image file into a content block for multimodal HumanMessage.

    Returns a dict suitable for LangChain's HumanMessage content list:
    {"type": "image_url", "image_url": {"url": "data:image/png;base64,...", "detail": "auto"}}

    Returns None if the file cannot be loaded.
    """
    if not os.path.isfile(image_path):
        return None

    ext = os.path.splitext(image_path)[1].lower()
    if ext not in SUPPORTED_EXTENSIONS:
        return None

    file_size = os.path.getsize(image_path)
    if file_size > MAX_IMAGE_SIZE or file_size == 0:
        return None

    try:
        with open(image_path, "rb") as f:
            image_data = f.read()
        encoded = base64.b64encode(image_data).decode("utf-8")
    except Exception:
        return None

    mime_type = SUPPORTED_MIMES.get(ext, "image/png")

    return {
        "type": "image_url",
        "image_url": {
            "url": f"data:{mime_type};base64,{encoded}",
            "detail": detail,
        },
    }
