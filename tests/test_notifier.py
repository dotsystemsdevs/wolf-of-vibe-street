"""Tests for tools.notifier — TelegramNotifier formatting + silent no-op."""

from __future__ import annotations

from typing import Any

from tools.notifier import NoOpNotifier, TelegramNotifier


class _FakePost:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []

    def __call__(self, url: str, *, json: dict[str, Any], timeout: float):
        self.calls.append((url, json))

        class _Resp:
            ok = True

        return _Resp()


def test_telegram_sends_when_configured() -> None:
    post = _FakePost()
    n = TelegramNotifier(token="t1", chat_id="c1", post=post)
    n.notify("INFO", "hello", "world")

    assert len(post.calls) == 1
    url, body = post.calls[0]
    assert "/bott1/sendMessage" in url
    assert body["chat_id"] == "c1"
    assert body["text"] == "[INFO] hello\nworld"


def test_telegram_no_body_omits_newline() -> None:
    post = _FakePost()
    n = TelegramNotifier(token="t", chat_id="c", post=post)
    n.notify("WARN", "title only")
    assert post.calls[0][1]["text"] == "[WARN] title only"


def test_telegram_silent_when_token_missing(monkeypatch) -> None:
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    monkeypatch.delenv("TELEGRAM_CHAT_ID", raising=False)
    post = _FakePost()

    n = TelegramNotifier(token="", chat_id="c", post=post)
    n.notify("INFO", "x")
    assert post.calls == []
    assert n.configured is False


def test_telegram_swallows_post_errors() -> None:
    """A telegram outage must not crash the bot loop."""

    def _exploding(*args, **kwargs):
        raise RuntimeError("telegram down")

    captured: list[Exception] = []
    n = TelegramNotifier(token="t", chat_id="c", post=_exploding, on_error=captured.append)
    n.notify("ERROR", "oops")
    assert len(captured) == 1
    assert "telegram down" in str(captured[0])


def test_noop_notifier_does_nothing() -> None:
    n = NoOpNotifier()
    n.notify("INFO", "x", "y")
