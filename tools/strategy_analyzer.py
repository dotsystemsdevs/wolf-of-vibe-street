"""Per-strategy P&L + decay analysis.

Phase 2 deliverable. Each symbol uses a strategy from .env's
`TRADERBOT_STRATEGY_PER_SYMBOL` map (with global TRADERBOT_STRATEGY fallback);
this module rolls up trades to that strategy and surfaces per-strategy
performance + decay flags so the operator knows which alpha is actually working.

Decay flag: a strategy is "decaying" if it has ≥5 trades, rolling-7d P&L < 0,
and profit factor < 0.7. The threshold is intentionally lenient — we want
early warning, not whip-fast rotation. Operator decides whether to disable.

Public functions:
  - per_strategy_pnl(rows, symbol_strategy_map) → list[StrategyStats]
  - decay_flags(stats) → list[str]   # strategy_ids currently flagged

Used by:
  - tools/daily_summary (telegram digest of which strategy is leading)
  - web/main (dashboard "Strategies" widget)
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pandas as pd

ROOT = Path(__file__).resolve().parent.parent


@dataclass
class StrategyStats:
    strategy_id: str
    symbols: list[str] = field(default_factory=list)
    trades: int = 0
    wins: int = 0
    losses: int = 0
    total_pnl: float = 0.0
    pnl_24h: float = 0.0
    pnl_7d: float = 0.0
    profit_factor: float = 0.0
    win_rate: float = 0.0
    avg_win: float = 0.0
    avg_loss: float = 0.0
    is_decaying: bool = False
    decay_reason: str | None = None


def parse_per_symbol_map(env: dict[str, str] | None = None) -> dict[str, str]:
    """Pull the symbol→strategy mapping from .env. Symbols not listed fall
    back to the global TRADERBOT_STRATEGY (or 'regime_aware_dipbuy' default)."""
    src = env if env is not None else os.environ
    raw = (src.get("TRADERBOT_STRATEGY_PER_SYMBOL") or "").strip()
    out: dict[str, str] = {}
    if not raw:
        return out
    for pair in raw.split(","):
        pair = pair.strip()
        if ":" in pair:
            sym, sid = (p.strip() for p in pair.split(":", 1))
            if sym and sid:
                out[sym] = sid
    return out


def _strategy_for(
    symbol: str, per_symbol: dict[str, str], default: str
) -> str:
    return per_symbol.get(symbol, default)


def per_strategy_pnl(
    trades: pd.DataFrame,
    *,
    per_symbol_map: dict[str, str],
    default_strategy: str,
    now_ms: int | None = None,
) -> list[StrategyStats]:
    """Aggregate `trades` (output of trades_dataframe) by strategy.

    Each trade is attributed to the strategy that owned that symbol per the
    .env mapping. Per-symbol overrides take priority; global default catches
    the rest.
    """
    if trades.empty:
        return []
    if now_ms is None:
        now_ms = int(pd.Timestamp.now("UTC").timestamp() * 1000)

    by_strategy: dict[str, StrategyStats] = {}
    for _, t in trades.iterrows():
        sym = t["symbol"]
        sid = _strategy_for(sym, per_symbol_map, default_strategy)
        s = by_strategy.setdefault(sid, StrategyStats(strategy_id=sid))
        if sym not in s.symbols:
            s.symbols.append(sym)
        s.trades += 1
        pnl = float(t["pnl"])
        s.total_pnl += pnl
        if pnl > 0:
            s.wins += 1
        elif pnl < 0:
            s.losses += 1
        exit_ts = int(t["exit_ts"])
        if exit_ts >= now_ms - 24 * 3_600_000:
            s.pnl_24h += pnl
        if exit_ts >= now_ms - 7 * 24 * 3_600_000:
            s.pnl_7d += pnl

    # Derived metrics + decay flag
    for s in by_strategy.values():
        n = s.trades
        s.win_rate = s.wins / n if n else 0.0
        sym_trades = trades[trades["symbol"].isin(s.symbols)]
        win_pnls = sym_trades[sym_trades["pnl"] > 0]["pnl"].tolist()
        loss_pnls = sym_trades[sym_trades["pnl"] < 0]["pnl"].tolist()
        s.avg_win = sum(win_pnls) / len(win_pnls) if win_pnls else 0.0
        s.avg_loss = sum(loss_pnls) / len(loss_pnls) if loss_pnls else 0.0
        if loss_pnls and sum(loss_pnls) != 0:
            s.profit_factor = sum(win_pnls) / abs(sum(loss_pnls))
        elif win_pnls:
            s.profit_factor = float("inf")
        else:
            s.profit_factor = 0.0
        # Decay flag: ≥5 trades AND 7d P&L < 0 AND profit factor < 0.7.
        # Conservative threshold — we want early warning, not whipsaw rotation.
        if n >= 5 and s.pnl_7d < 0 and s.profit_factor < 0.7:
            s.is_decaying = True
            s.decay_reason = (
                f"7d_pnl={s.pnl_7d:+.2f} pf={s.profit_factor:.2f} after {n} trades"
            )

    # Sort by total P&L descending — leaders at top, decay candidates at bottom.
    return sorted(by_strategy.values(), key=lambda s: -s.total_pnl)


def decay_flags(stats: list[StrategyStats]) -> list[str]:
    """Return strategy_ids currently flagged for decay."""
    return [s.strategy_id for s in stats if s.is_decaying]


def format_telegram_summary(stats: list[StrategyStats]) -> str:
    """One-line-per-strategy summary for the daily Telegram digest."""
    if not stats:
        return "(no closed trades yet)"
    lines: list[str] = []
    for s in stats:
        sign = "+" if s.total_pnl >= 0 else ""
        flag = " ⚠ decaying" if s.is_decaying else ""
        short = s.strategy_id.replace("regime_aware_", "RA-").replace(
            "union_meanrev_breakout", "Union"
        ).replace("ensemble_daytrader", "Ensemble").replace("RA-dipbuy", "Dipbuy")
        lines.append(
            f"{short}: {sign}${s.total_pnl:.2f} · {s.trades} trades · "
            f"PF {s.profit_factor:.2f}{flag}"
        )
    return "\n".join(lines)


# ---- CLI ----


def _load_env() -> None:
    p = ROOT / ".env"
    if not p.exists():
        return
    for line in p.read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip())


def main() -> int:
    import sys
    if str(ROOT) not in sys.path:
        sys.path.insert(0, str(ROOT))
    _load_env()

    import sqlite3
    from ui.views import trades_dataframe  # noqa: PLC0415

    con = sqlite3.connect(ROOT / "data" / "decision_log" / "traderbot.db")
    con.row_factory = sqlite3.Row
    rows: list[dict[str, Any]] = [dict(r) for r in con.execute("SELECT * FROM decisions").fetchall()]
    trades = trades_dataframe(rows)

    per_symbol = parse_per_symbol_map()
    default_strategy = (
        os.environ.get("TRADERBOT_STRATEGY")
        or os.environ.get("TRADERBOT_STRATEGY_ID")
        or "regime_aware_dipbuy"
    )

    stats = per_strategy_pnl(
        trades, per_symbol_map=per_symbol, default_strategy=default_strategy
    )

    print(f"=== Per-strategy P&L ({len(trades)} closed trades) ===")
    print()
    for s in stats:
        flag = " ⚠ DECAYING" if s.is_decaying else ""
        pf_str = f"{s.profit_factor:.2f}" if s.profit_factor != float("inf") else "∞"
        print(f"{s.strategy_id}{flag}")
        print(f"  symbols: {', '.join(s.symbols)}")
        print(f"  trades:  {s.trades} ({s.wins}W / {s.losses}L · {s.win_rate*100:.0f}% WR)")
        print(f"  total:   ${s.total_pnl:+.2f}")
        print(f"  24h:     ${s.pnl_24h:+.2f}")
        print(f"  7d:      ${s.pnl_7d:+.2f}")
        print(f"  PF:      {pf_str}")
        if s.avg_win or s.avg_loss:
            print(f"  avg win/loss: ${s.avg_win:+.2f} / ${s.avg_loss:+.2f}")
        if s.decay_reason:
            print(f"  decay:   {s.decay_reason}")
        print()
    flags = decay_flags(stats)
    if flags:
        print(f"⚠ {len(flags)} decaying strategies: {', '.join(flags)}")
    else:
        print("All strategies green.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
