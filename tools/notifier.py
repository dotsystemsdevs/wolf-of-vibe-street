"""Outbound notifications. Notifier is a Protocol so the loop can take any backend.

`TelegramNotifier` reads `TELEGRAM_BOT_TOKEN` + `TELEGRAM_CHAT_ID` from env if not
passed explicitly. If either is missing, `notify()` is a silent no-op so the loop
keeps running in dev environments without telegram credentials. Failed sends are
swallowed (logged via callback if provided) so a Telegram outage cannot kill the bot.
"""

from __future__ import annotations

import os
from collections.abc import Callable
from typing import Literal, Protocol

import requests

Level = Literal["INFO", "WARN", "ERROR"]


class Notifier(Protocol):
    def notify(self, level: Level, title: str, body: str = "") -> None: ...


class NoOpNotifier:
    """Default for tests / dev when telegram isn't configured."""

    def notify(self, level: Level, title: str, body: str = "") -> None:
        return


class TelegramNotifier:
    """Sends to Telegram via the Bot API. Silent no-op if creds are missing."""

    BASE_URL = "https://api.telegram.org"

    def __init__(
        self,
        token: str | None = None,
        chat_id: str | None = None,
        *,
        post: Callable[..., requests.Response] | None = None,
        on_error: Callable[[Exception], None] | None = None,
        timeout_s: float = 10.0,
    ):
        self.token = token if token is not None else os.environ.get("TELEGRAM_BOT_TOKEN", "")
        self.chat_id = chat_id if chat_id is not None else os.environ.get("TELEGRAM_CHAT_ID", "")
        self._post = post or requests.post
        self._on_error = on_error
        self._timeout_s = timeout_s

    @property
    def configured(self) -> bool:
        return bool(self.token and self.chat_id)

    def notify(self, level: Level, title: str, body: str = "") -> None:
        if not self.configured:
            return
        text = f"[{level}] {title}"
        if body:
            text = f"{text}\n{body}"
        url = f"{self.BASE_URL}/bot{self.token}/sendMessage"
        try:
            self._post(
                url,
                json={"chat_id": self.chat_id, "text": text},
                timeout=self._timeout_s,
            )
        except Exception as exc:
            if self._on_error is not None:
                self._on_error(exc)
