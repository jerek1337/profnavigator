"""Планировщик напоминаний."""
from __future__ import annotations

import asyncio
import logging
from typing import Mapping

from .engine import Engine
from .models import Reply
from .storage import Storage

log = logging.getLogger(__name__)


class ReminderScheduler:
    def __init__(self, engine: Engine, storage: Storage, adapters: Mapping[str, object], *,
                 delay_seconds: float, max_count: int, interval: float) -> None:
        self.engine = engine
        self.storage = storage
        self.adapters = adapters          # платформа → адаптер с методом send_to_user
        self.delay_seconds = delay_seconds
        self.max_count = max_count
        self.interval = interval

    async def run(self) -> None:
        if not self.adapters:
            log.info("Напоминания не активны: не подключён ни один мессенджер")
            await asyncio.Event().wait()
        log.info("Напоминания: через %.1f ч после теста, до %d раз", self.delay_seconds / 3600, self.max_count)
        while True:
            try:
                sent = await self.tick()
                if sent:
                    log.info("Отправлено напоминаний: %d", sent)
            except Exception:  # noqa: BLE001
                log.exception("Ошибка планировщика напоминаний")
            await asyncio.sleep(self.interval)

    async def tick(self) -> int:
        threshold = self.engine.clock() - self.delay_seconds
        sent = 0
        for user in self.storage.due_reminders(threshold, self.max_count):
            adapter = self.adapters.get(user.platform)
            if adapter is None:  # у веб-пользователей нет push-канала
                continue

            async def send(reply: Reply, _adapter=adapter, _user=user) -> None:
                await _adapter.send_to_user(_user, reply)

            if await self.engine.send_reminder(user.uid, send, delay_seconds=self.delay_seconds,
                                               max_count=self.max_count):
                sent += 1
        return sent
