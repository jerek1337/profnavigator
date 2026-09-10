"""Консольный режим: проверка сценария без токенов и интернета (python main.py --cli)."""
from __future__ import annotations

import asyncio
import string
import sys
import threading
from typing import TextIO

from core.engine import Engine
from core.models import Button, Incoming, Reply
from core.text import render_text

LETTERS = string.ascii_lowercase

class CliAdapter:
    platform = "cli"

    def __init__(self, engine: Engine, *, uid: str = "cli:local", stdin: TextIO | None = None,
                 stdout: TextIO | None = None) -> None:
        self.engine = engine
        self.uid = uid
        self.stdin = stdin or sys.stdin
        self.out = stdout or sys.stdout
        self.buttons: list[Button] = []

    def _print(self, text: str = "") -> None:
        print(text, file=self.out, flush=True)

    async def _send(self, reply: Reply) -> None:
        self._print()
        self._print(render_text(reply))
        flat = reply.flat_buttons()
        if flat:
            self.buttons = flat[:len(LETTERS)]
            for letter, button in zip(LETTERS, self.buttons):
                suffix = f"  ({button.url})" if button.url else ""
                self._print(f"  [{letter}] {button.text}{suffix}")

    async def run(self) -> None:
        loop = asyncio.get_running_loop()
        queue: asyncio.Queue[str | None] = asyncio.Queue()

        def reader() -> None:  # daemon-поток: не мешает выходу по Ctrl+C
            for line in self.stdin:
                loop.call_soon_threadsafe(queue.put_nowait, line.rstrip("\n"))
            loop.call_soon_threadsafe(queue.put_nowait, None)

        threading.Thread(target=reader, daemon=True).start()
        self._print("ПрофНавигатор FinTech — консольный режим. Введите букву кнопки или текст; «выход» — завершить.")
        await self.engine.handle(Incoming(self.uid, "cli", "start"), self._send)
        while True:
            line = await queue.get()
            if line is None or line.strip().lower() in {"exit", "quit", "выход"}:
                break
            text = line.strip()
            index = LETTERS.find(text.lower()) if len(text) == 1 else -1
            if 0 <= index < len(self.buttons):
                button = self.buttons[index]
                if button.url:
                    self._print(f"🔗 Ссылка: {button.url}")
                    continue
                await self.engine.handle(Incoming(self.uid, "cli", "action", button.action or "menu"), self._send)
            else:
                await self.engine.handle(Incoming(self.uid, "cli", "text", text), self._send)
