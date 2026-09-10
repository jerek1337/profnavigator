import asyncio
import unittest
from unittest import mock

from adapters.max_bot import MaxAdapter, MaxApiError
from adapters.telegram_bot import TelegramAdapter, TelegramError
from core.http import HttpResult
from core.models import Button, Reply
from core.scheduler import ReminderScheduler
from tests.helpers import make_engine


class FakeMax(MaxAdapter):
    def __init__(self, engine, storage, reject_keyboard=False):
        super().__init__(engine, storage, "token", bot_link="https://max.ru/test_bot")
        self.calls = []
        self.sent = 0
        self.reject_keyboard = reject_keyboard

    async def api(self, method, path, *, params=None, payload=None, timeout=15.0, attempts=4):
        self.calls.append((method, path, params, payload))
        if self.reject_keyboard and path == "/messages" and payload and "attachments" in payload:
            raise MaxApiError(400, "bad keyboard")
        if method == "POST" and path == "/messages":
            self.sent += 1
            return {"message": {"body": {"mid": f"mid-{self.sent}"}}}
        return {}

    def messages(self):
        return [c for c in self.calls if c[0] == "POST" and c[1] == "/messages"]


class MaxAdapterTest(unittest.IsolatedAsyncioTestCase):
    async def test_bot_started_and_callback(self):
        engine, storage, _ = make_engine()
        bot = FakeMax(engine, storage)
        await bot.process({"update_type": "bot_started", "chat_id": 555, "payload": "qr",
                           "user": {"user_id": 42, "first_name": "Маша"}})
        method, path, params, payload = bot.messages()[0]
        self.assertEqual(params, {"chat_id": "555"})
        self.assertTrue(payload["text"].startswith("Привет, Маша!"))
        keyboard = payload["attachments"][0]
        self.assertEqual(keyboard["type"], "inline_keyboard")
        self.assertEqual(keyboard["payload"]["buttons"][0][0], {"type": "callback", "text": "🚀 Начать", "payload": "begin"})

        await bot.process({"update_type": "message_callback",
                           "callback": {"callback_id": "cb1", "payload": "begin", "user": {"user_id": 42}},
                           "message": {"recipient": {"chat_id": 555, "chat_type": "dialog"}}})
        self.assertIn("Вопрос 1 из 5", bot.messages()[-1][3]["text"])
        self.assertEqual(storage.get_user("max:42").state, "q:0")

        await bot.process({"update_type": "message_created",
                           "message": {"sender": {"user_id": 42}, "recipient": {"chat_id": 555, "chat_type": "dialog"},
                                       "body": {"text": "математика"}}})
        self.assertEqual(storage.get_user("max:42").answers["subject"], "math")

    async def test_group_chat_only_hint(self):
        engine, storage, _ = make_engine()
        bot = FakeMax(engine, storage)
        await bot.process({"update_type": "message_created",
                           "message": {"sender": {"user_id": 7}, "recipient": {"chat_id": -1, "chat_type": "chat"},
                                       "body": {"text": "всем привет"}}})
        self.assertEqual(bot.messages(), [])
        await bot.process({"update_type": "message_created",
                           "message": {"sender": {"user_id": 7}, "recipient": {"chat_id": -1, "chat_type": "chat"},
                                       "body": {"text": "/start"}}})
        payload = bot.messages()[0][3]
        self.assertIn("личных сообщениях", payload["text"])
        self.assertIsNone(storage.get_user("max:7"))

    async def test_keyboard_rejected_falls_back_to_text_with_links(self):
        engine, storage, _ = make_engine()
        bot = FakeMax(engine, storage, reject_keyboard=True)
        await bot.send(1, None, Reply("Текст", [[Button("Сайт", url="https://fin-olimp.ru")]]))
        last = bot.messages()[-1][3]
        self.assertNotIn("attachments", last)
        self.assertIn("https://fin-olimp.ru", last["text"])

    async def test_scheduler_sends_through_adapter(self):
        engine, storage, clock = make_engine()
        bot = FakeMax(engine, storage)
        user = storage.get_or_create_user("max:9", chat_id="99")
        user.result = {"professions": ["economist", "fin_lawyer"], "profile": 1}
        user.state, user.completed_at = "done", clock.now - 8 * 86400
        storage.save_user(user)
        scheduler = ReminderScheduler(engine, storage, {"max": bot}, delay_seconds=7 * 86400, max_count=1, interval=60)
        self.assertEqual(await scheduler.tick(), 1)
        self.assertIn("Экономист", bot.messages()[-1][3]["text"])
        self.assertEqual(await scheduler.tick(), 0)


class RetryPolicyTest(unittest.IsolatedAsyncioTestCase):
    """Обрыв связи не должен превращаться в дубль сообщения в чате."""

    def setUp(self):
        patcher = mock.patch("adapters.telegram_bot.asyncio.sleep", new=mock.AsyncMock())
        patcher.start()
        self.addCleanup(patcher.stop)

    async def test_send_message_is_not_retried_after_network_error(self):
        engine, storage, _ = make_engine()
        bot = TelegramAdapter(engine, storage, "t")
        calls = []

        async def request(*args, **kwargs):
            calls.append(args)
            raise OSError("таймаут ответа")

        with mock.patch("adapters.telegram_bot.request", request):
            with self.assertRaises(TelegramError):
                await bot.api("sendMessage", {"chat_id": 1, "text": "привет"})
            self.assertEqual(len(calls), 1)

            calls.clear()
            with self.assertRaises(TelegramError):
                await bot.api("getUpdates", {"offset": 0})
            self.assertEqual(len(calls), 4)

    async def test_send_message_is_retried_on_flood_control(self):
        engine, storage, _ = make_engine()
        bot = TelegramAdapter(engine, storage, "t")
        statuses = [429, 200]

        async def request(*args, **kwargs):
            status = statuses.pop(0)
            ok = status == 200
            return HttpResult(status, {"ok": ok, "result": {"message_id": 7},
                                       "parameters": {"retry_after": 0}}, "")

        with mock.patch("adapters.telegram_bot.request", request):
            result = await bot.api("sendMessage", {"chat_id": 1, "text": "привет"})
        self.assertEqual(result, {"message_id": 7})
        self.assertEqual(statuses, [])

    async def test_max_post_is_not_retried_after_network_error(self):
        engine, storage, _ = make_engine()
        bot = MaxAdapter(engine, storage, "token")
        calls = []

        async def request(*args, **kwargs):
            calls.append(args)
            raise OSError("таймаут ответа")

        with mock.patch("adapters.max_bot.request", request),              mock.patch("adapters.max_bot.asyncio.sleep", new=mock.AsyncMock()):
            with self.assertRaises(MaxApiError):
                await bot.api("POST", "/messages", payload={"text": "привет"})
            self.assertEqual(len(calls), 1)
            calls.clear()
            with self.assertRaises(MaxApiError):
                await bot.api("GET", "/updates")
            self.assertEqual(len(calls), 4)


class StaleKeyboardTest(unittest.IsolatedAsyncioTestCase):
    """Кнопки предыдущего вопроса снимаются, чтобы по ним нельзя было нажать второй раз."""

    @staticmethod
    async def drain(bot):
        """Дождаться фоновых задач, чтобы они не пережили цикл событий теста."""
        while bot._tasks:
            await asyncio.gather(*list(bot._tasks), return_exceptions=True)

    async def test_telegram_revokes_previous_question_keyboard(self):
        engine, storage, _ = make_engine()
        bot = TelegramAdapter(engine, storage, "t")
        calls = []
        message_id = iter(range(100, 200))

        async def api(method, payload=None, **kwargs):
            calls.append((method, payload))
            return {"message_id": next(message_id)} if method == "sendMessage" else {}

        bot.api = api
        update = {"update_id": 1, "message": {"from": {"id": 5, "first_name": "Петя"},
                                              "chat": {"id": 5, "type": "private"}, "text": "/start"}}
        await bot.process(update)
        await bot.process({"update_id": 2, "callback_query": {"id": "c1", "data": "begin", "from": {"id": 5},
                                                              "message": {"chat": {"id": 5}}}})
        first_question_id = 101   # второе сообщение — «Вопрос 1 из 5»
        await bot.process({"update_id": 3, "callback_query": {"id": "c2", "data": "ans:0:0", "from": {"id": 5},
                                                              "message": {"chat": {"id": 5}}}})
        await self.drain(bot)
        edits = [payload for method, payload in calls if method == "editMessageReplyMarkup"]
        self.assertEqual(edits, [{"chat_id": 5, "message_id": first_question_id}])
        self.assertEqual(storage.get_user("tg:5").state, "q:1")

    async def test_max_revokes_previous_question_keyboard(self):
        engine, storage, _ = make_engine()
        bot = FakeMax(engine, storage)
        await bot.process({"update_type": "bot_started", "chat_id": 555, "user": {"user_id": 42}})
        await bot.process({"update_type": "message_callback",
                           "callback": {"payload": "begin", "user": {"user_id": 42}},
                           "message": {"recipient": {"chat_id": 555, "chat_type": "dialog"}}})
        await bot.process({"update_type": "message_callback",
                           "callback": {"payload": "ans:0:0", "user": {"user_id": 42}},
                           "message": {"recipient": {"chat_id": 555, "chat_type": "dialog"}}})
        await self.drain(bot)
        edits = [c for c in bot.calls if c[0] == "PUT"]
        self.assertEqual(len(edits), 1)
        self.assertEqual(edits[0][2], {"message_id": "mid-2"})
        self.assertEqual(edits[0][3]["attachments"], [])
        self.assertIn("Вопрос 1 из 5", edits[0][3]["text"])


class DuplicateUpdateTest(unittest.IsolatedAsyncioTestCase):
    """Повторно доставленное событие не должно вызывать второй ответ."""

    async def test_max_skips_repeated_updates(self):
        engine, storage, _ = make_engine()
        bot = FakeMax(engine, storage)
        update = {"update_type": "message_callback",
                  "callback": {"callback_id": "cb-1", "payload": "begin", "user": {"user_id": 42}},
                  "message": {"recipient": {"chat_id": 555, "chat_type": "dialog"}}}
        self.assertFalse(bot._is_duplicate(bot._update_key(update)))
        self.assertTrue(bot._is_duplicate(bot._update_key(update)))

        created = {"update_type": "message_created",
                   "message": {"body": {"mid": "m-1", "text": "привет"}}}
        self.assertFalse(bot._is_duplicate(bot._update_key(created)))
        self.assertTrue(bot._is_duplicate(bot._update_key(created)))
        # событие без идентификатора не блокируем
        self.assertFalse(bot._is_duplicate(bot._update_key({"update_type": "message_created", "message": {}})))

    async def test_seen_cache_is_bounded(self):
        engine, storage, _ = make_engine()
        bot = FakeMax(engine, storage)
        bot.SEEN_LIMIT = 10
        for i in range(50):
            bot._is_duplicate(f"k{i}")
        self.assertLessEqual(len(bot._seen), 10)
        self.assertTrue(bot._is_duplicate("k49"))
        self.assertFalse(bot._is_duplicate("k0"))   # вытеснен


class TelegramAdapterTest(unittest.IsolatedAsyncioTestCase):
    async def test_message_flow(self):
        engine, storage, _ = make_engine()
        bot = TelegramAdapter(engine, storage, "t")
        calls = []

        async def api(method, payload=None, **kwargs):
            calls.append((method, payload))
            return {}

        bot.api = api
        await bot.process({"update_id": 1, "message": {"from": {"id": 5, "first_name": "Петя"},
                                                       "chat": {"id": 5, "type": "private"}, "text": "/start"}})
        method, payload = calls[0]
        self.assertEqual(method, "sendMessage")
        self.assertEqual(payload["reply_markup"]["inline_keyboard"][0][0]["callback_data"], "begin")
        self.assertIsNotNone(storage.get_user("tg:5"))


if __name__ == "__main__":
    unittest.main()
