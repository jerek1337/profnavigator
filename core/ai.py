"""ИИ-провайдеры: YandexGPT, GigaChat и OpenAI-совместимые API.

Провайдеры перебираются по цепочке, у каждого свой таймаут, повтор и circuit
breaker. Не уложились в общий бюджет времени — движок отдаёт шаблонный ответ.
"""
from __future__ import annotations

import asyncio
import logging
import ssl
import time
import uuid
from dataclasses import dataclass
from typing import Any, Callable

from .http import HttpResult, request
from .text import sanitize_ai

log = logging.getLogger(__name__)

class AIError(Exception):
    """Ошибка ответа ИИ-провайдера."""

def _json_or_raise(res: HttpResult, provider: str) -> dict[str, Any]:
    if res.status >= 400:
        raise AIError(f"{provider}: HTTP {res.status}: {res.text[:200]}")
    if not isinstance(res.data, dict):
        raise AIError(f"{provider}: ответ не в формате JSON")
    return res.data

class AIProvider:
    name = "base"

    async def complete(self, system: str, user: str, *, max_tokens: int, temperature: float) -> str:
        raise NotImplementedError

class YandexGPTProvider(AIProvider):
    name = "yandexgpt"
    url = "https://llm.api.cloud.yandex.net/foundationModels/v1/completion"

    def __init__(self, api_key: str, folder_id: str, model: str = "yandexgpt-lite/latest", timeout: float = 20.0):
        self.api_key, self.folder_id, self.model, self.timeout = api_key, folder_id, model, timeout

    async def complete(self, system: str, user: str, *, max_tokens: int, temperature: float) -> str:
        payload = {
            "modelUri": f"gpt://{self.folder_id}/{self.model}",
            "completionOptions": {"stream": False, "temperature": temperature, "maxTokens": str(max_tokens)},
            "messages": [{"role": "system", "text": system}, {"role": "user", "text": user}],
        }
        headers = {"Authorization": f"Api-Key {self.api_key}", "x-folder-id": self.folder_id}
        res = await request("POST", self.url, json_body=payload, headers=headers, timeout=self.timeout)
        data = _json_or_raise(res, self.name)
        try:
            return data["result"]["alternatives"][0]["message"]["text"]
        except (KeyError, IndexError, TypeError) as exc:
            raise AIError(f"{self.name}: неожиданный формат ответа") from exc

class GigaChatProvider(AIProvider):
    name = "gigachat"
    oauth_url = "https://ngw.devices.sberbank.ru:9443/api/v2/oauth"
    api_url = "https://gigachat.devices.sberbank.ru/api/v1/chat/completions"

    def __init__(self, auth_key: str, scope: str = "GIGACHAT_API_PERS", model: str = "GigaChat", *,
                 verify_ssl: bool = True, ca_bundle: str = "", timeout: float = 20.0):
        self.auth_key, self.scope, self.model, self.timeout = auth_key, scope, model, timeout
        self.ssl_context: ssl.SSLContext | None = None
        if ca_bundle:  # сертификат НУЦ Минцифры, если его нет в системе
            self.ssl_context = ssl.create_default_context(cafile=ca_bundle)
        elif not verify_ssl:
            ctx = ssl.create_default_context()
            ctx.check_hostname = False
            ctx.verify_mode = ssl.CERT_NONE
            self.ssl_context = ctx
        self._token = ""
        self._expires_at = 0.0
        self._lock = asyncio.Lock()

    async def _get_token(self) -> str:
        async with self._lock:
            if self._token and time.time() < self._expires_at - 60:
                return self._token
            headers = {"Authorization": f"Basic {self.auth_key}", "RqUID": str(uuid.uuid4())}
            res = await request("POST", self.oauth_url, form={"scope": self.scope}, headers=headers,
                                timeout=self.timeout, ssl_context=self.ssl_context)
            data = _json_or_raise(res, self.name)
            token = data.get("access_token")
            if not token:
                raise AIError(f"{self.name}: не получен access_token")
            expires = data.get("expires_at")
            self._token = token
            self._expires_at = float(expires) / 1000 if isinstance(expires, (int, float)) else time.time() + 25 * 60
            return token

    async def complete(self, system: str, user: str, *, max_tokens: int, temperature: float) -> str:
        token = await self._get_token()
        payload = {
            "model": self.model,
            "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
            "temperature": temperature,
            "max_tokens": max_tokens,
        }
        res = await request("POST", self.api_url, json_body=payload, headers={"Authorization": f"Bearer {token}"},
                            timeout=self.timeout, ssl_context=self.ssl_context)
        if res.status == 401:
            self._token = ""  # токен истёк — получим новый при следующем вызове
        data = _json_or_raise(res, self.name)
        try:
            return data["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError) as exc:
            raise AIError(f"{self.name}: неожиданный формат ответа") from exc

class OpenAICompatibleProvider(AIProvider):
    """OpenAI и любые совместимые API (достаточно поменять base_url)."""
    name = "openai"

    def __init__(self, api_key: str, model: str = "gpt-4o-mini", base_url: str = "https://api.openai.com/v1",
                 timeout: float = 20.0):
        self.api_key, self.model, self.base_url, self.timeout = api_key, model, base_url.rstrip("/"), timeout

    async def complete(self, system: str, user: str, *, max_tokens: int, temperature: float) -> str:
        payload = {
            "model": self.model,
            "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
            "temperature": temperature,
            "max_tokens": max_tokens,
        }
        res = await request("POST", f"{self.base_url}/chat/completions", json_body=payload,
                            headers={"Authorization": f"Bearer {self.api_key}"}, timeout=self.timeout)
        data = _json_or_raise(res, self.name)
        try:
            return data["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError) as exc:
            raise AIError(f"{self.name}: неожиданный формат ответа") from exc

@dataclass
class _Breaker:
    failures: int = 0
    open_until: float = 0.0

class AIService:
    def __init__(self, providers: list[AIProvider], *, retries: int = 1, call_timeout: float = 20.0,
                 total_timeout: float = 30.0, failure_threshold: int = 3, cooldown: float = 60.0,
                 clock: Callable[[], float] = time.monotonic) -> None:
        self.providers = list(providers)
        self.retries = max(0, retries)
        self.call_timeout = call_timeout
        self.total_timeout = total_timeout
        self.failure_threshold = failure_threshold
        self.cooldown = cooldown
        self.clock = clock
        self._breakers = {p.name: _Breaker() for p in self.providers}

    @property
    def enabled(self) -> bool:
        return bool(self.providers)

    def status(self) -> list[dict[str, Any]]:
        now = self.clock()
        return [{"name": p.name, "available": self._breakers[p.name].open_until <= now,
                 "failures": self._breakers[p.name].failures} for p in self.providers]

    async def generate(self, system: str, user: str, *, max_tokens: int = 600,
                       temperature: float = 0.6) -> tuple[str | None, str | None]:
        """Возвращает (текст, имя провайдера) или (None, None), если ИИ недоступен."""
        if not self.providers:
            return None, None
        try:
            return await asyncio.wait_for(self._chain(system, user, max_tokens, temperature), self.total_timeout)
        except asyncio.TimeoutError:
            log.warning("ИИ не ответил за %.0f с — будет использован шаблон", self.total_timeout)
            return None, None

    async def _chain(self, system: str, user: str, max_tokens: int, temperature: float) -> tuple[str | None, str | None]:
        for provider in self.providers:
            breaker = self._breakers[provider.name]
            if breaker.open_until > self.clock():
                continue
            for attempt in range(self.retries + 1):
                try:
                    raw = await asyncio.wait_for(
                        provider.complete(system, user, max_tokens=max_tokens, temperature=temperature),
                        self.call_timeout,
                    )
                    text = sanitize_ai(raw or "")
                    if not text:
                        raise AIError(f"{provider.name}: пустой ответ")
                except asyncio.CancelledError:
                    raise
                except Exception as exc:  # noqa: BLE001
                    breaker.failures += 1
                    log.warning("Провайдер %s: ошибка (попытка %d): %s", provider.name, attempt + 1, exc)
                    if breaker.failures >= self.failure_threshold:
                        breaker.open_until = self.clock() + self.cooldown
                        log.error("Провайдер %s временно отключён на %.0f с", provider.name, self.cooldown)
                        break
                    if attempt < self.retries:
                        await asyncio.sleep(0.4 * (attempt + 1))
                else:
                    breaker.failures = 0
                    return text, provider.name
        return None, None

# Чтобы подключить новый ИИ, добавьте фабрику сюда.
PROVIDER_FACTORIES: dict[str, Callable[[Any], AIProvider | None]] = {
    "yandexgpt": lambda s: YandexGPTProvider(s.yandex_api_key, s.yandex_folder_id, s.yandex_model, s.ai_timeout)
    if s.yandex_api_key and s.yandex_folder_id else None,
    "gigachat": lambda s: GigaChatProvider(s.gigachat_auth_key, s.gigachat_scope, s.gigachat_model,
                                           verify_ssl=s.gigachat_verify_ssl, ca_bundle=s.gigachat_ca_bundle,
                                           timeout=s.ai_timeout)
    if s.gigachat_auth_key else None,
    "openai": lambda s: OpenAICompatibleProvider(s.openai_api_key, s.openai_model, s.openai_base_url, s.ai_timeout)
    if s.openai_api_key else None,
}

def build_ai(settings: Any) -> AIService:
    providers: list[AIProvider] = []
    for name in settings.ai_providers:
        factory = PROVIDER_FACTORIES.get(name)
        provider = factory(settings) if factory else None
        if provider:
            providers.append(provider)
    if providers:
        log.info("ИИ-провайдеры: %s", " → ".join(p.name for p in providers))
    else:
        log.warning("Ключи ИИ не заданы — бот работает на шаблонных ответах")
    return AIService(providers, retries=settings.ai_retries, call_timeout=settings.ai_timeout,
                     total_timeout=settings.ai_total_timeout)
