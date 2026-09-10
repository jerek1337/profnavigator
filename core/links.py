"""Подписанные ссылки: веб-версия для пользователя мессенджера и отслеживаемые переходы."""
from __future__ import annotations

import hashlib
import hmac
from urllib.parse import quote

class Links:
    def __init__(self, secret: str, public_url: str | None, finatlon_url: str) -> None:
        self._secret = secret.encode("utf-8")
        self.public_url = (public_url or "").rstrip("/")
        self.finatlon_url = finatlon_url

    def sign(self, uid: str) -> str:
        return hmac.new(self._secret, uid.encode("utf-8"), hashlib.sha256).hexdigest()[:32]

    def verify(self, uid: str, sig: str) -> bool:
        return hmac.compare_digest(self.sign(uid), sig or "")

    @property
    def has_public_https(self) -> bool:
        # мессенджеры принимают в кнопках только внешние https-ссылки
        return self.public_url.startswith("https://")

    def web(self, uid: str) -> str | None:
        """Ссылка на веб-версию, продолжающую тот же профиль (тест, результат, маршрут)."""
        if not self.has_public_https:
            return None
        return f"{self.public_url}/?u={quote(uid, safe='')}&s={self.sign(uid)}"

    def finatlon(self, uid: str) -> str:
        """Переход на сайт олимпиады через сервер — чтобы считать клики в аналитике."""
        if not self.has_public_https:
            return self.finatlon_url
        return f"{self.public_url}/go/finatlon?u={quote(uid, safe='')}"
