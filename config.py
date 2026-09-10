"""Настройки из переменных окружения или файла .env."""
from __future__ import annotations

import os
from dataclasses import dataclass, field, fields
from pathlib import Path
from typing import Mapping

def load_dotenv(path: str | Path = ".env") -> None:
    """Минимальный загрузчик .env (без внешних библиотек). Не перезаписывает уже заданные переменные."""
    p = Path(path)
    if not p.is_file():
        return
    for line in p.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = (part.strip() for part in line.split("=", 1))
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        os.environ.setdefault(key, value)

@dataclass
class Settings:
    # MAX
    max_token: str = ""
    max_api_url: str = "https://platform-api.max.ru"
    max_auth_mode: str = "header"          # header | query
    # Telegram (необязательно)
    telegram_token: str = ""
    # ссылка на бота для QR-кода и групповых чатов, например https://max.ru/<имя_бота>
    bot_link: str = ""

    # ИИ
    ai_providers: tuple[str, ...] = ("yandexgpt", "gigachat", "openai")
    ai_timeout: float = 20.0
    ai_total_timeout: float = 30.0
    ai_retries: int = 1
    yandex_api_key: str = ""
    yandex_folder_id: str = ""
    yandex_model: str = "yandexgpt-lite/latest"
    gigachat_auth_key: str = ""
    gigachat_scope: str = "GIGACHAT_API_PERS"
    gigachat_model: str = "GigaChat"
    gigachat_verify_ssl: bool = True
    gigachat_ca_bundle: str = ""
    openai_api_key: str = ""
    openai_model: str = "gpt-4o-mini"
    openai_base_url: str = "https://api.openai.com/v1"

    # Веб
    web_enabled: bool = True
    web_host: str = "0.0.0.0"
    web_port: int = 8080
    public_url: str = ""                   # https://… — нужен для кнопок-ссылок в мессенджерах
    admin_token: str = ""                  # пароль для дашборда аналитики
    secret_key: str = ""                   # подпись ссылок; если пусто — создаётся автоматически
    trust_proxy: bool = False

    # Данные и напоминания
    db_path: str = "data/profnavigator.db"
    reminder_delay_hours: float = 168.0    # 7 дней
    reminder_max: int = 4
    reminder_check_seconds: int = 300
    qa_daily_limit: int = 20
    log_level: str = "INFO"

    extra: dict[str, str] = field(default_factory=dict, repr=False)

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> "Settings":
        env = os.environ if env is None else env
        values = {}
        for f in fields(cls):
            if f.name == "extra":
                continue
            raw = env.get(f.name.upper())
            if raw is None or raw == "":
                continue
            default = f.default
            if isinstance(default, bool):
                values[f.name] = raw.strip().lower() in {"1", "true", "yes", "on", "да"}
            elif isinstance(default, int):
                values[f.name] = int(raw)
            elif isinstance(default, float):
                values[f.name] = float(raw)
            elif isinstance(default, tuple):
                values[f.name] = tuple(x.strip().lower() for x in raw.split(",") if x.strip())
            else:
                values[f.name] = raw.strip()
        return cls(**values)
