"""Модели данных, общие для движка и всех платформ."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from . import content as C

@dataclass
class Button:
    """Кнопка: либо действие (action), либо ссылка (url)."""
    text: str
    action: str | None = None
    url: str | None = None

    def to_dict(self) -> dict[str, str]:
        data = {"text": self.text}
        if self.action:
            data["action"] = self.action
        if self.url:
            data["url"] = self.url
        return data

@dataclass
class Reply:
    """Платформонезависимый ответ бота."""
    text: str
    buttons: list[list[Button]] = field(default_factory=list)
    meta: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "text": self.text,
            "buttons": [[b.to_dict() for b in row] for row in self.buttons],
            "meta": self.meta,
        }

    def flat_buttons(self) -> list[Button]:
        return [b for row in self.buttons for b in row]

@dataclass
class Incoming:
    """Входящее событие от любой платформы."""
    uid: str                 # "<платформа>:<id>", например "max:123"
    channel: str             # откуда пришло: max / tg / web / cli
    kind: str                # start | text | action
    value: str = ""
    name: str | None = None
    chat_id: str | None = None

@dataclass
class User:
    uid: str
    chat_id: str | None = None
    name: str | None = None
    state: str = "new"                        # new | q:<номер вопроса> | done
    answers: dict[str, str] = field(default_factory=dict)
    result: dict[str, Any] | None = None
    created_at: float = 0.0
    updated_at: float = 0.0
    completed_at: float | None = None
    reminded_at: float | None = None
    reminder_count: int = 0
    reminders_off: bool = False

    @property
    def platform(self) -> str:
        return self.uid.split(":", 1)[0]

    @property
    def external_id(self) -> str:
        return self.uid.split(":", 1)[-1]

    @property
    def quiz_step(self) -> int | None:
        if not self.state.startswith("q:"):
            return None
        try:
            step = int(self.state[2:])
        except ValueError:
            return None
        return step if 0 <= step < len(C.QUESTIONS) else None
