"""Утилиты для работы с текстом."""
from __future__ import annotations

import re
from typing import Any, Sequence, TypeVar

T = TypeVar("T")

def chunk(items: Sequence[T], size: int) -> list[list[T]]:
    size = max(1, size)
    return [list(items[i:i + size]) for i in range(0, len(items), size)]

def progress_bar(done: int, total: int) -> str:
    done = max(0, min(done, total))
    return "▰" * done + "▱" * (total - done)

def render_text(reply: Any) -> str:
    """Текст для мессенджеров: к вопросам теста добавляется строка прогресса."""
    progress = (reply.meta or {}).get("progress")
    if progress and progress.get("step", 0) < progress.get("total", 0):
        step, total = progress["step"], progress["total"]
        return f"Вопрос {step + 1} из {total}  {progress_bar(step + 1, total)}\n\n{reply.text}"
    return reply.text

def split_text(text: str, limit: int = 3800) -> list[str]:
    """Делит длинный текст на части по границам строк (лимит сообщений в мессенджерах)."""
    if len(text) <= limit:
        return [text]
    parts: list[str] = []
    current = ""
    for line in text.split("\n"):
        while len(line) > limit:
            if current:
                parts.append(current)
                current = ""
            parts.append(line[:limit])
            line = line[limit:]
        candidate = f"{current}\n{line}" if current else line
        if len(candidate) > limit:
            parts.append(current)
            current = line
        else:
            current = candidate
    if current:
        parts.append(current)
    return parts

def sanitize_ai(text: str, limit: int = 3500) -> str:
    """Убирает markdown-разметку из ответа ИИ, чтобы он одинаково выглядел везде."""
    t = text.replace("\r\n", "\n")
    t = re.sub(r"\*\*(.+?)\*\*", r"\1", t, flags=re.S)
    t = re.sub(r"__(.+?)__", r"\1", t, flags=re.S)
    t = re.sub(r"^[ \t]*#{1,6}[ \t]*", "", t, flags=re.M)
    t = re.sub(r"^[ \t]*[*\-][ \t]+", "• ", t, flags=re.M)
    t = t.replace("`", "")
    t = re.sub(r"\n{3,}", "\n\n", t).strip()
    if len(t) > limit:
        t = t[:limit].rsplit(" ", 1)[0].rstrip(",.;:") + "…"
    return t

def normalize(text: str) -> str:
    """Для сравнения ответов: без регистра, эмодзи, пробелов и знаков."""
    return re.sub(r"[\W_]+", "", text.casefold().replace("ё", "е"))
