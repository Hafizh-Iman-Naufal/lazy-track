import json
import logging
from typing import Any, TypeVar

import httpx
from pydantic import ValidationError

from lazytrack.ai.base import (
    AIError,
    AIProvider,
    AIAuthenticationError,
    AIRateLimitError,
    AITimeoutError,
    AIInvalidResponseError,
    AIProviderUnavailableError,
)

logger = logging.getLogger(__name__)

# Available OpenAI models (Chat Completions):
# - gpt-4o-mini (recommended default)
# - gpt-4o
# - gpt-4.1-mini

T = TypeVar("T", bound=Any)

class OpenAIProvider(AIProvider[T]):
    def __init__(self, api_key: str, model: str = "gpt-4o-mini"):
        self.api_key = api_key
        self.model = model
        self.base_url = "https://api.openai.com/v1"

    def name(self) -> str:
        return "openai"

    def supports_structured_output(self) -> bool:
        return True

    async def generate(
        self,
        prompt: str,
        schema: type[T] | None = None,
    ) -> T | str:
        if not self.api_key:
            raise AIAuthenticationError("OpenAI API key not configured")

        url = f"{self.base_url}/chat/completions"
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }

        messages = [{"role": "user", "content": prompt}]
        
        payload: dict[str, Any] = {
            "model": self.model,
            "messages": messages,
        }
        
        if schema:
            payload["response_format"] = {"type": "json_object"}
            schema_example = self._schema_to_example(schema)
            prompt_with_schema = (
                f"{prompt}\n\n"
                f"Respond with valid JSON matching this schema:\n"
                f"{json.dumps(schema_example, indent=2)}"
            )
            messages[0]["content"] = prompt_with_schema
            logger.debug(f"[OpenAI] Using schema: {schema.__name__}")

        logger.debug(f"[OpenAI] Sending request to {url}")
        
        try:
            async with httpx.AsyncClient(timeout=60.0) as client:
                response = await client.post(url, json=payload, headers=headers)
        except httpx.TimeoutException as e:
            logger.error(f"[OpenAI] Timeout: {e}")
            raise AITimeoutError(f"Request timed out: {e}")
        except httpx.RequestError as e:
            logger.error(f"[OpenAI] Request error: {e}")
            raise AIProviderUnavailableError(f"Provider unavailable: {e}")

        if response.status_code == 401:
            logger.error("[OpenAI] Authentication failed")
            raise AIAuthenticationError("Invalid OpenAI API key")
        elif response.status_code == 429:
            logger.warning("[OpenAI] Rate limited")
            raise AIRateLimitError("Rate limit exceeded")
        elif response.status_code >= 500:
            logger.error(f"[OpenAI] Server error: {response.status_code}")
            raise AIProviderUnavailableError(f"OpenAI server error: {response.status_code}")

        if response.status_code != 200:
            logger.error(f"[OpenAI] Unexpected status: {response.status_code}")
            raise AIError(f"Unexpected response: {response.status_code}")

        data = response.json()
        
        try:
            text = data["choices"][0]["message"]["content"]
        except (KeyError, IndexError) as e:
            logger.error(f"[OpenAI] Invalid response structure: {e}, data={data}")
            raise AIInvalidResponseError(f"Invalid response structure: {e}")

        logger.debug(f"[OpenAI] Raw response: {text[:200]}...")

        if schema:
            try:
                parsed = json.loads(text)
                logger.debug(f"[OpenAI] Parsed JSON: {parsed}")
                return schema.model_validate(parsed)
            except json.JSONDecodeError as e:
                logger.error(f"[OpenAI] JSON decode error: {e}\nRaw: {text[:500]}")
                raise AIInvalidResponseError(
                    f"Invalid JSON response: {e}\n"
                    f"Raw response: {text[:500]}"
                )
            except ValidationError as e:
                logger.error(f"[OpenAI] Validation error: {e}\nParsed: {parsed}")
                raise AIInvalidResponseError(
                    f"Failed to parse structured response: {e}\n"
                    f"Parsed response: {parsed}"
                )
            except Exception as e:
                logger.error(f"[OpenAI] Unexpected error: {type(e).__name__}: {e}")
                raise AIInvalidResponseError(f"Failed to parse structured response: {e}")

        return text

    def _schema_to_example(self, schema: type[T]) -> dict:
        schema_dict = schema.model_json_schema()
        example: dict[str, Any] = {}
        
        for name, prop in schema_dict.get("properties", {}).items():
            prop_type = prop.get("type", "string")
            if "enum" in prop and prop["enum"]:
                example[name] = prop["enum"][0]
            elif prop_type == "string":
                example[name] = "example_value"
            elif prop_type == "integer" or prop_type == "number":
                example[name] = 0
            elif prop_type == "boolean":
                example[name] = True
            elif prop_type == "array":
                example[name] = []
            elif prop_type == "object":
                example[name] = {}
            else:
                example[name] = None
        
        return example
