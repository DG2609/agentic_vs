"""
Model Advisor — runs a secondary LLM in parallel to critique the main model's response.
Activated via ADVISOR_MODEL config or /advisor CLI command.
"""
import asyncio
import logging
from typing import Optional

import config
try:
    from langchain_anthropic import ChatAnthropic
except ImportError:
    ChatAnthropic = None  # type: ignore
try:
    from langchain_openai import ChatOpenAI
except ImportError:
    ChatOpenAI = None  # type: ignore

logger = logging.getLogger(__name__)

_advisor_model: str = ""  # current advisor model name (empty = disabled)


def set_advisor_model(model: str) -> None:
    global _advisor_model
    _advisor_model = model.strip()
    if _advisor_model:
        logger.info("Advisor model set to: %s", _advisor_model)
    else:
        logger.info("Advisor model disabled")


def get_advisor_model() -> str:
    return _advisor_model


async def run_advisor(user_prompt: str, main_response: str, advisor_model: str) -> Optional[str]:
    """Run the advisor model to critique the main response. Returns critique or None on failure."""
    try:
        from langchain_core.messages import HumanMessage, SystemMessage

        # Build advisor LLM — use same provider but different model
        if config.LLM_PROVIDER == "anthropic":
            if ChatAnthropic is None:
                return None
            llm = ChatAnthropic(
                model=advisor_model,
                api_key=config.ANTHROPIC_API_KEY,
                max_tokens=1024,
                temperature=0.3,
            )
        elif config.LLM_PROVIDER == "openai":
            if ChatOpenAI is None:
                return None
            llm = ChatOpenAI(
                model=advisor_model,
                api_key=config.OPENAI_API_KEY,
                max_tokens=1024,
                temperature=0.3,
            )
        else:
            return None  # advisor only supported for anthropic/openai

        messages = [
            SystemMessage(content=(
                "You are an expert code reviewer acting as an advisor. "
                "Review the assistant's response and provide a concise critique: "
                "correctness issues, missing edge cases, better approaches, or confirm it's good. "
                "Be brief (2-4 sentences max). Start with LGTM ✅ if no issues found."
            )),
            HumanMessage(content=(
                f"Original request:\n{user_prompt[:2000]}\n\n"
                f"Assistant response:\n{main_response[:3000]}\n\n"
                "Your critique:"
            )),
        ]

        response = await asyncio.wait_for(
            llm.ainvoke(messages),
            timeout=30.0
        )
        return str(getattr(response, 'content', response)).strip()
    except Exception as e:
        logger.debug("Advisor failed: %s", e)
        return None
