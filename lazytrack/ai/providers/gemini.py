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

# Available Gemini models:
# - gemini-2.0-flash-exp (latest, recommended)
# - gemini-1.5-flash (stable, fast)
# - gemini-1.5-flash-8b (lightweight, faster)
# - gemini-1.5-pro (high capability)

T = TypeVar("T", bound=Any)

class GeminiProvider(AIProvider[T]):
    def __init__(self, api_key: str, model: str = "gemini-1.5-flash"):
        self.api_key = api_key
        self.model = model
        self.base_url = "https://generativelanguage.googleapis.com/v1beta"

    def name(self) -> str:
        return "gemini"

    def supports_structured_output(self) -> bool:
        return True

    async def generate(
        self,
        prompt: str,
        schema: type[T] | None = None,
    ) -> T | str:
        if not self.api_key:
            raise AIAuthenticationError("Gemini API key not configured")

        url = f"{self.base_url}/models/{self.model}:generateContent"
        params = {"key": self.api_key}

        contents = [{"parts": [{"text": prompt}]}]
        
        payload: dict[str, Any] = {
            "contents": contents,
            "generationConfig": {"responseMimeType": "application/json"},
        }

        logger.debug(f"[Gemini] Sending request to {url}")
        
        try:
            async with httpx.AsyncClient(timeout=60.0) as client:
                response = await client.post(url, json=payload, params=params)
        except httpx.TimeoutException as e:
            logger.error(f"[Gemini] Timeout: {e}")
            raise AITimeoutError(f"Request timed out: {e}")
        except httpx.RequestError as e:
            logger.error(f"[Gemini] Request error: {e}")
            raise AIProviderUnavailableError(f"Provider unavailable: {e}")

        if response.status_code == 401:
            logger.error("[Gemini] Authentication failed")
            raise AIAuthenticationError("Invalid Gemini API key")
        elif response.status_code == 429:
            logger.warning("[Gemini] Rate limited")
            raise AIRateLimitError("Rate limit exceeded")
        elif response.status_code >= 500:
            logger.error(f"[Gemini] Server error: {response.status_code}")
            raise AIProviderUnavailableError(f"Gemini server error: {response.status_code}")

        if response.status_code != 200:
            logger.error(f"[Gemini] Unexpected status: {response.status_code}, body: {response.text[:200]}")
            raise AIError(f"Unexpected response: {response.status_code}")

        data = response.json()
        
        try:
            text = data["candidates"][0]["content"]["parts"][0]["text"]
        except (KeyError, IndexError) as e:
            logger.error(f"[Gemini] Invalid response structure: {e}, data={data}")
            raise AIInvalidResponseError(f"Invalid response structure: {e}")

        logger.debug(f"[Gemini] Raw response: {text[:200]}...")

        if schema:
            try:
                logger.debug(f"[Gemini] Parsing as schema {schema.__name__}")
                parsed = json.loads(text)
                return schema.model_validate(parsed)
            except json.JSONDecodeError as e:
                logger.error(f"[Gemini] JSON decode error: {e}\nRaw: {text[:500]}")
                raise AIInvalidResponseError(
                    f"Invalid JSON response: {e}\nRaw response: {text[:500]}"
                )
            except ValidationError as e:
                logger.error(f"[Gemini] Validation error: {e}\nRaw: {text[:500]}")
                raise AIInvalidResponseError(
                    f"Failed to parse structured response: {e}\n"
                    f"Raw response: {text[:500]}"
                )
            except Exception as e:
                logger.error(f"[Gemini] Unexpected error: {type(e).__name__}: {e}")
                raise AIInvalidResponseError(f"Failed to parse structured response: {e}")

        return text
