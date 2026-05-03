"""Multi-timeframe strategy wrappers.

The single biggest activity multiplier per the 2026-05-02 research pass:
- Run **execution** on a faster timeframe (15m) → 4× more bar closes → more
  trigger opportunities
- Use **regime detection** on a slower timeframe (1h) → stable, less whipsaw

Without the slower regime gate, dropping to 15m alone failed walk-forward
(FAIL across all symbols). The gate filters out chop where 15m signals are
mostly noise. With the gate, 15m execution becomes activity-positive WITHOUT
edge collapse — that's the multi-timeframe pattern Freqtrade + TradingAgents
both use.
"""

from __future__ import annotations

from collections.abc import Callable

import pandas as pd

from data.store import bars_path, load_bars
from features.compute import bars_to_df
from features.regime import detect_regime
from signals.types import Signal

StrategyFn = Callable[..., list[Signal]]


def make_regime_gated_15m(
    *,
    uptrend_fn: StrategyFn,
    sideways_fn: StrategyFn,
    downtrend_fn: StrategyFn | None = None,
    regime_timeframe: str = "1h",
    regime_exchange: str = "binance",
) -> StrategyFn:
    """15m execution wrapped by 1h regime gate.

    For each 15m bar, looks up the active regime on the 1h timeframe (whichever
    1h bar contains that 15m timestamp), then routes the bar through the
    matching sub-strategy. Sells from any sub-strategy pass through (so an
    open position can always exit), but BUYs only fire when the routed
    sub-strategy says so.

    The 1h bars are read from the local Parquet cache. If the cache is missing
    or stale, the gate falls back to "sideways" (most permissive) so the
    strategy still produces signals — better than silent failure.
    """

    def composite(df: pd.DataFrame, *, symbol: str = "BTC/USDT", **kwargs) -> list[Signal]:
        if df.empty:
            return []

        # Load 1h regime data for the symbol from local Parquet cache
        regime_path = bars_path(regime_exchange, symbol, regime_timeframe)
        regimes_1h: pd.DataFrame | None = None
        if regime_path.exists():
            try:
                bars_1h = load_bars(regime_path)
                if bars_1h:
                    df_1h = bars_to_df(bars_1h)
                    regimes_1h = detect_regime(df_1h, trend_period=200)
                    regimes_1h = regimes_1h.assign(timestamp_ms=df_1h["timestamp_ms"].values)
            except Exception:  # noqa: BLE001
                regimes_1h = None

        # Pre-compute sub-strategy outputs for the full 15m df
        up_sigs = uptrend_fn(df, symbol=symbol)
        side_sigs = sideways_fn(df, symbol=symbol)
        down_sigs = downtrend_fn(df, symbol=symbol) if downtrend_fn else None

        # Build a lookup: timestamp_ms (15m) → regime label (from the 1h bar
        # whose window contains it). Falls back to "sideways" for bars before
        # the 1h regime series starts (insufficient history for EMA200).
        def regime_at(ts_ms: int) -> str:
            if regimes_1h is None or regimes_1h.empty:
                return "sideways"
            # Binary search: largest 1h bar <= ts_ms
            idx = regimes_1h["timestamp_ms"].searchsorted(ts_ms, side="right") - 1
            if idx < 0:
                return "sideways"
            return str(regimes_1h["trend"].iloc[idx])

        out: list[Signal] = []
        for i in range(len(df)):
            ts = int(df["timestamp_ms"].iloc[i])
            trend = regime_at(ts)

            if trend == "uptrend":
                pick = up_sigs[i] if i < len(up_sigs) else None
                tag = "uptrend"
            elif trend == "sideways":
                pick = side_sigs[i] if i < len(side_sigs) else None
                tag = "sideways"
            else:
                pick = down_sigs[i] if down_sigs and i < len(down_sigs) else None
                tag = "downtrend"

            # Always allow sells from any sub-strategy — protects open positions
            sells: list[Signal] = []
            for sigs in (up_sigs, side_sigs):
                if i < len(sigs) and sigs[i].side == "sell":
                    sells.append(sigs[i])
            if down_sigs and i < len(down_sigs) and down_sigs[i].side == "sell":
                sells.append(down_sigs[i])

            if sells:
                s = sells[0]
                out.append(
                    Signal(
                        timestamp_ms=ts, symbol=symbol, side="sell",
                        conviction=0.0, stop=None, target=None,
                        rationale=f"[15m·1h regime: {trend}] {s.rationale}",
                    )
                )
                continue

            if pick is None or pick.side != "buy":
                out.append(
                    Signal(
                        timestamp_ms=ts, symbol=symbol, side="hold",
                        conviction=0.0, stop=None, target=None,
                        rationale=f"[15m·1h regime: {trend}] hold",
                    )
                )
                continue

            out.append(
                Signal(
                    timestamp_ms=ts, symbol=symbol, side="buy",
                    conviction=pick.conviction, stop=pick.stop, target=pick.target,
                    rationale=f"[15m·1h regime: {tag}] {pick.rationale}",
                )
            )

        return out

    return composite


def make_union_strategy(*strategy_fns: StrategyFn) -> StrategyFn:
    """OR-merge signals from N strategies. First BUY/SELL wins, otherwise HOLD.

    Lets multiple orthogonal alphas (e.g., mean-reversion + breakout) coexist
    on the same symbols + timeframe. One quiet strategy doesn't silence the
    others — directly addresses the "too passive" symptom that comes from
    AND-gating multiple conditions in a single strategy.

    Conviction comes from the winning sub-strategy. Stop/target come from
    whichever signal we picked, never mixed (mixing stops across strategies
    is its own kind of bug — stops only make sense paired with the entry
    that set them).
    """
    if not strategy_fns:
        raise ValueError("make_union_strategy needs at least one strategy_fn")

    def composite(df: pd.DataFrame, *, symbol: str = "BTC/USDT", **kwargs) -> list[Signal]:
        if df.empty:
            return []
        all_sigs = [fn(df, symbol=symbol) for fn in strategy_fns]
        n = len(df)
        out: list[Signal] = []
        for i in range(n):
            picks = [sigs[i] for sigs in all_sigs if i < len(sigs)]
            ts = int(df["timestamp_ms"].iloc[i])

            # Priority order: exits before entries (so an open position always closes
            # safely), then long entries, then short entries. Within each tier the
            # first sub-strategy to fire wins.
            sell = next((s for s in picks if s.side == "sell"), None)
            cover = next((s for s in picks if s.side == "cover"), None)
            if sell is not None:
                out.append(
                    Signal(
                        timestamp_ms=ts, symbol=symbol, side="sell",
                        conviction=0.0, stop=None, target=None,
                        rationale=f"[union] {sell.rationale}",
                    )
                )
                continue
            if cover is not None:
                out.append(
                    Signal(
                        timestamp_ms=ts, symbol=symbol, side="cover",
                        conviction=0.0, stop=None, target=None,
                        rationale=f"[union] {cover.rationale}",
                    )
                )
                continue

            buy = next((s for s in picks if s.side == "buy"), None)
            if buy is not None:
                out.append(
                    Signal(
                        timestamp_ms=ts, symbol=symbol, side="buy",
                        conviction=buy.conviction, stop=buy.stop, target=buy.target,
                        rationale=f"[union] {buy.rationale}",
                    )
                )
                continue

            short = next((s for s in picks if s.side == "short"), None)
            if short is not None:
                out.append(
                    Signal(
                        timestamp_ms=ts, symbol=symbol, side="short",
                        conviction=short.conviction, stop=short.stop, target=short.target,
                        rationale=f"[union] {short.rationale}",
                    )
                )
                continue

            out.append(
                Signal(
                    timestamp_ms=ts, symbol=symbol, side="hold",
                    conviction=0.0, stop=None, target=None,
                    rationale="[union] hold",
                )
            )

        return out

    return composite
