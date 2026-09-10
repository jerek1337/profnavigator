import unittest

from core import content as C
from core.models import Incoming
from tests.helpers import Chat, FakeAI, make_engine


class EngineFlowTest(unittest.IsolatedAsyncioTestCase):
    async def pass_quiz(self, chat: Chat, answers=("Математика", "Анализировать данные", "Деньги", "9", "Поступление")):
        await chat.start()
        await chat.press("Начать")
        for answer in answers:
            await chat.press(answer)

    async def test_full_flow_with_buttons_without_ai(self):
        engine, storage, _ = make_engine()
        chat = Chat(engine)
        replies = await chat.start("qr_corridor")
        self.assertIn("Пройдём короткий тест", replies[0].text)
        await self.pass_quiz(chat)

        user = storage.get_user("max:1")
        self.assertEqual(user.state, "done")
        self.assertEqual(user.answers, {"subject": "math", "skills": "analysis", "interest": "money",
                                        "grade": "9", "goal": "university"})
        self.assertEqual(user.result["professions"], ["fin_analyst", "data_scientist"])
        self.assertEqual(user.result["profile"], 1)
        self.assertEqual(user.result["provider"], "template")

        texts = [r.text for r in chat.replies[-3:]]
        self.assertIn("анализирую", texts[0])
        self.assertIn("Финансовый аналитик", texts[1])
        self.assertIn("Профиль 1", texts[2])
        link = [b for b in chat.last.flat_buttons() if b.url][0]
        self.assertEqual(link.url, "https://fin-olimp.ru")

        start_events = [e for e in storage.events() if e["name"] == "start"]
        self.assertEqual(start_events[0]["data"], {"source": "qr_corridor"})

    async def test_text_answers_and_validation(self):
        engine, storage, _ = make_engine()
        chat = Chat(engine)
        await chat.start()
        await chat.press("Начать")
        await chat.text("История")                     # свой вариант предмета
        replies = await chat.text("танцевать")         # нет такого варианта
        self.assertIn("Не совсем понял", replies[0].text)
        self.assertEqual(replies[0].meta["progress"]["step"], 1)
        await chat.text("анализировать данные")
        await chat.text("космос")                      # свой интерес
        replies = await chat.text("пятый")
        self.assertIn("числом", replies[0].text)
        await chat.text("11 класс")
        await chat.text("пока не знаю")
        user = storage.get_user("max:1")
        self.assertEqual(user.answers["subject"], "other")
        self.assertEqual(user.answers["subject_text"], "История")
        self.assertEqual(user.answers["interest_text"], "космос")
        self.assertEqual(user.answers["grade"], "11")
        self.assertEqual(user.result["professions"], ["risk_manager", "esg_analyst"])

    async def test_back_and_stale_buttons(self):
        engine, storage, _ = make_engine()
        chat = Chat(engine)
        await chat.start()
        await chat.press("Начать")
        await chat.press("Информатика")
        await chat.press("Назад")
        self.assertEqual(storage.get_user("max:1").state, "q:0")
        replies = await chat.event("action", "ans:3:1")  # кнопка из другого шага
        self.assertIn("неактуальна", replies[0].text)
        self.assertEqual(storage.get_user("max:1").state, "q:0")

    async def test_resume_after_restart(self):
        engine, storage, clock = make_engine()
        chat = Chat(engine)
        await chat.start()
        await chat.press("Начать")
        await chat.press("Информатика")
        # «перезапуск» бота: новый движок поверх того же хранилища
        from core.engine import Engine
        engine2 = Engine(storage, FakeAI(), engine.links, clock=clock)
        chat2 = Chat(engine2)
        replies = await chat2.start()
        self.assertIn("вопросе 2 из 5", replies[0].text)
        replies = await chat2.press("Продолжить тест")
        self.assertIn("Что тебе ближе", replies[0].text)

    async def test_commands_before_quiz(self):
        engine, _, _ = make_engine()
        chat = Chat(engine)
        self.assertIn("Сначала пройди", (await chat.text("/result"))[0].text)
        self.assertIn("Сначала пройди", (await chat.text("/route"))[0].text)
        self.assertIn("Финатлон", (await chat.text("/finathlon"))[0].text)
        self.assertIn("Не знаю такой команды", (await chat.text("/abracadabra"))[0].text)
        self.assertIn("/forget", (await chat.text("/help"))[0].text)

    async def test_ai_advice_route_and_questions(self):
        ai = FakeAI("Совет от ИИ 🙂")
        engine, storage, _ = make_engine(ai=ai, public_url="https://bot.example.ru")
        chat = Chat(engine)
        await self.pass_quiz(chat, ("Информатика", "Создавать новое", "Технологии", "10", "Свой проект"))
        user = storage.get_user("max:1")
        self.assertEqual(user.result["advice"], "Совет от ИИ 🙂")
        prompt = ai.calls[0][1]
        for part in ("10 класса", "Информатика", "Создавать новое", "FinTech-разработчик", "5–7 предложений"):
            self.assertIn(part, prompt)
        # ссылки через сервер: учёт кликов и веб-версия того же профиля
        urls = [b.url for r in chat.replies for b in r.flat_buttons() if b.url]
        self.assertTrue(any(u.startswith("https://bot.example.ru/go/finatlon?u=max%3A1") for u in urls))
        self.assertTrue(any(u.startswith("https://bot.example.ru/?u=max%3A1&s=") for u in urls))

        replies = await chat.press("Мой маршрут")
        self.assertIn("Составляю", replies[0].text)
        self.assertIn("FinTech-разработчик", replies[1].text)
        calls = len(ai.calls)
        replies = await chat.text("/route")
        self.assertEqual(len(ai.calls), calls, "маршрут должен браться из кэша")

        replies = await chat.text("Какие предметы сдавать?")
        self.assertEqual(replies[-1].text, "Совет от ИИ 🙂")
        qa_events = [e for e in storage.events() if e["name"] == "qa"]
        self.assertNotIn("Какие предметы", str(qa_events))  # текст вопроса не сохраняется
        for _ in range(3):
            replies = await chat.text("ещё вопрос")
        self.assertIn("лимит", replies[-1].text)

    async def test_route_template_when_ai_off(self):
        engine, _, _ = make_engine()
        chat = Chat(engine)
        await self.pass_quiz(chat)
        replies = await chat.text("/route")
        self.assertIn("Месяц 1", replies[-1].text)
        self.assertIn("Месяц 3", replies[-1].text)

    async def test_reminders(self):
        engine, storage, clock = make_engine()
        chat = Chat(engine)
        await self.pass_quiz(chat)
        sent = []

        async def send(reply):
            sent.append(reply)

        week = 7 * 86400
        self.assertFalse(await engine.send_reminder("max:1", send, delay_seconds=week, max_count=2))
        clock.now += week + 1
        self.assertEqual(len(storage.due_reminders(clock.now - week, 2)), 1)
        self.assertTrue(await engine.send_reminder("max:1", send, delay_seconds=week, max_count=2))
        self.assertIn("Финансовый аналитик", sent[0].text)
        self.assertIn("Совет на сегодня", sent[0].text)
        self.assertFalse(await engine.send_reminder("max:1", send, delay_seconds=week, max_count=2))

        self.assertIn("remind_off", [b.action for b in sent[0].flat_buttons()])
        await chat.event("action", "remind_off")
        clock.now += week + 1
        self.assertFalse(await engine.send_reminder("max:1", send, delay_seconds=week, max_count=5))

    async def test_forget(self):
        engine, storage, _ = make_engine()
        chat = Chat(engine)
        await self.pass_quiz(chat)
        await chat.text("/forget")
        await chat.press("Да, удалить")
        self.assertIsNone(storage.get_user("max:1"))
        self.assertEqual([e for e in storage.events() if e["uid"] == "max:1"], [])

    async def test_error_is_contained(self):
        engine, storage, _ = make_engine()
        chat = Chat(engine)

        async def boom(*args):
            raise RuntimeError("сбой")

        engine.register_action("begin", boom)
        await chat.start()
        replies = await chat.press("Начать")
        self.assertIn("Что-то пошло не так", replies[0].text)
        self.assertTrue(any(e["name"] == "error" for e in storage.events()))
        replies = await chat.text("/help")  # бот продолжает работать
        self.assertIn("Что я умею", replies[0].text)

    async def test_last_answer_survives_broken_channel(self):
        """Обрыв связи на выдаче результата не должен откатывать тест к последнему вопросу."""
        engine, storage, _ = make_engine()
        chat = Chat(engine)
        await chat.start()
        await chat.press("Начать")
        for answer in ("Математика", "Анализировать данные", "Деньги", "9"):
            await chat.press(answer)

        async def dead_channel(reply):
            raise OSError("соединение потеряно")

        button = [b for b in chat.last.flat_buttons() if "Поступление" in b.text][0]
        await engine.handle(Incoming("max:1", "max", "action", button.action), dead_channel)

        user = storage.get_user("max:1")
        self.assertEqual(user.answers["goal"], "university")
        self.assertEqual(user.state, "done")
        self.assertIsNotNone(user.result)
        self.assertTrue(any(e["name"] == "completed" for e in storage.events()))

    async def test_questions_are_marked_transient(self):
        """Вопрос помечен transient — адаптер снимет с него кнопки."""
        engine, _, _ = make_engine()
        chat = Chat(engine)
        await chat.start()
        replies = await chat.press("Начать")
        self.assertTrue(replies[0].meta.get("transient"))
        self.assertFalse(chat.replies[0].meta.get("transient"))  # приветствие остаётся с кнопками

    async def test_analytics(self):
        from core.analytics import collect_stats
        engine, storage, clock = make_engine()
        for i in range(3):
            chat = Chat(engine, uid=f"web:u{i}", channel="web")
            await self.pass_quiz(chat)
        await Chat(engine, uid="web:x", channel="web").start()
        stats = collect_stats(storage, now=clock.now)
        self.assertEqual(stats["summary"]["users"], 4)
        self.assertEqual(stats["summary"]["completed"], 3)
        self.assertEqual(stats["summary"]["conversion"], 100.0)
        self.assertEqual(stats["funnel"][0]["users"], 3)
        self.assertEqual(dict(stats["professions"])["Финансовый аналитик"], 3)
        self.assertEqual(len(stats["daily"]), 14)


if __name__ == "__main__":
    unittest.main()
