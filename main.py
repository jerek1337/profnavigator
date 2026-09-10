"""
ПрофНавигатор FinTech — точка входа.

    python main.py          # MAX (+ Telegram, если задан токен) + веб-интерфейс + напоминания
    python main.py --cli    # проверить сценарий в консоли, без токенов и интернета
"""
from __future__ import annotations

import argparse
import asyncio
import contextlib
import logging
import secrets
import signal
import time
from typing import Awaitable, Callable

from config import Settings, load_dotenv
from core import content as C
from core.ai import build_ai
from core.engine import Engine
from core.links import Links
from core.scheduler import ReminderScheduler
from core.storage import Storage

log = logging.getLogger("profnavigator")

async def supervise(name: str, factory: Callable[[], Awaitable[None]]) -> None:
    """Перезапускает упавший компонент с нарастающей паузой — бот восстанавливается сам."""
    delay = 1.0
    while True:
        started = time.monotonic()
        try:
            await factory()
            log.warning("Компонент «%s» остановился — перезапуск", name)
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001
            log.exception("Компонент «%s» упал — перезапуск через %.0f с", name, delay)
        if time.monotonic() - started > 60:
            delay = 1.0
        await asyncio.sleep(delay)
        delay = min(delay * 2, 60.0)

def build_core(settings: Settings) -> tuple[Storage, Links, Engine]:
    storage = Storage(settings.db_path)
    secret = settings.secret_key or storage.kv_get("secret_key")
    if not secret:
        secret = secrets.token_hex(32)
        storage.kv_set("secret_key", secret)
    links = Links(secret, settings.public_url, C.FINATLON_URL)
    engine = Engine(storage, build_ai(settings), links, qa_daily_limit=settings.qa_daily_limit)
    return storage, links, engine

async def run(settings: Settings, *, cli: bool = False) -> None:
    storage, links, engine = build_core(settings)

    if cli:
        from adapters.cli import CliAdapter
        await CliAdapter(engine).run()
        return

    adapters = []
    if settings.max_token:
        from adapters.max_bot import MaxAdapter
        adapters.append(MaxAdapter(engine, storage, settings.max_token, base_url=settings.max_api_url,
                                   auth_mode=settings.max_auth_mode, bot_link=settings.bot_link))
    else:
        log.warning("MAX_TOKEN не задан — бот в MAX не запущен")
    if settings.telegram_token:
        from adapters.telegram_bot import TelegramAdapter
        adapters.append(TelegramAdapter(engine, storage, settings.telegram_token, bot_link=settings.bot_link))

    tasks = [asyncio.create_task(supervise(a.platform, a.run)) for a in adapters]
    if settings.web_enabled:
        from adapters.web import WebServer
        web = WebServer(engine, storage, links, settings, ai=engine.ai, adapters=tuple(adapters))
        tasks.append(asyncio.create_task(supervise("web", web.run)))
    scheduler = ReminderScheduler(engine, storage, {a.platform: a for a in adapters},
                                  delay_seconds=settings.reminder_delay_hours * 3600,
                                  max_count=settings.reminder_max, interval=settings.reminder_check_seconds)
    tasks.append(asyncio.create_task(supervise("reminders", scheduler.run)))

    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        with contextlib.suppress(NotImplementedError, RuntimeError):  # Windows
            loop.add_signal_handler(sig, stop.set)
    try:
        await stop.wait()
    finally:
        log.info("Остановка…")
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        storage.close()

def main() -> None:
    parser = argparse.ArgumentParser(description=C.BOT_TITLE)
    parser.add_argument("--cli", action="store_true", help="консольный режим для проверки сценария")
    parser.add_argument("--env", default=".env", help="путь к файлу настроек")
    args = parser.parse_args()

    load_dotenv(args.env)
    settings = Settings.from_env()
    logging.basicConfig(level=logging.WARNING if args.cli else settings.log_level.upper(),
                        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s")
    with contextlib.suppress(KeyboardInterrupt):
        asyncio.run(run(settings, cli=args.cli))

if __name__ == "__main__":
    main()
