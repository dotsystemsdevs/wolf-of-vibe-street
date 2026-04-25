# @architecture.md — Current File Map & Invariants

> Always read at session start. Updated whenever architecture changes.
> Status as of 2026-04-25: **scaffold landed. Empty modules, smoke tests green. No business logic yet.**

---

## 1. What exists right now

```
traderbot/
├── CLAUDE.md, knowledge.md, experiences.md, README.md
├── pyproject.toml          # uv project, Python >=3.12, ruff + pytest configured
├── .env.example            # env-var template (LIVE_TRADING, KILL_SWITCH, API keys)
├── .gitignore, .gitattributes
├── memory-bank/            # @architecture, @design-doc, implementation-plan, progress
├── agents/ strategies/ signals/ features/ execution/ risk/ backtest/
├── memory/ tools/ api/ ui/ workers/   # all empty modules with __init__.py
├── data/binance.py         # OHLCV fetcher (CCXT, public REST)
├── data/backfill.py        # paginated historical OHLCV
├── data/store.py           # Parquet save/load + canonical bars_path()
├── features/compute.py     # bars_to_df, returns, ema, rsi, atr, volatility_regime
├── signals/types.py        # Signal dataclass (validates buy → stop required)
├── strategies/baseline_ema_cross.py  # long-only EMA(12,26) cross w/ ATR stops
├── risk/sizing.py          # fixed-% risk sizing (default 0.5%, cap 1%)
├── backtest/engine.py      # walk-forward, single-position, cost-aware sim
├── backtest/metrics.py     # sharpe, sortino, max_dd, win_rate, BE_WR (S-50)
├── data/{state,decision_log,bars,cache}/  # gitignored runtime dirs
│   └── bars/binance/BTC_USDT/1h.parquet (local — 30 days, 720 rows, 34 KB)
├── config/live/            # gitignored live-config dir
└── tests/test_smoke.py     # 13 passing tests: every module imports + Python version
```

Data layer (read-only):
- `data/binance.py` — typed `fetch_ohlcv(symbol, timeframe, limit) -> list[Bar]`. Injectable `OHLCVClient` Protocol for tests.
- `data/backfill.py` — `backfill_ohlcv(..., since_ms, until_ms, chunk_size, sleep_s)` paginates until end-of-data or `until_ms`. Dedups overlapping pages by timestamp.
- `data/store.py` — Parquet save/load. `bars_path(exchange, symbol, timeframe)` → canonical `data/bars/{exchange}/{symbol_with_underscore}/{tf}.parquet`. `save_bars` is idempotent: merges with existing file, dedups, sorts.

All verified live against Binance public REST. No auth used. No order-placement code anywhere yet.

Features layer (causal — I-2 + P-05):
- `features/compute.py` — `bars_to_df`, `returns`, `ema`, `rsi`, `atr`, `volatility_regime`. Same module imported by train, backtest, live (I-2). All functions are causal — feature at time `t` uses only data ≤ `t`. The lookahead guard test in `tests/test_compute.py::test_no_lookahead_modifying_future_does_not_change_past` parametrizes over every public feature: if any future-touching op (e.g. `.shift(-1)`) is added later, that test fails.

End-to-end pipe (Phase 1 vertical slice):
- `data → features → strategy → risk → backtest`. One pure function per layer; each layer takes a DataFrame or list and returns one. The 30-day BTC parquet runs through the whole stack in <1s.
- Signal contract (`signals/types.py::Signal`): `buy` requires a `stop` (S-15 enforced in `__post_init__`); `sell` is exit-only; `conviction ∈ [-1, +1]` (S-58).
- Backtest invariants: entries fill at *next* bar's open (no peek), stop precedence > target on same-bar collisions (conservative), open positions close at last close (`exit_reason="end_of_data"`).

---

## 2. Target architecture (planned, not built)

This is the architecture we are building toward. Code will land in this shape.

### 2.1 Process model

Five separate processes, each with one job:

| Process | Job | Inputs | Outputs |
|---|---|---|---|
| `data_worker` | Ingest market data (WS + REST), persist bars/ticks. | Exchange WS/REST. | Parquet/SQLite bars; Redis last-tick cache. |
| `signal_worker` | Compute features, run strategy + LLM evaluators, write signals to queue. | Bars; news; features. | `signals` table rows; queue messages. |
| `executor` | The **only** process that talks to the broker. Consumes signals; risk-checks; places orders; reconciles. | Signals queue; broker API. | `orders` table; `positions` table; broker fills. |
| `api` | FastAPI dashboard + control surface. Read-only on state, control via signed actions. | HTTP. | UI + REST. |
| `monitor` | Heartbeats, drift detection (canary, P-vs-live parity), Telegram alerts. | All other processes' metrics + logs. | Alerts; daily report. |

This separation is non-negotiable (S-13). The dashboard must keep responding when signal compute spikes.

### 2.2 Data flow

```
Exchange WS ─► data_worker ─► bars (Parquet) ──┐
                                                ├─► signal_worker ─► signals (DB) ─► executor ─► broker
News API   ─► data_worker ─► news (DB)   ──────┘                                       │
                                                                                        ▼
                                                                                  decision_log (append-only)
                                                                                        │
                                                                                        ▼
                                                                                     monitor
```

### 2.3 Key invariants — these must hold at all times

| # | Invariant | Why | Enforcement |
|---|---|---|---|
| I-1 | Only `execution/` may **place orders** via broker SDKs. `data/` may use the same SDK read-only (public market-data endpoints, no auth). | Single point of order placement; impossible to bypass risk checks. Read-only data ingestion is fine because it cannot move money. | Lint rule + CI grep on private/auth methods (`create_order`, `cancel_order`, etc.) outside `execution/`. |
| I-2 | All features computed by `features/` — same code in train, backtest, live. | Train/serve skew = silent model failure (S-57). | Single import path; tests. |
| I-3 | Every order has an idempotent `client_order_id`. | Retries cannot double-fill. | Executor unit test. |
| I-4 | `LIVE_TRADING=true` AND human "go live" both required for real orders. | Two-key launch; nobody trips into live by accident. | Boot-time check + audit log. |
| I-5 | Risk caps in `risk/` enforced regardless of strategy output. | Strategy bugs cannot kill the account (P-15, P-31). | Executor calls `risk.check()` before every order. |
| I-6 | Decision log is append-only. | Audit + post-mortem. | DB constraint; no UPDATE/DELETE allowed. |
| I-7 | Backtests must use point-in-time data + symbol universe. | Survivorship + look-ahead = lies (P-04, P-05, P-29). | Backtest harness asserts as-of date on every fetch. |

### 2.4 Open architectural questions (to be resolved in `@design-doc.md`)

- **Stack language:** Python (default) vs TypeScript-Bun (à la `ritmex-ai-trader`).
- **Markets:** crypto-only first (CCXT), or crypto + equities (CCXT + Alpaca)?
- **LLM-orchestration framework:** Claude Agent SDK vs LangGraph vs custom thin layer.
- **Multi-agent depth:** start with single-LLM evaluator (S-33) and grow to TradingAgents-style debate, or start full-multi-agent?
- **Self-hosted vs ai4trade.ai-registered:** publish signals to AI-Trader from day one, or build standalone first?

These shape `agents/` vs `signals/` vs `strategies/` boundaries — answer before scaffolding.

---

## 3. Reading order for new contributors (incl. fresh Claude session)

1. `CLAUDE.md` — rules.
2. This file — architecture.
3. `@design-doc.md` — intent.
4. `implementation-plan.md` — next task.
5. `experiences.md` — pitfalls relevant to your task.
6. `knowledge.md` — domain reference.

---

*Last updated: 2026-04-25 — pre-code architecture sketch.*
