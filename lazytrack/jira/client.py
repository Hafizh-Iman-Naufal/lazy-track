import base64
from typing import Optional

import httpx


class JiraClientError(Exception):
    pass


class JiraAuthenticationError(JiraClientError):
    pass


class JiraPermissionError(JiraClientError):
    pass


class JiraNotFoundError(JiraClientError):
    pass


class JiraRateLimitError(JiraClientError):
    pass


class JiraServerError(JiraClientError):
    pass


class JiraTimeoutError(JiraClientError):
    pass


class JiraClient:
    def __init__(
        self,
        base_url: str,
        email: str,
        api_token: str,
        timeout: float = 30.0,
    ):
        self.base_url = base_url.rstrip("/")
        self.email = email
        self.api_token = api_token
        self.timeout = timeout
        self._auth = base64.b64encode(
            f"{email}:{api_token}".encode()
        ).decode()

    def _request(
        self,
        method: str,
        path: str,
        **kwargs,
    ) -> dict:
        url = f"{self.base_url}{path}"
        headers = kwargs.pop("headers", {})
        headers["Authorization"] = f"Basic {self._auth}"
        headers["Content-Type"] = "application/json"

        try:
            response = httpx.request(
                method=method,
                url=url,
                headers=headers,
                timeout=self.timeout,
                **kwargs,
            )
        except httpx.TimeoutException as e:
            raise JiraTimeoutError(f"Request timed out: {e}")

        if response.status_code == 401:
            raise JiraAuthenticationError("Invalid Jira credentials")
        elif response.status_code == 403:
            raise JiraPermissionError("Insufficient permissions")
        elif response.status_code == 404:
            raise JiraNotFoundError(f"Resource not found: {path}")
        elif response.status_code == 429:
            raise JiraRateLimitError("Rate limit exceeded")
        elif 500 <= response.status_code < 600:
            raise JiraServerError(f"Jira server error: {response.status_code}")

        return response.json()

    def get(self, path: str, **kwargs) -> dict:
        return self._request("GET", path, **kwargs)

    def post(self, path: str, **kwargs) -> dict:
        return self._request("POST", path, **kwargs)
