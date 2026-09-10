"""Движок диалога: принимает Incoming, отвечает Reply через send.

О мессенджерах не знает — их подключают адаптеры.
"""
from __future__ import annotations

import asyncio
import contextlib
import logging
import random
import time
import weakref
from typing import Any, Awaitable, Callable

from . import content as C
from . import prompts, templates
from .links import Links
from .matching import match_option, match_professions, parse_grade, pick_profile
from .models import Button, Incoming, Reply, User
from .storage import Storage
from .text import chunk

log = logging.getLogger(__name__)

Send = Callable[[Reply], Awaitable[None]]
Handler = Callable[[User, Incoming, str, Send], Awaitable[None]]

class Engine:
    def __init__(self, storage: Storage, ai: Any, links: Links, *, qa_daily_limit: int = 20,
                 clock: Callable[[], float] = time.time) -> None:
        self.storage = storage
        self.ai = ai
        self.links = links
        self.qa_daily_limit = qa_daily_limit
        self.clock = clock
        self._locks: "weakref.WeakValueDictionary[str, asyncio.Lock]" = weakref.WeakValueDictionary()

        self.commands: dict[str, Handler] = {
            "start": self.cmd_start,
            "result": self.act_result,
            "route": self.act_route,
            "finathlon": self.act_finatlon,
            "finatlon": self.act_finatlon,
            "help": self.act_menu,
            "menu": self.act_menu,
            "stop": self.act_remind_off,
            "forget": self.act_forget,
        }
        self.actions: dict[str, Handler] = {
            "hello": self.act_hello,
            "begin": self.act_begin,
            "resume": self.act_resume,
            "how": self.act_how,
            "ans": self.act_answer,
            "back": self.act_back,
            "result": self.act_result,
            "route": self.act_route,
            "finatlon": self.act_finatlon,
            "profiles": self.act_profiles,
            "menu": self.act_menu,
            "remind_off": self.act_remind_off,
            "remind_on": self.act_remind_on,
            "forget": self.act_forget,
            "forget_yes": self.act_forget_yes,
        }

    def register_command(self, name: str, handler: Handler) -> None:
        """Регистрирует команду без правки движка."""
        self.commands[name] = handler

    def register_action(self, name: str, handler: Handler) -> None:
        self.actions[name] = handler

    async def handle(self, inc: Incoming, send: Send) -> None:
        """Точка входа. Ошибка в одном сообщении не роняет бота."""
        async with self._lock(inc.uid):
            try:
                user = self.storage.get_or_create_user(inc.uid, chat_id=inc.chat_id, name=inc.name)
                await self._dispatch(user, inc, send)
            except Exception as exc:  # noqa: BLE001
                log.exception("Ошибка обработки %s «%s» от %s", inc.kind, (inc.value or "")[:40], inc.uid)
                self._event(inc.uid, inc.channel, "error", {"type": type(exc).__name__, "kind": inc.kind})
                with contextlib.suppress(Exception):
                    await send(Reply(C.ERROR_TEXT, [[Button("▶️ Продолжить", "resume"), Button("📋 Меню", "menu")]]))

    async def _dispatch(self, user: User, inc: Incoming, send: Send) -> None:
        if inc.kind == "start":
            return await self.cmd_start(user, inc, inc.value or "", send)
        if inc.kind == "action":
            name, _, arg = (inc.value or "").partition(":")
            handler = self.actions.get(name)
            if handler is None:
                return await self.act_menu(user, inc, "", send)
            return await handler(user, inc, arg, send)

        text = (inc.value or "").strip()[:C.MAX_INPUT_LEN]
        if not text:  # стикер, фото и т. п.
            return await self.act_resume(user, inc, "", send)
        if text.startswith("/"):
            parts = text[1:].split(maxsplit=1)
            command = parts[0].split("@")[0].lower() if parts else ""
            handler = self.commands.get(command)
            if handler is None:
                return await send(Reply(f"{C.UNKNOWN_COMMAND}\n\n{self._help_text()}", self._menu_buttons(user, inc)))
            return await handler(user, inc, parts[1] if len(parts) > 1 else "", send)
        return await self.on_text(user, inc, text, send)

    async def cmd_start(self, user: User, inc: Incoming, arg: str, send: Send) -> None:
        source = arg.strip()[:64] or None  # метка источника: QR в коридоре, классный час…
        self._event(user.uid, inc.channel, "start", {"source": source} if source else None)
        await self._welcome(user, inc, send)

    async def act_hello(self, user: User, inc: Incoming, arg: str, send: Send) -> None:
        await self._welcome(user, inc, send)

    async def _welcome(self, user: User, inc: Incoming, send: Send) -> None:
        text = C.WELCOME.format(name=self._name(user))
        step = user.quiz_step
        if step is not None:
            text += "\n\n" + C.QUIZ_UNFINISHED.format(step=step + 1, total=len(C.QUESTIONS))
            rows = [[Button("▶️ Продолжить тест", "resume")], [Button("🔁 Начать заново", "begin")]]
        else:
            rows = [[Button("🚀 Начать", "begin"), Button("📖 Как это работает", "how")]]
            if user.result:
                rows.append([Button("📋 Мой результат", "result")])
        self._append_web(rows, user, inc)
        await send(Reply(text, rows))

    async def act_how(self, user: User, inc: Incoming, arg: str, send: Send) -> None:
        await send(Reply(C.HOW_IT_WORKS, [[Button("🚀 Начать", "begin")]]))

    async def act_begin(self, user: User, inc: Incoming, arg: str, send: Send) -> None:
        user.answers = {}
        user.state = "q:0"
        self.storage.save_user(user)
        self._event(user.uid, inc.channel, "begin")
        await send(self._question(0))

    async def act_resume(self, user: User, inc: Incoming, arg: str, send: Send) -> None:
        step = user.quiz_step
        if step is not None:
            return await send(self._question(step))
        await self._welcome(user, inc, send)

    async def act_answer(self, user: User, inc: Incoming, arg: str, send: Send) -> None:
        step = user.quiz_step
        try:
            q_step, idx = (int(x) for x in arg.split(":", 1))
        except ValueError:
            q_step, idx = -1, -1
        if step is None or q_step != step or not 0 <= idx < len(C.QUESTIONS[step].options):
            self._event(user.uid, inc.channel, "stale_button")
            if step is None:
                return await send(Reply(C.STALE_BUTTON, self._menu_buttons(user, inc)))
            return await send(self._question(step, note=C.STALE_BUTTON))
        code = C.QUESTIONS[step].options[idx][0]
        await self._record(user, inc, step, code, None, send)

    async def act_back(self, user: User, inc: Incoming, arg: str, send: Send) -> None:
        step = user.quiz_step
        if step is None:
            return await self.act_resume(user, inc, "", send)
        if step > 0:
            step -= 1
            user.state = f"q:{step}"
            self.storage.save_user(user)
        await send(self._question(step))

    async def on_text(self, user: User, inc: Incoming, text: str, send: Send) -> None:
        step = user.quiz_step
        if step is not None:
            q = C.QUESTIONS[step]
            code, raw = match_option(q, text), None
            if code is None and q.key == "grade":
                grade = parse_grade(text)
                if grade is None:
                    return await send(self._question(step, note=C.GRADE_HINT))
                code = str(grade)
            elif code is None and q.free_text_code:
                code, raw = q.free_text_code, text[:C.FREE_TEXT_LEN]
            elif code is None:
                return await send(self._question(step, note=C.CHOOSE_BUTTON))
            return await self._record(user, inc, step, code, raw, send)
        if user.result:
            return await self._qa(user, inc, text, send)
        await self._welcome(user, inc, send)

    async def _record(self, user: User, inc: Incoming, step: int, code: str, raw: str | None, send: Send) -> None:
        q = C.QUESTIONS[step]
        user.answers[q.key] = code
        if raw:
            user.answers[f"{q.key}_text"] = raw
        else:
            user.answers.pop(f"{q.key}_text", None)
        self._event(user.uid, inc.channel, "answer", {"step": step, "key": q.key, "value": code})
        if step + 1 < len(C.QUESTIONS):
            user.state = f"q:{step + 1}"
            self.storage.save_user(user)
            return await send(self._question(step + 1))
        self.storage.save_user(user)  # до _finish: сбой ИИ или отправки не должен терять ответ
        await self._finish(user, inc, send)

    def _question(self, step: int, note: str | None = None) -> Reply:
        q = C.QUESTIONS[step]
        buttons = [Button(label, f"ans:{step}:{i}") for i, (_, label) in enumerate(q.options)]
        rows = chunk(buttons, q.columns)
        if step > 0:
            rows.append([Button("⬅️ Назад", "back")])
        parts = ([note] if note else []) + [q.text] + ([C.FREE_TEXT_HINT] if q.free_text_code else [])
        return Reply("\n\n".join(parts), rows,
                     meta={"progress": {"step": step, "total": len(C.QUESTIONS)}, "transient": True})

    async def _finish(self, user: User, inc: Incoming, send: Send) -> None:
        total = len(C.QUESTIONS)
        try:
            await send(Reply(C.ANALYZING, meta={"progress": {"step": total, "total": total}}))
        except Exception as exc:  # noqa: BLE001 — индикатор ожидания не стоит результата
            log.warning("Не удалось отправить индикатор ожидания %s: %s", user.uid, exc)

        prof_ids = match_professions(user.answers)
        profile = pick_profile(prof_ids, user.answers)
        advice, provider = await self.ai.generate(
            prompts.ADVICE_SYSTEM, prompts.advice_user(user.answers, prof_ids), max_tokens=700)
        if not advice:
            advice, provider = templates.advice(user.answers, prof_ids), "template"

        now = self.clock()
        user.result = {"professions": prof_ids, "profile": profile, "advice": advice,
                       "provider": provider, "route": None, "created_at": now}
        user.state = "done"
        user.completed_at = now
        user.reminded_at = None
        user.reminder_count = 0
        self.storage.save_user(user)
        self._event(user.uid, inc.channel, "completed",
                    {"professions": prof_ids, "profile": profile, "provider": provider})

        await send(self._result_reply(user, inc, with_finatlon=False))
        await send(self._finatlon_reply(user))

    def _result_reply(self, user: User, inc: Incoming, *, with_finatlon: bool = True) -> Reply:
        res = user.result or {}
        lines = [C.RESULT_HEADER, "", C.RESULT_SUBHEADER]
        for pid in res.get("professions", []):
            prof = C.PROFESSIONS.get(pid)
            if prof:
                lines.append(f"{prof['emoji']} {prof['title']} — {prof['about']}")
        if res.get("advice"):
            lines += ["", res["advice"]]
        rows = [[Button("🗺 Маршрут на 3 месяца", "route")]]
        if with_finatlon:
            rows.append([Button("🏆 Олимпиада «Финатлон»", "finatlon")])
        rows.append([Button("🔁 Пройти заново", "begin")])
        self._append_web(rows, user, inc)
        return Reply("\n".join(lines), rows)

    def _finatlon_reply(self, user: User) -> Reply:
        text = C.FINATLON_TEXT
        profile = (user.result or {}).get("profile")
        if profile in C.FINATLON_PROFILES:
            text += "\n\n" + C.FINATLON_YOUR_PROFILE.format(profile=C.FINATLON_PROFILES[profile])
        text += "\n\n" + C.FINATLON_ADMISSION_NOTE
        rows = [
            [Button("📝 Зарегистрироваться на fin-olimp.ru", url=self.links.finatlon(user.uid))],
            [Button("🧠 Узнать больше о профилях", "profiles")],
            [Button("🗺 Мой маршрут развития", "route")] if user.result else [Button("🚀 Пройти тест", "begin")],
        ]
        return Reply(text, rows)

    async def act_result(self, user: User, inc: Incoming, arg: str, send: Send) -> None:
        if not user.result:
            return await send(Reply(C.NOT_READY, [[Button("🚀 Начать тест", "begin")]]))
        self._event(user.uid, inc.channel, "result_view")
        await send(self._result_reply(user, inc))

    async def act_finatlon(self, user: User, inc: Incoming, arg: str, send: Send) -> None:
        self._event(user.uid, inc.channel, "finatlon_view")
        await send(self._finatlon_reply(user))

    async def act_profiles(self, user: User, inc: Incoming, arg: str, send: Send) -> None:
        self._event(user.uid, inc.channel, "profiles_view")
        rows = [[Button("📝 Зарегистрироваться на fin-olimp.ru", url=self.links.finatlon(user.uid))]]
        rows.append([Button("🗺 Мой маршрут развития", "route")] if user.result else [Button("🚀 Пройти тест", "begin")])
        await send(Reply(C.PROFILES_TEXT, rows))

    async def act_route(self, user: User, inc: Incoming, arg: str, send: Send) -> None:
        res = user.result
        if not res:
            return await send(Reply(C.NOT_READY, [[Button("🚀 Начать тест", "begin")]]))
        prof_ids = [p for p in res.get("professions", []) if p in C.PROFESSIONS] or match_professions(user.answers)
        profile = res.get("profile") if res.get("profile") in C.FINATLON_PROFILES else pick_profile(prof_ids, user.answers)

        route = res.get("route")
        stale_template = res.get("route_provider") == "template" and self.ai.enabled
        if not route or stale_template:
            await send(Reply(C.ROUTE_WAIT))
            route, provider = await self.ai.generate(
                prompts.ROUTE_SYSTEM, prompts.route_user(user.answers, prof_ids, profile), max_tokens=1000)
            if not route:
                route = res.get("route") or templates.route(user.answers, prof_ids, profile)
                provider = "template"
            res["route"], res["route_provider"] = route, provider
            self.storage.save_user(user)

        self._event(user.uid, inc.channel, "route_view", {"provider": res.get("route_provider")})
        title = C.PROFESSIONS[prof_ids[0]]["title"]
        rows = [[Button("🏆 Финатлон", "finatlon"), Button("📋 Мой результат", "result")],
                [Button("🔁 Пройти заново", "begin")]]
        await send(Reply(f"{C.ROUTE_HEADER.format(title=title)}\n\n{route}", rows))

    async def _qa(self, user: User, inc: Incoming, text: str, send: Send) -> None:
        if not self.ai.enabled:
            return await send(Reply(C.QA_UNAVAILABLE, self._menu_buttons(user, inc)))
        if self.storage.count_events(user.uid, "qa", self.clock() - 86400) >= self.qa_daily_limit:
            return await send(Reply(C.QA_LIMIT, self._menu_buttons(user, inc)))
        self._event(user.uid, inc.channel, "qa", {"length": len(text)})  # сам текст вопроса не храним
        await send(Reply(C.THINKING))
        answer, _ = await self.ai.generate(
            prompts.QA_SYSTEM, prompts.qa_user(user.answers, user.result or {}, text), max_tokens=500)
        await send(Reply(answer or C.QA_FAILED, self._menu_buttons(user, inc)))

    async def act_menu(self, user: User, inc: Incoming, arg: str, send: Send) -> None:
        await send(Reply(self._help_text(), self._menu_buttons(user, inc)))

    async def act_remind_off(self, user: User, inc: Incoming, arg: str, send: Send) -> None:
        user.reminders_off = True
        self.storage.save_user(user)
        self._event(user.uid, inc.channel, "reminders_off")
        await send(Reply(C.REMINDERS_OFF, [[Button("🔔 Включить напоминания", "remind_on")]]))

    async def act_remind_on(self, user: User, inc: Incoming, arg: str, send: Send) -> None:
        user.reminders_off = False
        self.storage.save_user(user)
        self._event(user.uid, inc.channel, "reminders_on")
        await send(Reply(C.REMINDERS_ON, self._menu_buttons(user, inc)))

    async def act_forget(self, user: User, inc: Incoming, arg: str, send: Send) -> None:
        await send(Reply(C.FORGET_CONFIRM, [[Button("🗑 Да, удалить", "forget_yes"), Button("Отмена", "menu")]]))

    async def act_forget_yes(self, user: User, inc: Incoming, arg: str, send: Send) -> None:
        self.storage.delete_user(user.uid)
        await send(Reply(C.FORGOTTEN, [[Button("🚀 Начать", "begin")]]))

    def reminder_due(self, user: User, delay_seconds: float, max_count: int) -> bool:
        if not user.result or user.reminders_off or user.reminder_count >= max_count:
            return False
        last_touch = user.reminded_at or user.completed_at
        return last_touch is not None and last_touch <= self.clock() - delay_seconds

    async def send_reminder(self, uid: str, send: Send, *, delay_seconds: float, max_count: int) -> bool:
        async with self._lock(uid):
            user = self.storage.get_user(uid)
            if user is None or not self.reminder_due(user, delay_seconds, max_count):
                return False
            prof_ids = [p for p in user.result.get("professions", []) if p in C.PROFESSIONS]
            if not prof_ids:
                return False
            prof = C.PROFESSIONS[prof_ids[0]]
            tip, _ = await self.ai.generate(prompts.TIP_SYSTEM, prompts.tip_user(user.answers, prof), max_tokens=200)
            reply = Reply(
                C.REMINDER.format(name=self._name(user), profession=prof["title"], tip=tip or random.choice(prof["tips"])),
                [[Button("🗺 Мой маршрут", "route"), Button("🏆 Финатлон", "finatlon")],
                 [Button("🔕 Не напоминать", "remind_off")]],
            )
            # отмечаем до отправки: заблокировавший бота не должен получать бесконечные попытки
            user.reminded_at = self.clock()
            user.reminder_count += 1
            self.storage.save_user(user)
            try:
                await send(reply)
            except Exception as exc:  # noqa: BLE001
                log.warning("Не удалось отправить напоминание %s: %s", uid, exc)
                self._event(uid, user.platform, "reminder_failed", {"type": type(exc).__name__})
                return False
            self._event(uid, user.platform, "reminder_sent", {"count": user.reminder_count})
            return True

    def _lock(self, uid: str) -> asyncio.Lock:
        lock = self._locks.get(uid)
        if lock is None:
            lock = asyncio.Lock()
            self._locks[uid] = lock
        return lock

    def _event(self, uid: str, channel: str, name: str, data: dict[str, Any] | None = None) -> None:
        try:
            self.storage.log_event(uid, channel, name, data)
        except Exception:  # noqa: BLE001 — аналитика не должна ломать диалог
            log.exception("Не удалось записать событие %s", name)

    @staticmethod
    def _name(user: User) -> str:
        return f", {user.name}" if user.name else ""

    def _help_text(self) -> str:
        return f"{C.HELP_TEXT}\n\n{C.HELP_QA}" if self.ai.enabled else C.HELP_TEXT

    def _append_web(self, rows: list[list[Button]], user: User, inc: Incoming) -> None:
        if inc.channel == "web":
            return
        url = self.links.web(user.uid)
        if url:
            rows.append([Button("🌐 Открыть веб-версию", url=url)])

    def _menu_buttons(self, user: User, inc: Incoming) -> list[list[Button]]:
        if user.result:
            rows = [[Button("📋 Мой результат", "result"), Button("🗺 Маршрут", "route")],
                    [Button("🏆 Финатлон", "finatlon"), Button("🔁 Пройти заново", "begin")]]
        else:
            rows = [[Button("🚀 Начать тест", "begin")], [Button("🏆 Олимпиада «Финатлон»", "finatlon")]]
        self._append_web(rows, user, inc)
        return rows
