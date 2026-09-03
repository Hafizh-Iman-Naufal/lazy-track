from abc import ABC, abstractmethod
from typing import Any, TypeVar, Generic
from pydantic import BaseModel

T = TypeVar("T", bound=BaseModel)


class AIError(Exception):
    pass


class AIAuthenticationError(AIError):
    pass


class AIRateLimitError(AIError):
    pass


class AITimeoutError(AIError):
    pass


class AIInvalidResponseError(AIError):
    pass


class AIProviderUnavailableError(AIError):
    pass


class AIProvider(ABC, Generic[T]):
    @abstractmethod
    async def generate(
        self,
        prompt: str,
        schema: type[T] | None = None,
    ) -> T | str:
        pass

    @abstractmethod
    def name(self) -> str:
        pass

    @abstractmethod
    def supports_structured_output(self) -> bool:
        pass
