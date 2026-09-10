import asyncio
import unittest

from core.ai import AIError, AIProvider, AIService, build_ai
from config import Settings


class Provider(AIProvider):
    def __init__(self, name, behaviour):
        self.name = name
        self.behaviour = behaviour
        self.calls = 0

    async def complete(self, system, user, *, max_tokens, temperature):
        self.calls += 1
        if self.behaviour == "fail":
            raise AIError("не работает")
        if self.behaviour == "slow":
            await asyncio.sleep(5)
        return "**Ответ** от " + self.name


class AIServiceTest(unittest.IsolatedAsyncioTestCase):
    async def test_fallback_chain(self):
        bad, good = Provider("yandexgpt", "fail"), Provider("gigachat", "ok")
        service = AIService([bad, good], retries=1)
        text, name = await service.generate("s", "u")
        self.assertEqual((text, name), ("Ответ от gigachat", "gigachat"))  # markdown убран
        self.assertEqual(bad.calls, 2)

    async def test_circuit_breaker_skips_broken_provider(self):
        bad, good = Provider("yandexgpt", "fail"), Provider("openai", "ok")
        service = AIService([bad, good], retries=0, failure_threshold=2, cooldown=60)
        for _ in range(3):
            await service.generate("s", "u")
        self.assertEqual(bad.calls, 2)
        self.assertFalse(service.status()[0]["available"])

    async def test_total_timeout_returns_none(self):
        service = AIService([Provider("slow", "slow")], retries=0, call_timeout=10, total_timeout=0.1)
        self.assertEqual(await service.generate("s", "u"), (None, None))

    async def test_no_keys_means_disabled(self):
        service = build_ai(Settings())
        self.assertFalse(service.enabled)
        self.assertEqual(await service.generate("s", "u"), (None, None))

    def test_provider_order_from_settings(self):
        s = Settings(ai_providers=("openai", "yandexgpt"), openai_api_key="k", yandex_api_key="k", yandex_folder_id="f")
        self.assertEqual([p.name for p in build_ai(s).providers], ["openai", "yandexgpt"])


if __name__ == "__main__":
    unittest.main()
