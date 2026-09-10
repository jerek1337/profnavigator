"""Промпты для ИИ. Все ответы опираются на данные пользователя и защищены от выдумок."""
from __future__ import annotations

from . import content as C
from .matching import answer_label

BASE_RULES = (
    "Пиши по-русски, обращайся на «ты», дружелюбно и без канцелярита. "
    "Не используй markdown: звёздочки, решётки и таблицы. "
    "Не выдумывай факты: не называй конкретные баллы, льготы, зарплаты, вузы, даты и ссылки, "
    "если их нет во входных данных; когда что-то нужно уточнить, советуй проверить на официальном сайте. "
    "Не обещай гарантий поступления и не проси личные данные."
)

ADVICE_SYSTEM = "Ты — ПрофНавигатор, дружелюбный ИИ-профориентолог для школьников в сфере финансов и технологий. " + BASE_RULES
ROUTE_SYSTEM = "Ты — ПрофНавигатор, наставник, который составляет понятные учебные планы для школьников. " + BASE_RULES
TIP_SYSTEM = "Ты — ПрофНавигатор, наставник школьника. Даёшь короткие практичные советы. " + BASE_RULES
QA_SYSTEM = (
    "Ты — ПрофНавигатор, ИИ-помощник по профориентации для школьников в сфере финансов и технологий. "
    "Отвечай коротко, до 6 предложений, с учётом профиля ученика. "
    "Отвечай только на вопросы о профессиях, учёбе, олимпиадах, финансовой грамотности и технологиях; "
    "на другие темы вежливо скажи, что с этим не поможешь, и предложи вернуться к профориентации. "
    + BASE_RULES
)

def _fields(answers: dict[str, str]) -> dict[str, str]:
    return {
        "grade": answer_label("grade", answers, "?"),
        "subject": answer_label("subject", answers),
        "skills": answer_label("skills", answers),
        "interest": answer_label("interest", answers),
        "goal": answer_label("goal", answers),
    }

def _titles(prof_ids: list[str]) -> list[str]:
    return [C.PROFESSIONS[p]["title"] for p in prof_ids if p in C.PROFESSIONS]

def advice_user(answers: dict[str, str], prof_ids: list[str]) -> str:
    f = _fields(answers)
    return (
        f"Ты профориентолог для школьника {f['grade']} класса. "
        f"Любимый предмет: «{f['subject']}». Интересы: «{f['interest']}», сильные стороны: «{f['skills']}», "
        f"цель: «{f['goal']}». По его ответам подобраны профессии: {' и '.join(_titles(prof_ids))}. "
        "Объясни, почему именно они подходят, опираясь на ответы, и дай 1 совет, с чего начать. "
        "Ответ дай дружелюбно, 5–7 предложений, с эмодзи."
    )

def route_user(answers: dict[str, str], prof_ids: list[str], profile: int) -> str:
    f = _fields(answers)
    main = C.PROFESSIONS[prof_ids[0]]
    resources = []
    for pid in prof_ids:
        resources.extend(C.PROFESSIONS[pid]["resources"])
    resources = list(dict.fromkeys(resources))
    return (
        f"Составь персональный маршрут развития на 3 месяца для школьника {f['grade']} класса, "
        f"который хочет стать: {main['title']}. Цель: «{f['goal']}», интересы: «{f['interest']}».\n"
        "Структура: «Месяц 1», «Месяц 2», «Месяц 3», в каждом 2–3 пункта с эмодзи.\n"
        "Обязательно включи:\n"
        f"1) что изучить — используй только ресурсы из списка: {'; '.join(resources)};\n"
        f"2) подготовку к олимпиаде «Финатлон», {C.FINATLON_PROFILES[profile]} (сайт fin-olimp.ru);\n"
        f"3) школьный проект — можно взять за основу идею: {main['project']}.\n"
        "Не больше 1000 символов, без таблиц и без других ссылок."
    )

def tip_user(answers: dict[str, str], prof: dict) -> str:
    f = _fields(answers)
    return (
        f"Дай один короткий практический совет на сегодня (1–2 предложения, с эмодзи) школьнику {f['grade']} класса, "
        f"который готовится стать: {prof['title']}. Совет должен занимать не больше 20 минут."
    )

def qa_user(answers: dict[str, str], result: dict, question: str) -> str:
    f = _fields(answers)
    profile = C.FINATLON_PROFILES.get(result.get("profile"), "не определён")
    return (
        f"Профиль ученика: {f['grade']} класс; предмет — «{f['subject']}»; сильные стороны — «{f['skills']}»; "
        f"интересы — «{f['interest']}»; цель — «{f['goal']}»; подобранные профессии — "
        f"{', '.join(_titles(result.get('professions', [])))}; олимпиада «Финатлон» — {profile}.\n\n"
        f"Вопрос ученика: «{question}»"
    )
