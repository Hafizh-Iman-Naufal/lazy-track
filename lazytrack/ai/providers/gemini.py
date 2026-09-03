import json
from typing import Any, TypeVar
from pydantic import BaseModel

import httpx

from lazytrack.ai.base import (
    AIError,
    AIProvider,
    AIAuthenticationError,
    AIRateLimitError,
    AITimeoutError,
    AIInvalidResponseError,
    AIProviderUnavailableError,
)

T = TypeVar("T", bound=BaseModel)


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
        
        payload: dict[str, Any] = {"contents": contents}
        
        if schema:
            payload["generationConfig"] = {
                "responseMimeType": "application/json",
                "responseSchema": self._schema_to_gemini(schema),
            }

        try:
            async with httpx.AsyncClient(timeout=60.0) as client:
                response = await client.post(url, json=payload, params=params)
        except httpx.TimeoutException as e:
            raise AITimeoutError(f"Request timed out: {e}")
        except httpx.RequestError as e:
            raise AIProviderUnavailableError(f"Provider unavailable: {e}")

        if response.status_code == 401:
            raise AIAuthenticationError("Invalid Gemini API key")
        elif response.status_code == 429:
            raise AIRateLimitError("Rate limit exceeded")
        elif response.status_code >= 500:
            raise AIProviderUnavailableError(f"Gemini server error: {response.status_code}")

        if response.status_code != 200:
            raise AIError(f"Unexpected response: {response.status_code}")

        data = response.json()
        
        try:
            text = data["candidates"][0]["content"]["parts"][0]["text"]
        except (KeyError, IndexError) as e:
            raise AIInvalidResponseError(f"Invalid response structure: {e}")

        if schema:
            try:
                return schema.model_validate_json(text)
            except Exception as e:
                raise AIInvalidResponseError(f"Failed to parse structured response: {e}")

        return text

    def _schema_to_gemini(self, schema: type[T]) -> dict:
        schema_dict = schema.model_json_schema()
        properties = schema_dict.get("properties", {})
        
        gemini_schema: dict[str, Any] = {
            "type": schema_dict.get("type", "object"),
            "properties": {},
            "required": schema_dict.get("required", []),
        }
        
        for name, prop in properties.items():
            gemini_schema["properties"][name] = {
                "type": prop.get("type", "string"),
            }
            if "enum" in prop:
                gemini_schema["properties"][name]["enum"] = prop["enum"]
        
        return gemini_schema
