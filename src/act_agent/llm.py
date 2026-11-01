"""LLM factory: OpenAI-compatible gateway (DeepSeek/通义/vLLM) or Anthropic."""

from __future__ import annotations

import logging

from langchain_core.language_models import BaseChatModel

from act_agent.settings import Settings

logger = logging.getLogger(__name__)


def build_llm(settings: Settings) -> BaseChatModel:
    api_key = settings.llm_api_key or "not-configured"
    if not settings.llm_api_key:
        logger.warning("LLM API key is empty - chat calls will fail until configured")

    if settings.llm_provider.lower() == "anthropic":
        from langchain_anthropic import ChatAnthropic

        return ChatAnthropic(
            model=settings.llm_model,
            api_key=api_key,
            base_url=settings.llm_base_url or None,
            temperature=settings.llm_temperature,
            max_tokens=settings.llm_max_tokens,
        )

    from langchain_openai import ChatOpenAI

    return ChatOpenAI(
        model=settings.llm_model,
        api_key=api_key,
        base_url=settings.llm_base_url or None,
        temperature=settings.llm_temperature,
        max_tokens=settings.llm_max_tokens,
        timeout=120,
        max_retries=2,
    )
