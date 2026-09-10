"""Адаптер мессенджера MAX (Bot API, long polling).

Адрес API задаётся в .env; сверяйтесь с https://dev.max.ru.
"""
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

class MaxApiError(Exception):
    def __init__(self, status: int, message: str) -> None:
        super().__init__(f"MAX API {status}: {message}")
        self.status = status

class MaxAdapter(BaseAdapter):
    platform = "max"
    update_types = "message_created,message_callback,bot_started"

    # Повтор POST мог бы создать второе сообщение в чате: ответ на него
    # мог потеряться уже после доставки.
    IDEMPOTENT_HTTP = frozenset({"GET", "HEAD", "PUT", "PATCH", "DELETE"})

    def __init__(self, engine: Engine, storage: Storage, token: str, *, base_url: str = "https://platform-api.max.ru",
                 auth_mode: str = "header", bot_link: str = "", poll_timeout: int = 30) -> None:
        super().__init__()
        self.engine = engine
        self.storage = storage
        self.token = token
        self.base_url = base_url.rstrip("/")
        self.auth_mode = auth_mode
        self.bot_link = bot_link
        self.poll_timeout = poll_timeout
        if auth_mode == "query":
            log.warning("MAX_AUTH_MODE=query: передача токена в query-параметрах больше не поддерживается "
                        "платформой MAX, запросы вернут 401. Используйте MAX_AUTH_MODE=header")

    async def api(self, method: str, path: str, *, params: dict[str, Any] | None = None,
                  payload: Any = None, timeout: float = 15.0, attempts: int = 4) -> Any:
        params = {k: str(v) for k, v in (params or {}).items()}
        headers: dict[str, str] = {}
        if self.auth_mode == "query":
            params["access_token"] = self.token
        else:
            headers["Authorization"] = self.token
        repeatable = method.upper() in self.IDEMPOTENT_HTTP
        last_error = ""
        for attempt in range(attempts):
            try:
                res = await request(method, self.base_url + path, params=params, json_body=payload,
                                    headers=headers, timeout=timeout)
            except OSError as exc:  # нет сети / таймаут
                if not repeatable:  # запрос мог дойти — дубль хуже ошибки
                    raise MaxApiError(0, str(exc)) from exc
                last_error = str(exc)
            else:
                if res.status == 429:  # лимит частоты: отклонено, повтор безопасен
                    last_error = f"HTTP {res.status}"
                elif res.status >= 500:
                    if not repeatable:
                        raise MaxApiError(res.status, res.text[:300])
                    last_error = f"HTTP {res.status}"
                elif res.status >= 400:
                    raise MaxApiError(res.status, res.text[:300])
                else:
                    return res.data if res.data is not None else {}
            if attempt < attempts - 1:
                await asyncio.sleep(min(2 ** attempt, 10))
        raise MaxApiError(0, last_error)

    async def run(self) -> None:
        me = await self.api("GET", "/me")
        log.info("MAX-бот подключён: %s", me.get("name") or me.get("username") or me.get("user_id"))
        with contextlib.suppress(Exception):
            await self.api("PATCH", "/me", payload={
                "commands": [{"name": name, "description": desc} for name, desc in C.COMMANDS]}, attempts=1)
        marker = self.storage.kv_get("max_marker")
        while True:
            params: dict[str, Any] = {"timeout": self.poll_timeout, "limit": 100, "types": self.update_types}
            if marker:
                params["marker"] = marker
            data = await self.api("GET", "/updates", params=params, timeout=self.poll_timeout + 15)
            for update in data.get("updates") or []:
                # marker в ответе nullable: не сдвинется — те же события приедут снова
                if self._is_duplicate(self._update_key(update)):
                    continue
                self._spawn(self.process(update))
            if data.get("marker") is not None:
                marker = str(data["marker"])
                self.storage.kv_set("max_marker", marker)

    @staticmethod
    def _update_key(update: dict[str, Any]) -> str | None:
        """Идентификатор события: сквозного update_id у MAX нет."""
        kind = update.get("update_type")
        if kind == "message_callback":
            callback = update.get("callback") or {}
            return f"cb:{callback['callback_id']}" if callback.get("callback_id") else None
        if kind == "message_created":
            mid = ((update.get("message") or {}).get("body") or {}).get("mid")
            return f"msg:{mid}" if mid else None
        if kind == "bot_started":
            uid = (update.get("user") or {}).get("user_id")
            return f"start:{uid}:{update.get('timestamp')}" if uid else None
        return None

    async def process(self, update: dict[str, Any]) -> None:
        kind = update.get("update_type")
        if kind == "bot_started":
            user = update.get("user") or {}
            uid, chat_id = user.get("user_id"), update.get("chat_id")
            inc_kind, value = "start", str(update.get("payload") or "")
        elif kind == "message_created":
            message = update.get("message") or {}
            user = message.get("sender") or {}
            if user.get("is_bot"):
                return
            recipient = message.get("recipient") or {}
            uid, chat_id = user.get("user_id"), recipient.get("chat_id")
            text = ((message.get("body") or {}).get("text") or "").strip()
            if recipient.get("chat_type") not in (None, "dialog"):
                # в школьном чате отвечаем только на команды и зовём в личку
                if text.startswith("/") and chat_id:
                    await self.send(chat_id, None, self._group_hint())
                return
            if text.lower().startswith("/start"):
                inc_kind, value = "start", text[6:].strip()
            else:
                inc_kind, value = "text", text
        elif kind == "message_callback":
            callback = update.get("callback") or {}
            user = callback.get("user") or {}
            recipient = (update.get("message") or {}).get("recipient") or {}
            uid, chat_id = user.get("user_id"), recipient.get("chat_id")
            inc_kind, value = "action", str(callback.get("payload") or "")
            if callback.get("callback_id"):
                self._spawn(self.answer_callback(callback["callback_id"], value))
        else:
            return
        if uid is None:
            return
        inc = Incoming(uid=f"max:{uid}", channel="max", kind=inc_kind, value=value,
                       name=user.get("first_name") or user.get("name"),
                       chat_id=str(chat_id) if chat_id else None)

        async def send(reply: Reply) -> None:
            await self.send(inc.chat_id, uid, reply)

        await self.engine.handle(inc, send)

    async def answer_callback(self, callback_id: str, action: str) -> None:
        notification = "✅ Принято" if action.startswith("ans:") else "👌"
        try:
            await self.api("POST", "/answers", params={"callback_id": callback_id},
                           payload={"notification": notification}, attempts=1)
        except Exception as exc:  # noqa: BLE001
            log.debug("answer_callback: %s", exc)

    async def send(self, chat_id: Any, user_id: Any, reply: Reply) -> None:
        params = {"chat_id": chat_id} if chat_id else {"user_id": user_id}
        chat_key = str(chat_id or user_id)
        await self._revoke_keyboard(chat_key)
        parts = split_text(render_text(reply))
        for i, part in enumerate(parts):
            body: dict[str, Any] = {"text": part}
            if i == len(parts) - 1 and reply.buttons:
                body["attachments"] = [self.keyboard(reply.buttons)]
            try:
                sent = await self.api("POST", "/messages", params=params, payload=body)
                if "attachments" in body and reply.meta.get("transient"):
                    message = (sent or {}).get("message") or sent or {}
                    mid = (message.get("body") or {}).get("mid")
                    self._remember_transient(chat_key, (mid, part) if mid else None)
            except MaxApiError as exc:
                if "attachments" not in body or not 400 <= exc.status < 500:
                    raise
                # клавиатуру не приняли — отправляем текст, а ссылки дописываем в сообщение
                log.warning("Клавиатура отклонена (%s), отправляю без кнопок", exc)
                links = [f"{b.text}: {b.url}" for b in reply.flat_buttons() if b.url]
                await self.api("POST", "/messages", params=params,
                               payload={"text": "\n\n".join([part, *links]) if links else part})

    async def _revoke_keyboard(self, chat_key: str) -> None:
        """Убрать кнопки у предыдущего вопроса: тот же текст, но без вложений."""
        remembered = self._take_transient(chat_key)
        if not remembered:
            return
        mid, text = remembered
        with contextlib.suppress(Exception):  # редактирование необязательно
            # пустой массив = удалить все вложения; null или отсутствие поля ничего не меняют
            await self.api("PUT", "/messages", params={"message_id": mid},
                           payload={"text": text, "attachments": [], "notify": False}, attempts=1)

    async def send_to_user(self, user: User, reply: Reply) -> None:
        await self.send(user.chat_id, user.external_id, reply)

    @staticmethod
    def keyboard(rows: list[list[Button]]) -> dict[str, Any]:
        buttons = []
        for row in rows:
            buttons.append([
                {"type": "link", "text": b.text, "url": b.url} if b.url
                else {"type": "callback", "text": b.text, "payload": b.action or "menu"}
                for b in row
            ])
        return {"type": "inline_keyboard", "payload": {"buttons": buttons}}

    def _group_hint(self) -> Reply:
        rows = [[Button("💬 Написать боту", url=self.bot_link)]] if self.bot_link.startswith("https://") else []
        return Reply(C.GROUP_HINT, rows)
