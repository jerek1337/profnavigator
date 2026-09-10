"""Подбор профессий и профиля олимпиады."""
from __future__ import annotations

import re

from . import content as C
from .text import normalize


def match_professions(answers: dict[str, str], k: int = 2) -> list[str]:
    """Сначала таблица RULES, затем взвешенный подбор для остальных сочетаний."""
    subject, skills, interest = answers.get("subject"), answers.get("skills"), answers.get("interest")
    rule = C.RULES.get((subject or "", skills or ""))
    if rule:
        return list(rule)[:k]
    scored = []
    for order, (pid, prof) in enumerate(C.PROFESSIONS.items()):
        w = prof["weights"]
        score = (w["subject"].get(subject, 0) * 2      # предмет и навыки важнее интереса
                 + w["skills"].get(skills, 0) * 2
                 + w["interest"].get(interest, 0))
        scored.append((-score, order, pid))
    scored.sort()
    return [pid for _, _, pid in scored[:k]]


def pick_profile(prof_ids: list[str], answers: dict[str, str]) -> int:
    profiles = [C.PROFESSIONS[p]["profile"] for p in prof_ids if p in C.PROFESSIONS]
    if profiles and len(set(profiles)) == 1:
        return profiles[0]
    hint = C.PROFILE_BY_INTEREST.get(answers.get("interest", "")) or C.PROFILE_BY_SKILLS.get(answers.get("skills", ""))
    return hint or (profiles[0] if profiles else 1)


def match_option(question: C.Question, text: str) -> str | None:
    """Сопоставляет введённый текст с вариантом ответа («математика», «Анализировать данные 📊»)."""
    t = normalize(text)
    if not t:
        return None
    for code, label in question.options:
        if normalize(label) == t:
            return code
    if len(t) >= 4:
        hits = [code for code, label in question.options if normalize(label).startswith(t)]
        if len(hits) == 1:
            return hits[0]
    return None


def parse_grade(text: str) -> int | None:
    m = re.search(r"\d{1,2}", text)
    if not m:
        return None
    grade = int(m.group())
    return grade if C.GRADE_MIN <= grade <= C.GRADE_MAX else None


def option_label(key: str, code: str | None) -> str | None:
    idx = C.QUESTION_INDEX.get(key)
    if idx is None or code is None:
        return None
    for c, label in C.QUESTIONS[idx].options:
        if c == code:
            return label
    return None


def answer_label(key: str, answers: dict[str, str], default: str = "не указано") -> str:
    """Человекочитаемый ответ: свой вариант пользователя или подпись кнопки."""
    raw = answers.get(f"{key}_text")
    if raw:
        return raw
    return option_label(key, answers.get(key)) or default
