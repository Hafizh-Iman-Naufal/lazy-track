from lazytrack.ai.base import (
    AIError,
    AIAuthenticationError,
    AIRateLimitError,
    AITimeoutError,
    AIInvalidResponseError,
    AIProviderUnavailableError,
    AIProvider,
)
from lazytrack.ai.registry import (
    ProviderRegistry,
    register_provider,
    get_provider,
    list_providers,
    get_registry,
)
from lazytrack.ai.providers import GeminiProvider, DeepSeekProvider, MiniMaxProvider
from lazytrack.ai.schemas import (
    IntentType,
    AllocationItem,
    AllocateTimeIntent,
    ReallocateTimeIntent,
    RemoveAllocationIntent,
    ShowWeekIntent,
    ShowIssuesIntent,
    AddLeaveIntent,
    RemoveLeaveIntent,
    AddHolidayIntent,
    RemoveHolidayIntent,
    ClarificationRequired,
    IntentSchema,
    UnionIntent,
)
from lazytrack.ai.prompts import SYSTEM_PROMPT, build_intent_prompt
from lazytrack.ai.normalize import ParseContext, parse_intent, normalize_intent_dict
from lazytrack.ai.generate import generate_structured_intent

registry = get_registry()
registry.register("gemini", GeminiProvider)
registry.register("deepseek", DeepSeekProvider)
registry.register("minimax", MiniMaxProvider)

__all__ = [
    "AIError",
    "AIAuthenticationError",
    "AIRateLimitError",
    "AITimeoutError",
    "AIInvalidResponseError",
    "AIProviderUnavailableError",
    "AIProvider",
    "ProviderRegistry",
    "register_provider",
    "get_provider",
    "list_providers",
    "get_registry",
    "GeminiProvider",
    "DeepSeekProvider",
    "MiniMaxProvider",
    "IntentType",
    "AllocationItem",
    "AllocateTimeIntent",
    "ReallocateTimeIntent",
    "RemoveAllocationIntent",
    "ShowWeekIntent",
    "ShowIssuesIntent",
    "AddLeaveIntent",
    "RemoveLeaveIntent",
    "AddHolidayIntent",
    "RemoveHolidayIntent",
    "ClarificationRequired",
    "IntentSchema",
    "UnionIntent",
    "SYSTEM_PROMPT",
    "build_intent_prompt",
    "ParseContext",
    "parse_intent",
    "normalize_intent_dict",
    "generate_structured_intent",
]
