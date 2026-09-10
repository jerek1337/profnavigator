"""Асинхронный HTTP-клиент на стандартной библиотеке — никаких внешних зависимостей."""
from __future__ import annotations

import asyncio
import json
import ssl
import threading
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from typing import Any, Callable, TypeVar

T = TypeVar("T")
USER_AGENT = "ProfNavigatorFinTech/1.0"

@dataclass
class HttpResult:
    status: int
    data: Any
    text: str

    @property
    def ok(self) -> bool:
        return 200 <= self.status < 300

def run_in_thread(fn: Callable[..., T], *args: Any) -> "asyncio.Future[T]":
    """Запускает блокирующую функцию в фоновом daemon-потоке.

    В отличие от стандартного пула потоков, daemon-поток не задерживает выход
    из программы по Ctrl+C, даже если в этот момент идёт long polling.
    """
    loop = asyncio.get_running_loop()
    future: asyncio.Future[T] = loop.create_future()

    def resolve(ok: bool, value: Any) -> None:
        if future.done():
            return
        if ok:
            future.set_result(value)
        else:
            future.set_exception(value)

    def worker() -> None:
        try:
            outcome = (True, fn(*args))
        except Exception as exc:  # noqa: BLE001
            outcome = (False, exc)
        try:
            loop.call_soon_threadsafe(resolve, *outcome)
        except RuntimeError:  # цикл событий уже закрыт
            pass

    threading.Thread(target=worker, daemon=True).start()
    return future

def _request_sync(method: str, url: str, params: dict[str, Any] | None, json_body: Any,
                  form: dict[str, str] | None, headers: dict[str, str] | None,
                  timeout: float, ssl_context: ssl.SSLContext | None) -> HttpResult:
    if params:
        query = urllib.parse.urlencode({k: v for k, v in params.items() if v is not None})
        url = f"{url}{'&' if '?' in url else '?'}{query}"
    all_headers = {"User-Agent": USER_AGENT, "Accept": "application/json"}
    data: bytes | None = None
    if json_body is not None:
        data = json.dumps(json_body, ensure_ascii=False).encode("utf-8")
        all_headers["Content-Type"] = "application/json; charset=utf-8"
    elif form is not None:
        data = urllib.parse.urlencode(form).encode("utf-8")
        all_headers["Content-Type"] = "application/x-www-form-urlencoded"
    all_headers.update(headers or {})
    req = urllib.request.Request(url, data=data, method=method.upper(), headers=all_headers)
    try:
        with urllib.request.urlopen(req, timeout=timeout, context=ssl_context) as resp:
            status, raw = resp.status, resp.read()
    except urllib.error.HTTPError as exc:
        status = exc.code
        try:
            raw = exc.read()
        except OSError:
            raw = b""
    text = raw.decode("utf-8", errors="replace")
    try:
        parsed = json.loads(text) if text.strip() else None
    except ValueError:
        parsed = None
    return HttpResult(status, parsed, text)

async def request(method: str, url: str, *, params: dict[str, Any] | None = None, json_body: Any = None,
                  form: dict[str, str] | None = None, headers: dict[str, str] | None = None,
                  timeout: float = 20.0, ssl_context: ssl.SSLContext | None = None) -> HttpResult:
    """HTTP-запрос. Сетевые ошибки (нет связи, таймаут) выбрасываются как OSError."""
    return await run_in_thread(_request_sync, method, url, params, json_body, form, headers, timeout, ssl_context)
