"""Аналитика для дашборда: воронка, популярные профессии, источники переходов."""
from __future__ import annotations

import time
from collections import Counter, defaultdict
from typing import Any

from . import content as C
from .matching import option_label
from .storage import Storage

def _top(counter: Counter, limit: int = 10) -> list[list[Any]]:
    return [[label, count] for label, count in counter.most_common(limit)]

def collect_stats(storage: Storage, now: float | None = None) -> dict[str, Any]:
    now = now or time.time()
    users = storage.all_users()
    events = storage.events()

    uids_by_event: dict[str, set[str]] = defaultdict(set)
    answered: dict[int, set[str]] = defaultdict(set)
    sources: Counter = Counter()
    channels: Counter = Counter()
    errors_24h = 0
    qa_total = 0
    for e in events:
        uids_by_event[e["name"]].add(e["uid"])
        channels[e["channel"]] += 1
        if e["name"] == "answer" and isinstance(e["data"].get("step"), int):
            answered[e["data"]["step"]].add(e["uid"])
        elif e["name"] == "start":
            sources[e["data"].get("source") or "прямой заход"] += 1
        elif e["name"] == "error" and e["ts"] >= now - 86400:
            errors_24h += 1
        elif e["name"] == "qa":
            qa_total += 1

    completed = [u for u in users if u.result]
    professions: Counter = Counter()
    profiles: Counter = Counter()
    providers: Counter = Counter()
    for u in completed:
        for pid in u.result.get("professions", []):
            if pid in C.PROFESSIONS:
                professions[C.PROFESSIONS[pid]["title"]] += 1
        if u.result.get("profile") in C.FINATLON_PROFILES:
            profiles[C.FINATLON_PROFILES[u.result["profile"]]] += 1
        provider = u.result.get("provider") or "template"
        providers["Шаблон" if provider == "template" else f"ИИ: {provider}"] += 1

    def answers_counter(key: str) -> Counter:
        # свой текстовый вариант не показываем — только категории
        return Counter(option_label(key, u.answers.get(key)) or "—" for u in completed)

    started = len(uids_by_event["begin"])
    daily: Counter = Counter()
    for u in completed:
        if u.completed_at and u.completed_at >= now - 14 * 86400:
            daily[time.strftime("%d.%m", time.localtime(u.completed_at))] += 1
    days = [time.strftime("%d.%m", time.localtime(now - i * 86400)) for i in range(13, -1, -1)]

    return {
        "generated_at": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(now)),
        "summary": {
            "users": len(users),
            "started": started,
            "completed": len(completed),
            "conversion": round(100 * len(completed) / started, 1) if started else 0.0,
            "finatlon_clicks": len(uids_by_event["finatlon_click"]),
            "route_views": len(uids_by_event["route_view"]),
            "qa_questions": qa_total,
            "reminders_sent": len(uids_by_event["reminder_sent"]),
            "errors_24h": errors_24h,
        },
        "funnel": [{"label": f"Вопрос {i + 1}", "users": len(answered[i])} for i in range(len(C.QUESTIONS))],
        "professions": _top(professions),
        "subjects": _top(answers_counter("subject")),
        "grades": sorted(_top(answers_counter("grade"), 20), key=lambda x: (len(str(x[0])), str(x[0]))),
        "goals": _top(answers_counter("goal")),
        "profiles": _top(profiles),
        "sources": _top(sources),
        "channels": _top(channels),
        "providers": _top(providers),
        "daily": [[d, daily.get(d, 0)] for d in days],
    }
