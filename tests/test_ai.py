import pytest
from pydantic import BaseModel

from lazytrack.ai import (
    get_provider,
    list_providers,
    AIAuthenticationError,
    AIRateLimitError,
    AITimeoutError,
    AIProviderUnavailableError,
    get_registry,
)
from lazytrack.ai.providers import GeminiProvider, DeepSeekProvider, MiniMaxProvider


class IntentSchema(BaseModel):
    type: str
    message: str


class TestProviderRegistry:
    def setup_method(self):
        registry = get_registry()
        registry._providers.clear()
        registry.register("gemini", GeminiProvider)
        registry.register("deepseek", DeepSeekProvider)
        registry.register("minimax", MiniMaxProvider)

    def test_list_providers(self):
        providers = list_providers()
        assert "gemini" in providers
        assert "deepseek" in providers
        assert "minimax" in providers

    def test_get_gemini_provider(self):
        cls = get_provider("gemini")
        assert cls == GeminiProvider

    def test_get_deepseek_provider(self):
        cls = get_provider("deepseek")
        assert cls == DeepSeekProvider

    def test_get_minimax_provider(self):
        cls = get_provider("minimax")
        assert cls == MiniMaxProvider

    def test_case_insensitive(self):
        assert get_provider("GEMINI") == GeminiProvider
        assert get_provider("DeepSeek") == DeepSeekProvider
        assert get_provider("MiniMax") == MiniMaxProvider

    def test_unknown_provider(self):
        assert get_provider("unknown") is None


class TestGeminiProvider:
    def test_name(self):
        provider = GeminiProvider(api_key="test")
        assert provider.name() == "gemini"

    def test_supports_structured_output(self):
        provider = GeminiProvider(api_key="test")
        assert provider.supports_structured_output() is True

    def test_no_api_key_raises(self):
        provider = GeminiProvider(api_key="")
        import asyncio
        with pytest.raises(AIAuthenticationError):
            asyncio.run(provider.generate("test"))


class TestDeepSeekProvider:
    def test_name(self):
        provider = DeepSeekProvider(api_key="test")
        assert provider.name() == "deepseek"

    def test_supports_structured_output(self):
        provider = DeepSeekProvider(api_key="test")
        assert provider.supports_structured_output() is True

    def test_no_api_key_raises(self):
        provider = DeepSeekProvider(api_key="")
        import asyncio
        with pytest.raises(AIAuthenticationError):
            asyncio.run(provider.generate("test"))


class TestMiniMaxProvider:
    def test_name(self):
        provider = MiniMaxProvider(api_key="test")
        assert provider.name() == "minimax"

    def test_supports_structured_output(self):
        provider = MiniMaxProvider(api_key="test")
        assert provider.supports_structured_output() is True

    def test_no_api_key_raises(self):
        provider = MiniMaxProvider(api_key="")
        import asyncio
        with pytest.raises(AIAuthenticationError):
            asyncio.run(provider.generate("test"))
