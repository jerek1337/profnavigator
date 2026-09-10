import asyncio
import json
import unittest
import urllib.error
import urllib.request

from adapters.web import WebServer
from config import Settings
from core.http import run_in_thread
from tests.helpers import make_engine


def http(method, url, body=None):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method, headers={"Content-Type": "application/json"})

    class NoRedirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, *args, **kwargs):
            return None

    opener = urllib.request.build_opener(NoRedirect)
    try:
        with opener.open(req, timeout=10) as resp:
            return resp.status, dict(resp.headers), resp.read().decode()
    except urllib.error.HTTPError as exc:
        return exc.code, dict(exc.headers), exc.read().decode()


class WebServerTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.engine, self.storage, _ = make_engine()
        settings = Settings(web_host="127.0.0.1", web_port=0, admin_token="secret", ai_total_timeout=5)
        self.web = WebServer(self.engine, self.storage, self.engine.links, settings)
        self.task = asyncio.create_task(self.web.run())
        await asyncio.wait_for(self.web.ready.wait(), 5)
        self.base = f"http://127.0.0.1:{self.web.port}"

    async def asyncTearDown(self):
        self.task.cancel()
        await asyncio.gather(self.task, return_exceptions=True)

    async def call(self, method, path, body=None):
        return await run_in_thread(http, method, self.base + path, body)

    async def test_chat_session_flow(self):
        status, _, raw = await self.call("POST", "/api/chat", {"kind": "start", "value": "site"})
        self.assertEqual(status, 200)
        data = json.loads(raw)
        self.assertTrue(data["uid"].startswith("web:"))
        self.assertIn("Пройдём короткий тест", data["replies"][0]["text"])

        session = {"uid": data["uid"], "sig": data["sig"]}
        status, _, raw = await self.call("POST", "/api/chat", {**session, "kind": "action", "value": "begin"})
        reply = json.loads(raw)["replies"][0]
        self.assertEqual(reply["meta"]["progress"], {"step": 0, "total": 5})
        self.assertEqual(reply["buttons"][0][0], {"text": "Математика", "action": "ans:0:0"})

        # подделанная подпись → новый профиль, чужие данные недоступны
        status, _, raw = await self.call("POST", "/api/chat", {"uid": data["uid"], "sig": "bad", "kind": "action", "value": "begin"})
        self.assertNotEqual(json.loads(raw)["uid"], data["uid"])

    async def test_bad_requests(self):
        self.assertEqual((await self.call("POST", "/api/chat", {"kind": "hack"}))[0], 400)
        self.assertEqual((await self.call("GET", "/nope"))[0], 404)

    async def test_pages_health_stats_redirect(self):
        status, headers, body = await self.call("GET", "/")
        self.assertEqual(status, 200)
        self.assertIn("ПрофНавигатор FinTech", body)
        self.assertEqual((await self.call("GET", "/widget.js"))[0], 200)
        self.assertEqual((await self.call("GET", "/dashboard"))[0], 200)
        self.assertIn("Узнай свою профессию", (await self.call("GET", "/poster"))[2])

        status, _, body = await self.call("GET", "/health")
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(body)["status"], "ok")

        self.assertEqual((await self.call("GET", "/api/stats?token=wrong"))[0], 403)
        status, _, body = await self.call("GET", "/api/stats?token=secret")
        self.assertEqual(status, 200)
        self.assertIn("summary", json.loads(body))

        status, headers, _ = await self.call("GET", "/go/finatlon?u=max:77")
        self.assertEqual(status, 302)
        self.assertEqual(headers["Location"], "https://fin-olimp.ru")
        self.assertTrue(any(e["name"] == "finatlon_click" and e["uid"] == "max:77" for e in self.storage.events()))

    async def test_rate_limit(self):
        self.web.limiter.limit = 3
        codes = [(await self.call("POST", "/api/chat", {"kind": "start"}))[0] for _ in range(5)]
        self.assertEqual(codes[:3], [200, 200, 200])
        self.assertEqual(codes[-1], 429)


if __name__ == "__main__":
    unittest.main()
