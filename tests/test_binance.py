"""Tests for data.binance — happy / edge / failure (CLAUDE.md §3.1 3-test rule)."""

from __future__ import annotations

import pytest

from data.binance import Bar, fetch_ohlcv

SAMPLE_RAW = [
    [1700000000000, 35000.0, 35100.0, 34900.0, 35050.0, 12.5],
    [1700003600000, 35050.0, 35200.0, 35000.0, 35150.0, 8.2],
]


class _FakeClient:
    def __init__(self, raw: list[list[float]] | None = None, exc: Exception | None = None):
        self.raw = raw or []
        self.exc = exc
        self.calls: list[tuple[str, str, int | None]] = []

    def fetch_ohlcv(
        self, symbol: str, timeframe: str = "1h", since: int | None = None, limit: int | None = None
    ) -> list[list[float]]:
        self.calls.append((symbol, timeframe, limit))
        if self.exc is not None:
            raise self.exc
        return self.raw


def test_expected_returns_typed_bars_in_order() -> None:
    """Happy path: client returns 2 raw rows → 2 Bar dicts with correct fields."""
    client = _FakeClient(raw=SAMPLE_RAW)
    bars = fetch_ohlcv("BTC/USDT", timeframe="1h", limit=2, client=client)

    assert len(bars) == 2
    assert client.calls == [("BTC/USDT", "1h", 2)]
    first: Bar = bars[0]
    assert first["timestamp_ms"] == 1700000000000
    assert first["open"] == 35000.0
    assert first["close"] == 35050.0
    assert first["volume"] == 12.5


def test_edge_empty_response_returns_empty_list() -> None:
    """Edge: exchange returns no data (e.g. brand-new symbol, future `since`) → [] not error."""
    client = _FakeClient(raw=[])
    assert fetch_ohlcv("BTC/USDT", limit=100, client=client) == []


@pytest.mark.parametrize(
    "kwargs",
    [
        {"symbol": "BTCUSDT"},  # missing slash
        {"symbol": ""},  # empty
        {"symbol": "BTC/USDT", "timeframe": "7m"},  # unsupported tf
        {"symbol": "BTC/USDT", "limit": 0},  # below range
        {"symbol": "BTC/USDT", "limit": 1001},  # above range
    ],
)
def test_failure_invalid_inputs_raise_value_error(kwargs: dict) -> None:
    """Failure: bad inputs raise ValueError before any network call."""
    client = _FakeClient(raw=SAMPLE_RAW)
    with pytest.raises(ValueError):
        fetch_ohlcv(client=client, **kwargs)
    assert client.calls == [], "validation must run before fetch_ohlcv is called"


def test_failure_network_error_propagates() -> None:
    """After retries exhausted, the last NetworkError is raised (not swallowed)."""
    import ccxt

    client = _FakeClient(exc=ccxt.NetworkError("upstream down"))
    with pytest.raises(ccxt.NetworkError, match="upstream down"):
        fetch_ohlcv("BTC/USDT", client=client, max_retries=1, base_backoff_s=0)


def test_transient_errors_retry_then_succeed() -> None:
    """Network blips: fail twice, third fetch returns data."""
    import ccxt

    class _Flaky:
        def __init__(self) -> None:
            self.n = 0
            self.raw = [SAMPLE_RAW[0]]

        def fetch_ohlcv(
            self, symbol: str, timeframe: str = "1h", since: int | None = None, limit: int | None = None
        ) -> list[list[float]]:  # noqa: ARG002
            self.n += 1
            if self.n < 3:
                raise ccxt.NetworkError("blip")
            return self.raw

    flaky = _Flaky()
    bars = fetch_ohlcv("BTC/USDT", timeframe="1h", limit=1, client=flaky, base_backoff_s=0)
    assert flaky.n == 3
    assert len(bars) == 1
    assert bars[0]["close"] == 35050.0


def test_non_transient_error_not_retried() -> None:
    """InvalidOrder / non-network errors: single attempt, no sleep loop worth retrying."""
    import ccxt

    class _Bad:
        def fetch_ohlcv(
            self, symbol: str, timeframe: str = "1h", since: int | None = None, limit: int | None = None
        ) -> list[list[float]]:  # noqa: ARG002
            raise ccxt.BadSymbol("nope")

    with pytest.raises(ccxt.BadSymbol, match="nope"):
        fetch_ohlcv("BTC/USDT", client=_Bad(), max_retries=4, base_backoff_s=0)
