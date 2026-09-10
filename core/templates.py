"""Шаблонные ответы — работают, когда ИИ недоступен (MVP «без ИИ на старте»)."""
from __future__ import annotations

from . import content as C
from .matching import answer_label

def _cap(text: str) -> str:
    return text[:1].upper() + text[1:]

def advice(answers: dict[str, str], prof_ids: list[str]) -> str:
    profs = [C.PROFESSIONS[p] for p in prof_ids if p in C.PROFESSIONS]
    skills = answer_label("skills", answers).lower()
    interest = answer_label("interest", answers, "")
    if answers.get("subject") == "other" and not answers.get("subject_text"):
        opening = f"Тебе ближе всего — {skills}, и это отличная база для мира финансов и технологий 💪"
    else:
        subject = answer_label("subject", answers)
        opening = (f"Тебе легче всего даётся «{subject}», а ближе всего — {skills}. "
                   "Это отличная база для мира финансов и технологий 💪")
    parts = [opening]
    if len(profs) >= 2:
        parts.append(f"{profs[0]['emoji']} {profs[0]['title']} и {profs[1]['emoji']} {profs[1]['title']} — "
                     f"профессии, где каждый день нужно именно это: {skills}.")
    if interest:
        parts.append(f"А интерес к теме «{interest}» поможет не бросить на полпути ✨")
    if profs:
        parts.append(f"💡 С чего начать: {profs[0]['start']}.")
    goal_line = C.GOAL_LINES.get(answers.get("goal", ""))
    if goal_line:
        parts.append(goal_line)
    return "\n\n".join(parts)

def route(answers: dict[str, str], prof_ids: list[str], profile: int) -> str:
    prof = C.PROFESSIONS[prof_ids[0]]
    res = prof["resources"]
    return "\n".join([
        "📅 Месяц 1 — разобраться в основах",
        f"• 📘 {res[0]}",
        f"• 📗 {res[1]}",
        f"• 👣 Первый шаг: {prof['start']}",
        "",
        "📅 Месяц 2 — практика и олимпиада",
        f"• 🏆 Изучи на fin-olimp.ru правила и сроки «Финатлона» и выбери {C.FINATLON_PROFILES[profile]}",
        f"• ✏️ {_cap(C.FINATLON_PRACTICE[profile])}",
        f"• 🛠 Ещё: {res[2]}",
        "",
        "📅 Месяц 3 — свой школьный проект",
        f"• 💡 Идея: {prof['project']}",
        "• 📣 Покажи результат на классном часе или в школьном чате и собери отзывы",
    ])
