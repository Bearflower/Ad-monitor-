from __future__ import annotations

import json
import urllib.request
import uuid
from typing import Protocol

from adwatch.config import Settings


class ZiniaoApiError(RuntimeError):
    pass


class HttpTransport(Protocol):
    def post(
        self, endpoint: str, payload: dict[str, object], timeout: int
    ) -> dict[str, object]: ...


class UrllibTransport:
    def post(
        self, endpoint: str, payload: dict[str, object], timeout: int
    ) -> dict[str, object]:
        request = urllib.request.Request(
            endpoint,
            data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            headers={"Content-Type": "application/json; charset=utf-8"},
            method="POST",
        )
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return json.loads(response.read().decode("utf-8"))


class ZiniaoClient:
    def __init__(
        self, settings: Settings, *, transport: HttpTransport | None = None
    ) -> None:
        if not settings.ziniao_ready:
            raise ZiniaoApiError("Ziniao credentials are incomplete")
        self.settings = settings
        self.transport = transport or UrllibTransport()

    def get_browser_list(self) -> list[dict]:
        response = self._call("getBrowserList")
        return list(response.get("browserList", []))

    def start_browser(self, browser_oauth: str) -> dict[str, object]:
        return self._call("startBrowser", browserOauth=browser_oauth)

    def stop_browser(self, browser_oauth: str) -> dict[str, object]:
        return self._call("stopBrowser", browserOauth=browser_oauth)

    def exit(self) -> dict[str, object]:
        return self._call("exit", include_credentials=False)

    def _call(
        self,
        action: str,
        *,
        include_credentials: bool = True,
        **values: object,
    ) -> dict[str, object]:
        payload: dict[str, object] = {
            "action": action,
            "requestId": str(uuid.uuid4()),
            **values,
        }
        if include_credentials:
            payload["userInfo"] = json.dumps(
                {
                    "company": self.settings.ziniao_company,
                    "username": self.settings.ziniao_username,
                    "password": self.settings.ziniao_password,
                },
                ensure_ascii=False,
            )
        response = self.transport.post(
            self.settings.ziniao_endpoint, payload, 120
        )
        status = response.get("statusCode", response.get("code", 0))
        if status not in (0, "0", None):
            message = response.get("err") or response.get("msg") or status
            raise ZiniaoApiError(str(message))
        return response
