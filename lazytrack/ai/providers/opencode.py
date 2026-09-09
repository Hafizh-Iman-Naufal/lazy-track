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

# OpenCode Zen chat/completions models:
# - minimax-m2.7 (recommended default)
# - deepseek-v4-flash
# - glm-5.3-flash
# GPT/Claude on Zen use other endpoints and are not supported here.

T = TypeVar("T", bound=Any)

class OpenCodeProvider(AIProvider[T]):
    def __init__(self, api_key: str, model: str = "minimax-m2.7"):
        self.api_key = api_key
        self.model = model
        self.base_url = "https://opencode.ai/zen/v1"

    def name(self) -> str:
        return "opencode"

    def supports_structured_output(self) -> bool:
        return True

    async def generate(
        self,
        prompt: str,
        schema: type[T] | None = None,
    ) -> T | str:
        if not self.api_key:
            raise AIAuthenticationError("OpenCode API key not configured")

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
            logger.debug(f"[OpenCode] Using schema: {schema.__name__}")

        logger.debug(f"[OpenCode] Sending request to {url}")
        
        try:
            async with httpx.AsyncClient(timeout=60.0) as client:
                response = await client.post(url, json=payload, headers=headers)
        except httpx.TimeoutException as e:
            logger.error(f"[OpenCode] Timeout: {e}")
            raise AITimeoutError(f"Request timed out: {e}")
        except httpx.RequestError as e:
            logger.error(f"[OpenCode] Request error: {e}")
            raise AIProviderUnavailableError(f"Provider unavailable: {e}")

        if response.status_code == 401:
            logger.error("[OpenCode] Authentication failed")
            raise AIAuthenticationError("Invalid OpenCode API key")
        elif response.status_code == 429:
            logger.warning("[OpenCode] Rate limited")
            raise AIRateLimitError("Rate limit exceeded")
        elif response.status_code >= 500:
            logger.error(f"[OpenCode] Server error: {response.status_code}")
            raise AIProviderUnavailableError(f"OpenCode server error: {response.status_code}")

        if response.status_code != 200:
            logger.error(f"[OpenCode] Unexpected status: {response.status_code}")
            raise AIError(f"Unexpected response: {response.status_code}")

        data = response.json()
        
        try:
            text = data["choices"][0]["message"]["content"]
        except (KeyError, IndexError) as e:
            logger.error(f"[OpenCode] Invalid response structure: {e}, data={data}")
            raise AIInvalidResponseError(f"Invalid response structure: {e}")

        logger.debug(f"[OpenCode] Raw response: {text[:200]}...")

        if schema:
            try:
                parsed = json.loads(text)
                logger.debug(f"[OpenCode] Parsed JSON: {parsed}")
                return schema.model_validate(parsed)
            except json.JSONDecodeError as e:
                logger.error(f"[OpenCode] JSON decode error: {e}\nRaw: {text[:500]}")
                raise AIInvalidResponseError(
                    f"Invalid JSON response: {e}\n"
                    f"Raw response: {text[:500]}"
                )
            except ValidationError as e:
                logger.error(f"[OpenCode] Validation error: {e}\nParsed: {parsed}")
                raise AIInvalidResponseError(
                    f"Failed to parse structured response: {e}\n"
                    f"Parsed response: {parsed}"
                )
            except Exception as e:
                logger.error(f"[OpenCode] Unexpected error: {type(e).__name__}: {e}")
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
