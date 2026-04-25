<div align="center">

# 🐺 Wolf Of Vibe Street

![Wolf Of Vibe Street](assets/banner.png)

**A disciplined, emotionless, paper-first AI/agent crypto trading bot.**
Built in one night, vibe-coded together with [Claude Code](https://claude.com/claude-code).

[![CI](https://github.com/dotsystemsdevs/wolf-of-vibe-street/actions/workflows/ci.yml/badge.svg)](https://github.com/dotsystemsdevs/wolf-of-vibe-street/actions/workflows/ci.yml)
[![tests](https://img.shields.io/badge/tests-190%20passing-brightgreen)]()
[![python](https://img.shields.io/badge/python-3.12%2B-blue)]()

</div>

---

## What this is

A real, working algorithmic trading bot that:

- **Pulls live market data** from Binance (CCXT, public REST).
- **Generates trading signals** with EMA crossover + ATR stops (the boring baseline).
- **Filters signals through Claude** as an LLM evaluator (S-33 hybrid pattern).
- **Sizes positions** with fixed-% risk + hard caps (kill switch, daily DD halt).
- **Executes paper orders** with realistic commission + slippage modeling.
- **Logs every decision** to an append-only SQLite audit trail.
- **Surfaces it all** in a dark-themed dashboard you start/stop from the browser.

Mantra (from `memory-bank/@design-doc.md`): *"Boring + alive > clever + dead."*

**Paper trading only.** Real orders require an explicit `LIVE_TRADING=true` flag *and* a session-time human gate that does not exist yet. Caution > cleverness.

---

## Quick start

One command:

```bash
./dev-start.sh
```

Opens <http://localhost:8501>. Click **Start loop** in the sidebar. Done — bot is running.

For the full setup (uv, Python 3.12+, optional Telegram alerts), see below.

---

## Architecture

```
Binance API ─► data/binance.py ─► Parquet ──┐
                                             ├─► features ─► strategies ─► signals
News (TBD)  ─► …                       ──────┘                       │
                                                                      ▼
                                              risk caps ──────► Executor ──────► PaperBroker
                                                                      │
                                                                      ▼
                                                          decision_log (SQLite, append-only)
                                                                      │
                                                  ┌───────────────────┼───────────────────┐
                                                  ▼                   ▼                   ▼
                                            dashboard           text report          Telegram alerts
```

Five separate concerns. The decision log is the source of truth. The dashboard reads it.
The live loop writes to it via the executor.

---

## Layout

```
traderbot/
├── data/binance.py          OHLCV fetcher (CCXT)
├── data/backfill.py         Paginated historical pulls
├── data/store.py            Parquet save/load
├── features/compute.py      EMA / RSI / ATR / vol regime — all causal
├── strategies/
│   ├── baseline_ema_cross.py    Trivial EMA crossover (the floor)
│   └── llm_filtered.py          Wraps any strategy with an LLM evaluator (S-33)
├── agents/llm_evaluator.py  Claude evaluator + RuleBased mock
├── signals/types.py         Signal dataclass (validates: buy → stop required)
├── risk/
│   ├── sizing.py            Fixed-% risk, hard cap 1%
│   └── caps.py              Kill switch + DD halts + max positions/notional
├── execution/
│   ├── broker.py            Order/Fill/Position + Broker Protocol
│   ├── ccxt_paper.py        PaperBroker (sim fills + slippage + fee)
│   └── runner.py            Executor — bar-driven, single-position
├── backtest/
│   ├── engine.py            Walk-forward, cost-aware
│   ├── metrics.py           Sharpe / Sortino / max DD / BE_WR
│   └── compare.py           Multi-symbol side-by-side
├── memory/decision_log.py   SQLite append-only (UPDATE/DELETE blocked by triggers)
├── workers/live_loop.py     Polls Binance → writes new bars → calls Executor
├── tools/notifier.py        Telegram + NoOp
├── tools/loop_control.py    Start/stop/status the loop subprocess
├── tools/env_config.py      .env reader/writer (preserves other lines)
├── ui/views.py              Pure summary functions (testable, no Streamlit)
├── ui/dashboard.py          Streamlit page
└── ui/report.py             Text-mode CLI summary
```

Read `CLAUDE.md` for operating rules, `memory-bank/@architecture.md` for invariants, `memory-bank/@design-doc.md` for what + why.

---

## What you can do — entirely from the browser

| Action | Where |
|---|---|
| Start / stop the bot | Sidebar → Live loop |
| Pause without stopping | Sidebar → Kill switch |
| See P&L, trades, positions | Overview tab |
| Watch loop output live | Overview → Loop output panel |
| Run a multi-symbol backtest | 🔬 Backtest compare tab |
| Configure Telegram alerts | Sidebar → 📱 Telegram alerts |
| Reset for a clean soak | Sidebar → ⚠ Reset for fresh soak |
| Soak status check | Top of Overview (green/yellow/red banner) |

---

## Setup (first time)

```bash
git clone https://github.com/dotsystemsdevs/wolf-of-vibe-street.git
cd wolf-of-vibe-street
uv sync
cp .env.example .env  # fill in TELEGRAM_BOT_TOKEN if you want alerts
uv run pytest
./dev-start.sh
```

Open <http://localhost:8501>.

### Optional config (env vars)

| Var | Default | Purpose |
|---|---|---|
| `TRADERBOT_SYMBOL` | `BTC/USDT` | What to trade |
| `TRADERBOT_TIMEFRAME` | `1h` | Bar size |
| `TRADERBOT_INITIAL_CASH` | `10000` | USD |
| `TRADERBOT_RISK_PCT` | `0.005` | 0.5 % per trade (cap 1 %) |
| `TRADERBOT_POLL_INTERVAL_S` | `30` | Binance polling cadence |
| `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID` | — | Both required for Telegram alerts |
| `ANTHROPIC_API_KEY` | — | For Claude LLM evaluator (Phase 2) |

### Pause / kill switch

```bash
touch data/state/KILL_SWITCH       # bot pauses (doesn't exit)
rm   data/state/KILL_SWITCH        # bot resumes
```

Or use the sidebar toggle.

---

## Tech stack

- **Python 3.12+** with `uv` for package management
- **CCXT** for exchange APIs (Binance public)
- **pandas + pyarrow** for data + Parquet
- **SQLite** for the decision log (append-only via triggers)
- **Streamlit + Plotly** for the dashboard
- **Anthropic SDK** for the Claude evaluator
- **pytest** + **ruff** + GitHub Actions CI

---

## The journey

This bot was built in **one night, ~26 sessions**, from an empty folder to a running paper-trading system. See [JOURNEY.md](JOURNEY.md) for the day-by-day diary.

---

## License

MIT — do whatever you want, but don't blame me if the bot loses (paper) money.

**It is not financial advice. It is paper trading. Caution > cleverness.**
