# traderbot

AI/agent-based crypto trading bot — paper-first, audit-logged.

**Read `CLAUDE.md` first.** Then `memory-bank/@architecture.md`, `memory-bank/@design-doc.md`, `memory-bank/implementation-plan.md`, `memory-bank/progress.md`.

## Setup

```bash
uv sync
cp .env.example .env  # then fill in (only needed for Telegram alerts in Phase 1)
uv run pytest
```

## Run

Three things you can run:

```bash
# 1. Live loop (paper mode) — polls Binance, processes new bars, writes decision log.
uv run python -m workers.live_loop

# 2. Dashboard — read the live log in a browser.
uv run streamlit run ui/dashboard.py

# 3. Text report — instant CLI summary of the decision log.
uv run python -m ui.report
```

The live loop is **paper only**. Real orders require `LIVE_TRADING=true` AND a separate go-live gate that does not exist yet (per I-4 in `memory-bank/@architecture.md`).

## Pause

```bash
touch data/state/KILL_SWITCH       # bot pauses, doesn't exit
rm   data/state/KILL_SWITCH        # bot resumes
```

The dashboard sidebar has the same toggle.

## Configure

Override defaults via env vars (see `.env.example`):

| Var | Default | Notes |
|---|---|---|
| `TRADERBOT_SYMBOL` | `BTC/USDT` | |
| `TRADERBOT_TIMEFRAME` | `1h` | |
| `TRADERBOT_INITIAL_CASH` | `10000` | USD |
| `TRADERBOT_RISK_PCT` | `0.005` | 0.5 % per trade, hard cap 1 % in `risk/sizing.py` |
| `TRADERBOT_POLL_INTERVAL_S` | `30` | how often to poll Binance |
| `TRADERBOT_HEARTBEAT_INTERVAL_S` | `3600` | telegram heartbeat cadence |
| `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID` | — | both required for telegram; missing → silent no-op |
