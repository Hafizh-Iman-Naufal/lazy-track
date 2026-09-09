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

# Available Claude models:
# - claude-haiku-4-5 (recommended default)
# - claude-sonnet-4-5
# - claude-opus-4-5

T = TypeVar("T", bound=Any)

class ClaudeProvider(AIProvider[T]):
    def __init__(self, api_key: str, model: str = "claude-haiku-4-5"):
        self.api_key = api_key
        self.model = model
        self.base_url = "https://api.anthropic.com/v1"

    def name(self) -> str:
        return "claude"

    def supports_structured_output(self) -> bool:
        return True

    async def generate(
        self,
        prompt: str,
        schema: type[T] | None = None,
    ) -> T | str:
        if not self.api_key:
            raise AIAuthenticationError("Claude API key not configured")

        url = f"{self.base_url}/messages"
        headers = {
            "x-api-key": self.api_key,
            "anthropic-version": "2023-06-01",
            "Content-Type": "application/json",
        }

        content = prompt
        if schema:
            schema_example = self._schema_to_example(schema)
            content = (
                f"{prompt}\n\n"
                f"Respond with valid JSON matching this schema:\n"
                f"{json.dumps(schema_example, indent=2)}"
            )
            logger.debug(f"[Claude] Using schema: {schema.__name__}")

        payload: dict[str, Any] = {
            "model": self.model,
            "max_tokens": 1024,
            "messages": [{"role": "user", "content": content}],
        }

        logger.debug(f"[Claude] Sending request to {url}")
        
        try:
            async with httpx.AsyncClient(timeout=60.0) as client:
                response = await client.post(url, json=payload, headers=headers)
        except httpx.TimeoutException as e:
            logger.error(f"[Claude] Timeout: {e}")
            raise AITimeoutError(f"Request timed out: {e}")
        except httpx.RequestError as e:
            logger.error(f"[Claude] Request error: {e}")
            raise AIProviderUnavailableError(f"Provider unavailable: {e}")

        if response.status_code == 401:
            logger.error("[Claude] Authentication failed")
            raise AIAuthenticationError("Invalid Claude API key")
        elif response.status_code == 429:
            logger.warning("[Claude] Rate limited")
            raise AIRateLimitError("Rate limit exceeded")
        elif response.status_code >= 500:
            logger.error(f"[Claude] Server error: {response.status_code}")
            raise AIProviderUnavailableError(f"Claude server error: {response.status_code}")

        if response.status_code != 200:
            logger.error(f"[Claude] Unexpected status: {response.status_code}")
            raise AIError(f"Unexpected response: {response.status_code}")

        data = response.json()
        
        try:
            blocks = data.get("content") or []
            text = ""
            for block in blocks:
                if block.get("type") == "text":
                    text = block.get("text", "")
                    break
            if not text and blocks:
                text = blocks[0].get("text", "")
        except (KeyError, IndexError, AttributeError, TypeError) as e:
            logger.error(f"[Claude] Invalid response structure: {e}, data={data}")
            raise AIInvalidResponseError(f"Invalid response structure: {e}")

        logger.debug(f"[Claude] Raw response: {text[:200]}...")

        if schema:
            try:
                parsed = json.loads(text)
                logger.debug(f"[Claude] Parsed JSON: {parsed}")
                return schema.model_validate(parsed)
            except json.JSONDecodeError as e:
                logger.error(f"[Claude] JSON decode error: {e}\nRaw: {text[:500]}")
                raise AIInvalidResponseError(
                    f"Invalid JSON response: {e}\n"
                    f"Raw response: {text[:500]}"
                )
            except ValidationError as e:
                logger.error(f"[Claude] Validation error: {e}\nParsed: {parsed}")
                raise AIInvalidResponseError(
                    f"Failed to parse structured response: {e}\n"
                    f"Parsed response: {parsed}"
                )
            except Exception as e:
                logger.error(f"[Claude] Unexpected error: {type(e).__name__}: {e}")
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
