from __future__ import annotations

from typing import Any
from urllib.parse import urljoin

import requests


class ApiError(RuntimeError):
    """Raised when the clinic API is unavailable or returns a bad response."""


class ClinicApiClient:
    def __init__(
        self,
        base_url: str,
        token: str | None = None,
        token_header: str = "Authorization",
        token_prefix: str = "Bearer ",
        api_key: str | None = None,
        api_key_header: str = "apikey",
        timeout: float = 5,
    ) -> None:
        self.base_url = base_url.rstrip("/") + "/"
        self.token = token
        self.token_header = token_header
        self.token_prefix = token_prefix
        self.api_key = api_key
        self.api_key_header = api_key_header
        self.timeout = timeout
        self.session = requests.Session()

    def _headers(self) -> dict[str, str]:
        headers = {
            "Accept": "application/json",
            "Content-Type": "application/json",
        }
        if self.token:
            headers[self.token_header] = f"{self.token_prefix}{self.token}"
        if self.api_key:
            headers[self.api_key_header] = self.api_key
        return headers

    def _request(
        self,
        method: str,
        path: str,
        *,
        json: dict[str, Any] | None = None,
        allow_404: bool = False,
    ) -> Any:
        url = urljoin(self.base_url, path.lstrip("/"))
        try:
            response = self.session.request(
                method=method,
                url=url,
                json=json,
                headers=self._headers(),
                timeout=self.timeout,
            )
        except requests.RequestException as exc:
            raise ApiError(f"API недоступно: {exc}") from exc

        if allow_404 and response.status_code == 404:
            return None

        if not 200 <= response.status_code < 300:
            body = response.text[:500]
            raise ApiError(f"API вернуло {response.status_code}: {body}")

        if not response.text:
            return {}

        try:
            return response.json()
        except ValueError:
            return {"raw": response.text}

    @staticmethod
    def _as_list(data: Any, *keys: str) -> list[dict[str, Any]]:
        if isinstance(data, list):
            return [item for item in data if isinstance(item, dict)]
        if isinstance(data, dict):
            for key in keys:
                value = data.get(key)
                if isinstance(value, list):
                    return [item for item in value if isinstance(item, dict)]
            value = data.get("data")
            if isinstance(value, list):
                return [item for item in value if isinstance(item, dict)]
            value = data.get("items")
            if isinstance(value, list):
                return [item for item in value if isinstance(item, dict)]
        return []

    def welcome(self) -> dict[str, Any]:
        data = self._request("GET", "/welcome/")
        return data if isinstance(data, dict) else {}

    def create_client(self, payload: dict[str, Any]) -> dict[str, Any]:
        data = self._request("POST", "/clients/", json=payload)
        return data if isinstance(data, dict) else {}

    def get_client(self, client_id: str) -> dict[str, Any] | None:
        data = self._request("GET", f"/clients/{client_id}", allow_404=True)
        return data if isinstance(data, dict) else None

    def update_client(self, client_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        data = self._request("PUT", f"/clients/{client_id}", json=payload)
        return data if isinstance(data, dict) else {}

    def list_subscriptions(self) -> list[dict[str, Any]]:
        data = self._request("GET", "/subscriptions/")
        return self._as_list(data, "subscriptions")

    def get_client_subscription(self, client_id: str) -> dict[str, Any] | None:
        data = self._request("GET", f"/clients/{client_id}/subscription/", allow_404=True)
        if isinstance(data, list):
            return data[0] if data else None
        return data if isinstance(data, dict) else None

    def connect_subscription(
        self,
        client_id: str,
        subscription_id: str,
        payload: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        body = {"subscription_id": subscription_id}
        if payload:
            body.update(payload)
        data = self._request("POST", f"/clients/{client_id}/subscription/", json=body)
        return data if isinstance(data, dict) else {}

    def list_prices(self) -> list[dict[str, Any]]:
        data = self._request("GET", "/prices/")
        return self._as_list(data, "prices", "categories")

    def get_price(self, price_id: str) -> dict[str, Any] | None:
        data = self._request("GET", f"/prices/{price_id}", allow_404=True)
        return data if isinstance(data, dict) else None

