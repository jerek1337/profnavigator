"""Базовый класс адаптера платформы."""
from __future__ import annotations

import asyncio
import logging
from abc import ABC, abstractmethod
from typing import Any, Coroutine

from core.models import Reply, User

log = logging.getLogger(__name__)

class BaseAdapter(ABC):
    """Чтобы подключить новую платформу (VK, Сферум, школьный портал…), реализуйте два метода."""

    platform: str = "base"

    TRANSIENT_LIMIT = 5000   # чатов в памяти для снятия устаревших клавиатур
    SEEN_LIMIT = 2000        # обработанных обновлений в памяти

    def __init__(self) -> None:
        self._tasks: set[asyncio.Task] = set()
        self._transient: dict[str, Any] = {}
        self._seen: dict[str, None] = {}

    @abstractmethod
    async def run(self) -> None:
        """Получение событий (long polling / webhook) и передача их в движок."""

    @abstractmethod
    async def send_to_user(self, user: User, reply: Reply) -> None:
        """Отправка сообщения пользователю по инициативе бота (напоминания)."""

    def _spawn(self, coro: Coroutine) -> None:
        task = asyncio.create_task(coro)
        self._tasks.add(task)
        task.add_done_callback(self._task_done)

    def _task_done(self, task: asyncio.Task) -> None:
        self._tasks.discard(task)
        if not task.cancelled() and task.exception():
            log.error("Фоновая задача %s завершилась ошибкой", self.platform, exc_info=task.exception())

    def _remember_transient(self, chat_key: str, value: Any) -> None:
        """Запомнить сообщение, у которого при следующей отправке снимаем кнопки."""
        if not chat_key or value is None:
            return
        if len(self._transient) >= self.TRANSIENT_LIMIT:
            self._transient.clear()
        self._transient[chat_key] = value

    def _take_transient(self, chat_key: str) -> Any:
        return self._transient.pop(chat_key, None) if chat_key else None

    def _is_duplicate(self, key: str | None) -> bool:
        """Уже обрабатывали такое обновление? Long polling умеет доставлять повторно."""
        if not key:
            return False
        if key in self._seen:
            return True
        self._seen[key] = None  # dict хранит порядок вставки — вытесняем самые старые
        while len(self._seen) > self.SEEN_LIMIT:
            self._seen.pop(next(iter(self._seen)))
        return False
