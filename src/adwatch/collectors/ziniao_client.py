from __future__ import annotations

import json
import subprocess
import time
import urllib.request
import uuid
from collections.abc import Callable
from pathlib import Path
from typing import Protocol

from adwatch.config import Settings


class ZiniaoApiError(RuntimeError):
    pass


class ZiniaoCliClient:
    def __init__(
        self,
        *,
        executable: str = "ziniao-cli",
        runner: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
        sleeper: Callable[[float], None] = time.sleep,
    ) -> None:
        self.executable = executable
        self.runner = runner
        self.sleeper = sleeper

    def get_store_list(self) -> list[dict]:
        response = self._run("store", "list", "--format", "json")
        data = response.get("data", {})
        if isinstance(data, dict):
            return list(data.get("items", []))
        return list(data)

    def page_content(
        self,
        store_id: str,
        *,
        url: str | None = None,
        content_format: str = "structured",
    ) -> dict[str, object]:
        if url:
            target = json.dumps(url)
            self.page_exec(store_id, f'location.href={target}; "navigating"')
            # 用文章ID做URL匹配，应对SPA中动态路径段（如 /portal/4/article/xxx）
            article_id = ""
            for part in url.split("/"):
                if part.isdigit() and len(part) >= 4:
                    article_id = part
                    break
            for attempt in range(15):
                current_url = str(self.page_exec(store_id, "location.href"))
                if article_id:
                    if article_id in current_url:
                        break
                elif current_url.startswith(url):
                    break
                if attempt + 1 < 15:
                    self.sleeper(1)
            else:
                raise ZiniaoApiError(
                    f"Page did not reach expected URL after 15 attempts: {url}"
                )
        arguments = [
            "page",
            "content",
            "--store-id",
            store_id,
            "--content-format",
            content_format,
        ]
        page: object = {}
        for attempt in range(10):
            response = self._run(*arguments)
            data = response.get("data", {})
            page = data.get("data", data) if isinstance(data, dict) else data
            content = page.get("content", {}) if isinstance(page, dict) else {}
            if isinstance(content, dict) and (
                content.get("text") or content.get("links")
            ):
                break
            if attempt + 1 < 10:
                self.sleeper(1)
        data = response.get("data", {})
        if not isinstance(data, dict):
            return {"content": data}
        if not isinstance(page, dict):
            return {"content": page}
        content = page.get("content", {})
        if not isinstance(content, dict):
            content = {"text": content}
        links = content.get("links", [])
        dom_script = """
            JSON.stringify({
              url: location.href,
              title: document.title,
              text: (document.body.innerText || '').trim(),
              headings: Array.from(document.querySelectorAll('h1,h2,h3'))
                .map(x => (x.innerText || x.textContent || '').trim())
                .filter(Boolean),
              links: Array.from(document.querySelectorAll('a[href]'))
                .map(a => ({href: a.href, text:
                  (a.innerText || a.textContent || '').trim()}))
            })
            """
        dom = self.page_exec(store_id, dom_script)
        if url and "/edu/home" in url:
            for attempt in range(9):
                dom_links = dom.get("links", []) if isinstance(dom, dict) else []
                if len(dom_links) >= 20:
                    break
                self.sleeper(1)
                dom = self.page_exec(store_id, dom_script)
        elif url and ("/edu/article/" in url or "/portal/article/" in url):
            for attempt in range(9):
                dom_text = str(dom.get("text", "")) if isinstance(dom, dict) else ""
                if len(dom_text) > 700 and "window.HTML_LOADED" not in dom_text:
                    break
                self.sleeper(1)
                dom = self.page_exec(store_id, dom_script)
        if isinstance(dom, dict):
            page = {**page, **{k: v for k, v in dom.items() if v}}
            if dom.get("text"):
                content["text"] = dom["text"]
            if dom.get("headings"):
                content["headings"] = dom["headings"]
            if dom.get("links"):
                links = dom["links"]
        return {
            "url": page.get("url", url or ""),
            "title": page.get("title", ""),
            "language": page.get("language", "unknown"),
            "paragraphs": [str(content.get("text", ""))],
            "headings": content.get("headings", []),
            "links": [
                str(link.get("href"))
                for link in links
                if isinstance(link, dict) and link.get("href")
            ],
        }

    def page_exec(self, store_id: str, script: str) -> object:
        response = self._run(
            "page",
            "exec",
            "--store-id",
            store_id,
            "--script",
            script,
        )
        result = response.get("data", {}).get("data", {}).get("result")
        if isinstance(result, str):
            try:
                return json.loads(result)
            except json.JSONDecodeError:
                return result
        return result

    def page_query(self, store_id: str, selector: str) -> object:
        response = self._run(
            "page",
            "query",
            "--store-id",
            store_id,
            "--selector",
            selector,
        )
        return response.get("data")

    def page_input(
        self,
        store_id: str,
        selector: str,
        text: str,
        *,
        clear: bool = False,
    ) -> None:
        arguments = [
            "page",
            "input",
            "--store-id",
            store_id,
            "--selector",
            selector,
            "--text",
            text,
        ]
        if clear:
            arguments.append("--clear")
        self._run(*arguments)

    def page_click(self, store_id: str, selector: str) -> None:
        self._run(
            "page",
            "click",
            "--store-id",
            store_id,
            "--selector",
            selector,
        )

    def page_screenshot(self, store_id: str, destination: Path) -> str:
        path = destination.resolve()
        path.parent.mkdir(parents=True, exist_ok=True)
        self._run(
            "page",
            "screenshot",
            "--store-id",
            store_id,
            "--path",
            str(path),
        )
        return str(path)

    def navigate_and_exec(
        self,
        store_id: str,
        url: str,
        script: str,
        *,
        expected_url: str,
        require_nonempty: bool = False,
        attempts: int = 15,
    ) -> object:
        target = json.dumps(url)
        self.page_exec(store_id, f'location.href={target}; "navigating"')
        for attempt in range(attempts):
            current_url = self.page_exec(store_id, "location.href")
            if expected_url in str(current_url):
                result = self.page_exec(store_id, script)
                if not require_nonempty or result:
                    return result
            if attempt + 1 < attempts:
                self.sleeper(1)
        raise ZiniaoApiError(
            f"Page did not reach expected URL or data after {attempts} attempts: "
            f"{expected_url}"
        )

    def page_exec_until(
        self,
        store_id: str,
        script: str,
        *,
        ready: Callable[[object], bool],
        attempts: int = 3,
    ) -> object:
        for attempt in range(attempts):
            result = self.page_exec(store_id, script)
            if ready(result):
                return result
            if attempt + 1 < attempts:
                self.sleeper(1)
        raise ZiniaoApiError(
            f"Page data was not ready after {attempts} attempts"
        )

    def _run(self, *arguments: str) -> dict[str, object]:
        command = [self.executable, *arguments]
        completed = self.runner(
            command,
            capture_output=True,
            text=True,
            timeout=120,
            check=False,
        )
        if completed.returncode:
            message = completed.stderr.strip() or completed.stdout.strip()
            raise ZiniaoApiError(message or f"Command failed: {command[1]}")
        try:
            response = json.loads(completed.stdout)
        except json.JSONDecodeError as error:
            raise ZiniaoApiError("Ziniao CLI returned invalid JSON") from error
        if not response.get("ok", False):
            error = response.get("error", {})
            message = error.get("message") if isinstance(error, dict) else error
            raise ZiniaoApiError(str(message or "Ziniao CLI request failed"))
        return response


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
