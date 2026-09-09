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
from lazytrack.ai.providers import (
    GeminiProvider,
    DeepSeekProvider,
    MiniMaxProvider,
    OpenAIProvider,
    ClaudeProvider,
    OpenCodeProvider,
)


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
        registry.register("openai", OpenAIProvider)
        registry.register("claude", ClaudeProvider)
        registry.register("opencode", OpenCodeProvider)

    def test_list_providers(self):
        providers = list_providers()
        assert "gemini" in providers
        assert "deepseek" in providers
        assert "minimax" in providers
        assert "openai" in providers
        assert "claude" in providers
        assert "opencode" in providers

    def test_get_gemini_provider(self):
        cls = get_provider("gemini")
        assert cls == GeminiProvider

    def test_get_deepseek_provider(self):
        cls = get_provider("deepseek")
        assert cls == DeepSeekProvider

    def test_get_minimax_provider(self):
        cls = get_provider("minimax")
        assert cls == MiniMaxProvider

    def test_get_openai_provider(self):
        cls = get_provider("openai")
        assert cls == OpenAIProvider

    def test_get_claude_provider(self):
        cls = get_provider("claude")
        assert cls == ClaudeProvider

    def test_get_opencode_provider(self):
        cls = get_provider("opencode")
        assert cls == OpenCodeProvider

    def test_case_insensitive(self):
        assert get_provider("GEMINI") == GeminiProvider
        assert get_provider("DeepSeek") == DeepSeekProvider
        assert get_provider("MiniMax") == MiniMaxProvider
        assert get_provider("OpenAI") == OpenAIProvider
        assert get_provider("Claude") == ClaudeProvider
        assert get_provider("OpenCode") == OpenCodeProvider

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


class TestOpenAIProvider:
    def test_name(self):
        provider = OpenAIProvider(api_key="test")
        assert provider.name() == "openai"

    def test_supports_structured_output(self):
        provider = OpenAIProvider(api_key="test")
        assert provider.supports_structured_output() is True

    def test_no_api_key_raises(self):
        provider = OpenAIProvider(api_key="")
        import asyncio
        with pytest.raises(AIAuthenticationError):
            asyncio.run(provider.generate("test"))


class TestClaudeProvider:
    def test_name(self):
        provider = ClaudeProvider(api_key="test")
        assert provider.name() == "claude"

    def test_supports_structured_output(self):
        provider = ClaudeProvider(api_key="test")
        assert provider.supports_structured_output() is True

    def test_no_api_key_raises(self):
        provider = ClaudeProvider(api_key="")
        import asyncio
        with pytest.raises(AIAuthenticationError):
            asyncio.run(provider.generate("test"))


class TestOpenCodeProvider:
    def test_name(self):
        provider = OpenCodeProvider(api_key="test")
        assert provider.name() == "opencode"

    def test_supports_structured_output(self):
        provider = OpenCodeProvider(api_key="test")
        assert provider.supports_structured_output() is True

    def test_no_api_key_raises(self):
        provider = OpenCodeProvider(api_key="")
        import asyncio
        with pytest.raises(AIAuthenticationError):
            asyncio.run(provider.generate("test"))
