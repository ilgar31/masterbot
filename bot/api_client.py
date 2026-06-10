from __future__ import annotations

from datetime import date, timedelta
from typing import Any
from urllib.parse import urljoin

import requests


class ApiError(RuntimeError):
    """Raised when the clinic API is unavailable or returns a bad response."""

    def __init__(
        self,
        message: str,
        *,
        status_code: int | None = None,
        body: str | None = None,
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.body = body


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

    def _request_once(
        self,
        method: str,
        path: str,
        *,
        json: dict[str, Any] | None = None,
    ) -> requests.Response:
        url = urljoin(self.base_url, path.lstrip("/"))
        try:
            return self.session.request(
                method=method,
                url=url,
                json=json,
                headers=self._headers(),
                timeout=self.timeout,
            )
        except requests.RequestException as exc:
            raise ApiError(f"API недоступно: {exc}") from exc

    @staticmethod
    def _alternate_slash_path(path: str) -> str | None:
        if path == "/":
            return None
        return path.rstrip("/") if path.endswith("/") else path + "/"

    def _request(
        self,
        method: str,
        path: str,
        *,
        json: dict[str, Any] | None = None,
        allow_404: bool = False,
    ) -> Any:
        response = self._request_once(method, path, json=json)

        # Some lightweight APIs expose /clients while others expose /clients/.
        # Try the sibling route once before surfacing a 404 to the bot.
        if response.status_code == 404:
            alternate_path = self._alternate_slash_path(path)
            if alternate_path and alternate_path != path:
                alternate_response = self._request_once(method, alternate_path, json=json)
                if alternate_response.status_code != 404:
                    response = alternate_response

        if allow_404 and response.status_code == 404:
            return None

        if not 200 <= response.status_code < 300:
            body = response.text[:500]
            raise ApiError(
                f"API вернуло {response.status_code}: {body}",
                status_code=response.status_code,
                body=body,
            )

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

    @staticmethod
    def _client_payload(payload: dict[str, Any]) -> dict[str, Any]:
        full_name = str(payload.get("full_name") or payload.get("name") or "").strip()
        parts = [part for part in full_name.split() if part]
        name = str(payload.get("name") or (parts[0] if parts else "Клиент")).strip()
        surname = str(payload.get("surname") or (parts[1] if len(parts) > 1 else "Без фамилии")).strip()
        patronymic = str(payload.get("patronymic") or (" ".join(parts[2:]) if len(parts) > 2 else "-")).strip()
        phone = str(payload.get("phone") or "").strip()
        if phone and not phone.startswith("+"):
            phone = f"+{phone}"

        result: dict[str, Any] = {
            "name": name,
            "surname": surname,
            "patronymic": patronymic or "-",
            "age": int(payload.get("age") or 1),
            "phone": phone,
        }
        email = str(payload.get("email") or "").strip()
        if email:
            result["email"] = email
        password = payload.get("password")
        if password:
            result["password"] = str(password)
        return result

    @staticmethod
    def _subscription_payload(
        client_id: str,
        subscription_id: str,
        payload: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        today = date.today()
        body: dict[str, Any] = {
            "client_id": int(client_id),
            "subscription_id": int(subscription_id),
            "start_date": today.isoformat(),
            "end_date": (today + timedelta(days=365)).isoformat(),
        }
        if payload:
            body.update(payload)
        return body

    def welcome(self) -> dict[str, Any]:
        data = self._request("GET", "/welcome/")
        return data if isinstance(data, dict) else {}

    def create_client(self, payload: dict[str, Any]) -> dict[str, Any]:
        data = self._request("POST", "/clients/", json=self._client_payload(payload))
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
        data = self._request("GET", f"/clients/subscription/{client_id}/", allow_404=True)
        if isinstance(data, list):
            subscription = data[0] if data else None
        else:
            subscription = data if isinstance(data, dict) else None
        if not subscription:
            return None

        services = self._request("GET", f"/clients/subscription/{client_id}/services/", allow_404=True)
        if isinstance(services, list):
            subscription["services_availability"] = services
            for service in services:
                if isinstance(service, dict) and service.get("subscription_name"):
                    subscription.setdefault("subscription_name", service.get("subscription_name"))
                    subscription.setdefault("price", service.get("subscription_price"))
                    break
        return subscription

    def connect_subscription(
        self,
        client_id: str,
        subscription_id: str,
        payload: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        body = self._subscription_payload(client_id, subscription_id, payload)
        try:
            data = self._request("POST", "/clients/subscription/", json=body)
        except ApiError as exc:
            if exc.status_code != 400 or not exc.body or "already has a subscription" not in exc.body:
                raise
            data = self._request("PUT", f"/clients/subscription/{client_id}/", json=body)
        return data if isinstance(data, dict) else {}

    def list_prices(self) -> list[dict[str, Any]]:
        data = self._request("GET", "/prices/")
        return self._as_list(data, "prices", "categories")

    def get_price(self, price_id: str) -> dict[str, Any] | None:
        data = self._request("GET", f"/prices/{price_id}", allow_404=True)
        return data if isinstance(data, dict) else None
