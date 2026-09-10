"""
Веб-интерфейс на стандартной библиотеке (http.server).

Маршруты:
  GET  /               — чат (мини-приложение, работает и в iframe-виджете)
  POST /api/chat       — тот же сценарий, что в мессенджерах
  GET  /widget.js      — виджет для школьного сайта (одна строка <script>)
  GET  /dashboard      — аналитика (нужен ADMIN_TOKEN)
  GET  /api/stats      — данные аналитики в JSON
  GET  /go/finatlon    — переход на fin-olimp.ru с учётом клика
  GET  /qr.svg         — QR-код на бота (?source=corridor — метка источника)
  GET  /poster         — плакат для печати «Узнай свою профессию за 3 минуты»
  GET  /health         — проверка работоспособности
"""
from __future__ import annotations

import asyncio
import html
import hmac
import io
import json
import logging
import re
import secrets
import threading
import time
from collections import deque
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Callable
from urllib.parse import parse_qs, urlsplit

from core import content as C
from core.analytics import collect_stats
from core.engine import Engine
from core.links import Links
from core.models import Incoming, Reply
from core.storage import Storage

log = logging.getLogger(__name__)

STATIC_DIR = Path(__file__).resolve().parent.parent / "web"
UID_RE = re.compile(r"(web|max|tg|cli):[A-Za-z0-9_-]{1,64}")
SOURCE_RE = re.compile(r"[A-Za-z0-9_-]{1,32}")
MAX_BODY = 8 * 1024

class RateLimiter:
    """Не больше `limit` запросов за `window` секунд с одного адреса."""

    def __init__(self, limit: int = 40, window: float = 10.0) -> None:
        self.limit, self.window = limit, window
        self._hits: dict[str, deque[float]] = {}
        self._lock = threading.Lock()

    def allow(self, key: str) -> bool:
        now = time.monotonic()
        with self._lock:
            if len(self._hits) > 10_000:
                self._hits.clear()
            hits = self._hits.setdefault(key, deque())
            while hits and hits[0] <= now - self.window:
                hits.popleft()
            if len(hits) >= self.limit:
                return False
            hits.append(now)
            return True

class WebServer:
    platform = "web"

    def __init__(self, engine: Engine, storage: Storage, links: Links, settings: Any, *,
                 ai: Any = None, adapters: tuple = (), static_dir: Path = STATIC_DIR) -> None:
        self.engine = engine
        self.storage = storage
        self.links = links
        self.settings = settings
        self.ai = ai
        self.adapters = adapters
        self.static_dir = static_dir
        self.limiter = RateLimiter()
        self.loop: asyncio.AbstractEventLoop | None = None
        self.httpd: ThreadingHTTPServer | None = None
        self.ready = asyncio.Event()
        self.started_at = time.time()
        self.routes: dict[tuple[str, str], Callable[[BaseHTTPRequestHandler, dict[str, str]], None]] = {
            ("GET", "/"): self.page_index,
            ("GET", "/index.html"): self.page_index,
            ("GET", "/widget.js"): self.page_widget,
            ("GET", "/dashboard"): self.page_dashboard,
            ("GET", "/poster"): self.page_poster,
            ("GET", "/qr.svg"): self.qr_svg,
            ("GET", "/go/finatlon"): self.go_finatlon,
            ("GET", "/health"): self.health,
            ("GET", "/api/stats"): self.api_stats,
            ("POST", "/api/chat"): self.api_chat,
        }

    @property
    def port(self) -> int:
        return self.httpd.server_address[1] if self.httpd else 0

    async def run(self) -> None:
        self.loop = asyncio.get_running_loop()
        server = self

        class Handler(BaseHTTPRequestHandler):
            server_version = "ProfNavigator/1.0"

            def do_GET(self) -> None:  # noqa: N802
                server.dispatch(self, "GET")

            def do_POST(self) -> None:  # noqa: N802
                server.dispatch(self, "POST")

            def log_message(self, fmt: str, *args: Any) -> None:
                log.debug("web %s - " + fmt, self.client_address[0], *args)

        httpd = ThreadingHTTPServer((self.settings.web_host, self.settings.web_port), Handler)
        httpd.daemon_threads = True
        self.httpd = httpd
        threading.Thread(target=httpd.serve_forever, kwargs={"poll_interval": 0.5}, daemon=True).start()
        log.info("Веб-интерфейс: http://%s:%d  (дашборд: /dashboard?token=…)",
                 "localhost" if self.settings.web_host in ("0.0.0.0", "") else self.settings.web_host, self.port)
        self.ready.set()
        try:
            await asyncio.Event().wait()
        finally:
            def stop() -> None:
                httpd.shutdown()
                httpd.server_close()
            threading.Thread(target=stop, daemon=True).start()

    def dispatch(self, h: BaseHTTPRequestHandler, method: str) -> None:
        parts = urlsplit(h.path)
        query = {k: v[0] for k, v in parse_qs(parts.query).items()}
        handler = self.routes.get((method, parts.path.rstrip("/") or "/"))
        try:
            if handler is None:
                self.send(h, 404, {"error": "not_found"})
            else:
                handler(h, query)
        except Exception:  # noqa: BLE001
            log.exception("Ошибка веб-запроса %s %s", method, parts.path)
            try:
                self.send(h, 500, {"error": "internal", "message": "Внутренняя ошибка, попробуйте ещё раз"})
            except Exception:  # noqa: BLE001
                pass

    def send(self, h: BaseHTTPRequestHandler, status: int, body: Any, content_type: str | None = None,
             headers: dict[str, str] | None = None) -> None:
        if isinstance(body, (dict, list)):
            data = json.dumps(body, ensure_ascii=False).encode("utf-8")
            content_type = content_type or "application/json; charset=utf-8"
        elif isinstance(body, str):
            data = body.encode("utf-8")
            content_type = content_type or "text/plain; charset=utf-8"
        else:
            data = body or b""
        h.send_response(status)
        h.send_header("Content-Type", content_type or "application/octet-stream")
        h.send_header("Content-Length", str(len(data)))
        h.send_header("X-Content-Type-Options", "nosniff")
        h.send_header("Referrer-Policy", "no-referrer")
        for key, value in (headers or {}).items():
            h.send_header(key, value)
        h.end_headers()
        h.wfile.write(data)

    def client_ip(self, h: BaseHTTPRequestHandler) -> str:
        if self.settings.trust_proxy and h.headers.get("X-Forwarded-For"):
            return h.headers["X-Forwarded-For"].split(",")[0].strip()
        return h.client_address[0]

    def static(self, name: str) -> str | None:
        path = self.static_dir / name
        return path.read_text(encoding="utf-8") if path.is_file() else None

    def page_index(self, h: BaseHTTPRequestHandler, q: dict[str, str]) -> None:
        page = self.static("index.html")
        if page is None:
            return self.send(h, 404, "index.html не найден")
        self.send(h, 200, page, "text/html; charset=utf-8", {"Cache-Control": "no-cache"})

    def page_widget(self, h: BaseHTTPRequestHandler, q: dict[str, str]) -> None:
        script = self.static("widget.js") or ""
        self.send(h, 200, script, "application/javascript; charset=utf-8", {"Cache-Control": "max-age=300"})

    def page_dashboard(self, h: BaseHTTPRequestHandler, q: dict[str, str]) -> None:
        page = self.static("dashboard.html") or "dashboard.html не найден"
        self.send(h, 200, page, "text/html; charset=utf-8", {"Cache-Control": "no-cache"})

    def page_poster(self, h: BaseHTTPRequestHandler, q: dict[str, str]) -> None:
        source = q.get("source", "poster")
        source = source if SOURCE_RE.fullmatch(source) else "poster"
        page = (self.static("poster.html") or "poster.html не найден")
        page = page.replace("{{QR_SRC}}", html.escape(f"qr.svg?source={source}"))
        page = page.replace("{{LINK}}", html.escape(self.settings.bot_link or "Задайте BOT_LINK в .env"))
        self.send(h, 200, page, "text/html; charset=utf-8")

    def api_chat(self, h: BaseHTTPRequestHandler, q: dict[str, str]) -> None:
        if not self.limiter.allow(self.client_ip(h)):
            return self.send(h, 429, {"error": "rate_limited", "message": "Слишком много сообщений подряд"})
        try:
            length = int(h.headers.get("Content-Length") or 0)
        except ValueError:
            length = -1
        if not 0 <= length <= MAX_BODY:
            return self.send(h, 413, {"error": "too_large"})
        try:
            body = json.loads(h.rfile.read(length) or b"{}")
        except ValueError:
            return self.send(h, 400, {"error": "bad_json"})
        if not isinstance(body, dict) or body.get("kind") not in {"start", "text", "action"}:
            return self.send(h, 400, {"error": "bad_request"})

        kind, value = body["kind"], str(body.get("value") or "")[:C.MAX_INPUT_LEN]
        uid, sig = body.get("uid"), body.get("sig")
        if not (isinstance(uid, str) and isinstance(sig, str) and UID_RE.fullmatch(uid) and self.links.verify(uid, sig)):
            uid = f"web:{secrets.token_hex(12)}"   # новая или подделанная сессия → новый профиль
            sig = self.links.sign(uid)
            if kind != "start":
                kind, value = "start", ""

        replies: list[dict[str, Any]] = []

        async def collect(reply: Reply) -> None:
            replies.append(reply.to_dict())

        future = asyncio.run_coroutine_threadsafe(
            self.engine.handle(Incoming(uid=uid, channel="web", kind=kind, value=value), collect), self.loop)
        future.result(timeout=self.settings.ai_total_timeout * 2 + 30)
        self.send(h, 200, {"uid": uid, "sig": sig, "replies": replies}, headers={"Cache-Control": "no-store"})

    def _admin_ok(self, q: dict[str, str]) -> bool:
        token = self.settings.admin_token
        return bool(token) and hmac.compare_digest(q.get("token", ""), token)

    def api_stats(self, h: BaseHTTPRequestHandler, q: dict[str, str]) -> None:
        if not self.settings.admin_token:
            return self.send(h, 403, {"error": "disabled", "message": "Задайте ADMIN_TOKEN в .env, чтобы включить дашборд"})
        if not self._admin_ok(q):
            return self.send(h, 403, {"error": "forbidden", "message": "Неверный токен"})
        self.send(h, 200, collect_stats(self.storage), headers={"Cache-Control": "no-store"})

    def go_finatlon(self, h: BaseHTTPRequestHandler, q: dict[str, str]) -> None:
        uid = q.get("u", "")
        if UID_RE.fullmatch(uid):
            try:
                self.storage.log_event(uid, "link", "finatlon_click")
            except Exception:  # noqa: BLE001
                log.exception("Не удалось записать клик")
        self.send(h, 302, b"", "text/plain", {"Location": C.FINATLON_URL})

    def qr_svg(self, h: BaseHTTPRequestHandler, q: dict[str, str]) -> None:
        if q.get("target") == "web":
            data = self.links.public_url
        else:
            data = self.settings.bot_link
            source = q.get("source", "")
            if data and SOURCE_RE.fullmatch(source):
                data += ("&" if "?" in data else "?") + f"start={source}"
        if not data:
            return self.send(h, 400, "Задайте BOT_LINK (или PUBLIC_URL для target=web) в .env")
        try:
            import qrcode
            import qrcode.image.svg
        except ImportError:
            return self.send(h, 501, "Для QR-кода установите пакет: pip install qrcode")
        image = qrcode.make(data, image_factory=qrcode.image.svg.SvgPathImage, box_size=20, border=2)
        buffer = io.BytesIO()
        image.save(buffer)
        self.send(h, 200, buffer.getvalue(), "image/svg+xml", {"Cache-Control": "max-age=3600"})

    def health(self, h: BaseHTTPRequestHandler, q: dict[str, str]) -> None:
        try:
            db_ok = self.storage.ping()
        except Exception:  # noqa: BLE001
            db_ok = False
        body = {
            "status": "ok" if db_ok else "degraded",
            "database": db_ok,
            "uptime_seconds": int(time.time() - self.started_at),
            "platforms": ["web", *[a.platform for a in self.adapters]],
            "ai": self.ai.status() if self.ai else [],
        }
        self.send(h, 200 if db_ok else 503, body, headers={"Cache-Control": "no-store"})
