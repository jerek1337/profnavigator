"""Адаптер Telegram (дополнительная интеграция; сценарий тот же, что в MAX)."""
from __future__ import annotations

import asyncio
import contextlib
import logging
from typing import Any

from core import content as C
from core.engine import Engine
from core.http import request
from core.models import Button, Incoming, Reply, User
from core.storage import Storage
from core.text import render_text, split_text

from .base import BaseAdapter

log = logging.getLogger(__name__)

class TelegramError(Exception):
    def __init__(self, status: int, message: str) -> None:
        super().__init__(f"Telegram API {status}: {message}")
        self.status = status

class TelegramAdapter(BaseAdapter):
    platform = "tg"

    # Повторять после обрыва связи можно только эти методы: ответ на sendMessage
    # мог потеряться уже после доставки, и повтор дал бы дубль в чате.
    IDEMPOTENT = frozenset({"getMe", "getUpdates", "setMyCommands", "answerCallbackQuery",
                            "editMessageReplyMarkup"})

    def __init__(self, engine: Engine, storage: Storage, token: str, *, bot_link: str = "", poll_timeout: int = 25) -> None:
        super().__init__()
        self.engine = engine
        self.storage = storage
        self.base_url = f"https://api.telegram.org/bot{token}"
        self.bot_link = bot_link
        self.poll_timeout = poll_timeout

    async def api(self, method: str, payload: dict[str, Any] | None = None, *, timeout: float = 15.0,
                  attempts: int = 4) -> Any:
        repeatable = method in self.IDEMPOTENT
        last_error = ""
        for attempt in range(attempts):
            delay = min(2 ** attempt, 10)
            try:
                res = await request("POST", f"{self.base_url}/{method}", json_body=payload or {}, timeout=timeout)
            except OSError as exc:
                if not repeatable:  # запрос мог дойти — дубль хуже ошибки
                    raise TelegramError(0, str(exc)) from exc
                last_error = str(exc)
            else:
                data = res.data if isinstance(res.data, dict) else {}
                if res.status == 429:  # флуд-контроль: не доставлено, повтор безопасен
                    last_error = f"HTTP {res.status}"
                    delay = (data.get("parameters") or {}).get("retry_after", delay)
                elif res.status >= 500:
                    if not repeatable:
                        raise TelegramError(res.status, res.text[:300])
                    last_error = f"HTTP {res.status}"
                elif not data.get("ok"):
                    raise TelegramError(res.status, res.text[:300])
                else:
                    return data.get("result")
            if attempt < attempts - 1:
                await asyncio.sleep(delay)
        raise TelegramError(0, last_error)

    async def run(self) -> None:
        me = await self.api("getMe")
        log.info("Telegram-бот подключён: @%s", (me or {}).get("username"))
        with contextlib.suppress(Exception):
            await self.api("setMyCommands", {"commands": [{"command": n, "description": d} for n, d in C.COMMANDS]},
                           attempts=1)
        offset = int(self.storage.kv_get("tg_offset") or 0)
        while True:
            updates = await self.api("getUpdates", {"offset": offset, "timeout": self.poll_timeout,
                                                    "allowed_updates": ["message", "callback_query"]},
                                     timeout=self.poll_timeout + 15)
            for update in updates or []:
                offset = max(offset, int(update["update_id"]) + 1)
                if self._is_duplicate(str(update["update_id"])):
                    continue
                self._spawn(self.process(update))
            self.storage.kv_set("tg_offset", str(offset))

    async def process(self, update: dict[str, Any]) -> None:
        if "callback_query" in update:
            cq = update["callback_query"]
            user = cq.get("from") or {}
            chat = (cq.get("message") or {}).get("chat") or {}
            self._spawn(self._answer_callback(cq.get("id")))
            kind, value = "action", cq.get("data") or ""
        elif "message" in update:
            message = update["message"]
            user = message.get("from") or {}
            chat = message.get("chat") or {}
            text = (message.get("text") or "").strip()
            if user.get("is_bot"):
                return
            if chat.get("type") != "private":
                if text.startswith("/"):
                    await self.send(chat.get("id"), self._group_hint())
                return
            if text.lower().startswith("/start"):
                kind, value = "start", text[6:].strip()
            else:
                kind, value = "text", text
        else:
            return
        if not user.get("id") or not chat.get("id"):
            return
        inc = Incoming(uid=f"tg:{user['id']}", channel="tg", kind=kind, value=value,
                       name=user.get("first_name"), chat_id=str(chat["id"]))

        async def send(reply: Reply) -> None:
            await self.send(chat["id"], reply)

        await self.engine.handle(inc, send)

    async def _answer_callback(self, callback_id: str | None) -> None:
        if callback_id:
            with contextlib.suppress(Exception):
                await self.api("answerCallbackQuery", {"callback_query_id": callback_id}, attempts=1)

    async def send(self, chat_id: Any, reply: Reply) -> None:
        await self._revoke_keyboard(chat_id)
        parts = split_text(render_text(reply))
        sent: Any = None
        for i, part in enumerate(parts):
            payload: dict[str, Any] = {"chat_id": chat_id, "text": part, "disable_web_page_preview": True}
            if i == len(parts) - 1 and reply.buttons:
                payload["reply_markup"] = {"inline_keyboard": [
                    [{"text": b.text, "url": b.url} if b.url else {"text": b.text, "callback_data": b.action or "menu"}
                     for b in row] for row in reply.buttons]}
            sent = await self.api("sendMessage", payload)
        if reply.meta.get("transient") and reply.buttons and isinstance(sent, dict):
            self._remember_transient(str(chat_id), sent.get("message_id"))

    async def _revoke_keyboard(self, chat_id: Any) -> None:
        """Убрать кнопки у предыдущего вопроса."""
        message_id = self._take_transient(str(chat_id))
        if message_id is None:
            return
        with contextlib.suppress(Exception):  # сообщения нет или оно уже без кнопок
            await self.api("editMessageReplyMarkup", {"chat_id": chat_id, "message_id": message_id}, attempts=1)

    async def send_to_user(self, user: User, reply: Reply) -> None:
        await self.send(user.chat_id or user.external_id, reply)

    def _group_hint(self) -> Reply:
        rows = [[Button("💬 Написать боту", url=self.bot_link)]] if self.bot_link.startswith("https://") else []
        return Reply(C.GROUP_HINT, rows)
