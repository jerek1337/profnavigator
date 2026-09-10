from __future__ import annotations

from core.engine import Engine
from core.links import Links
from core.models import Incoming, Reply
from core.storage import Storage


class FakeClock:
    def __init__(self, now: float = 1_800_000_000.0) -> None:
        self.now = now

    def __call__(self) -> float:
        return self.now


class FakeAI:
    """ИИ-заглушка: либо недоступен, либо возвращает заготовленный текст."""

    def __init__(self, answer: str | None = None) -> None:
        self.answer = answer
        self.calls: list[tuple[str, str]] = []

    @property
    def enabled(self) -> bool:
        return self.answer is not None

    async def generate(self, system, user, *, max_tokens=600, temperature=0.6):
        self.calls.append((system, user))
        return (self.answer, "fake") if self.answer else (None, None)

    def status(self):
        return []


def make_engine(ai: FakeAI | None = None, public_url: str = "", clock: FakeClock | None = None):
    clock = clock or FakeClock()
    storage = Storage(":memory:", clock=clock)
    links = Links("test-secret", public_url, "https://fin-olimp.ru")
    engine = Engine(storage, ai or FakeAI(), links, qa_daily_limit=3, clock=clock)
    return engine, storage, clock


class Chat:
    """Удобный «пользователь» для тестов сценария."""

    def __init__(self, engine: Engine, uid: str = "max:1", channel: str = "max") -> None:
        self.engine, self.uid, self.channel = engine, uid, channel
        self.replies: list[Reply] = []

    async def _send(self, reply: Reply) -> None:
        self.replies.append(reply)

    async def event(self, kind: str, value: str = "") -> list[Reply]:
        start = len(self.replies)
        await self.engine.handle(Incoming(self.uid, self.channel, kind, value), self._send)
        return self.replies[start:]

    async def start(self, payload: str = "") -> list[Reply]:
        return await self.event("start", payload)

    async def text(self, value: str) -> list[Reply]:
        return await self.event("text", value)

    async def press(self, label_part: str) -> list[Reply]:
        """Нажать кнопку последнего сообщения с кнопками по части подписи."""
        for reply in reversed(self.replies):
            if reply.buttons:
                for button in reply.flat_buttons():
                    if label_part in button.text and button.action:
                        return await self.event("action", button.action)
                raise AssertionError(f"Кнопка «{label_part}» не найдена: {[b.text for b in reply.flat_buttons()]}")
        raise AssertionError("Нет сообщений с кнопками")

    @property
    def last(self) -> Reply:
        return self.replies[-1]
