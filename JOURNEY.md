# 🐺 The Wolf Of Vibe Street — Journey

> One person, one Mac Mini, one night.
> Empty folder → running paper-trading bot in 26 sessions.
> Vibe-coded with [Claude Code](https://claude.com/claude-code).

This is the diary, not the spec. For technical detail see [`CLAUDE.md`](CLAUDE.md), [`memory-bank/@architecture.md`](memory-bank/@architecture.md), and [`memory-bank/@design-doc.md`](memory-bank/@design-doc.md).

---

## Session 0 — The setup

Stack: Python 3.12 + uv on a **Mac Mini M1, 8 GB RAM, macOS Tahoe**.
Goal: build an AI/agent-based crypto trading bot, paper-first, *eventually* trade real money in tiny size.

Three decisions that shaped everything:

- **Spot crypto only.** No futures, no leverage. Survive first.
- **Boring + alive > clever + dead.** A baseline strategy that loses honestly is better than a clever one that lies.
- **Decision log is the source of truth.** Every signal, every order, every block — written to an append-only SQLite log. The dashboard reads it. Nothing is hardcoded twice.

---

## Session 1–3 — Knowledge before code

Compiled `knowledge.md` (15 sections of domain knowledge from surveying 11 open-source AI trader repos), `experiences.md` (33 pitfalls + 58 success factors with citations), and a pre-code architecture sketch.

Key choices locked: Python + uv, CCXT for data, Backtrader path-compatible, SQLite + Parquet, Streamlit dashboard, Telegram alerts, Claude Agent SDK with provider abstraction.

No code yet. Just rules.

---

## Session 4–5 — The scaffold + CI

`uv init`, the folder skeleton from `CLAUDE.md` §5 (13 modules, every one importable), pytest + ruff configured, GitHub Actions running on every push. Hosting locked to Mac Mini through Phase 2.

13/13 smoke tests passing. No business logic yet, but the rails are down.

---

## Session 6 — Hosting decision

Mac Mini stays through Phases 1 + 2. Migrate to Hetzner CX22 (~€4/mo) only when Phase 3 begins (real money). The 24/7 prep (`pmset` config + `caffeinate` wrapper) deferred to right before the soak.

---

## Session 7 — First real code

`data/binance.py` — typed `fetch_ohlcv(symbol, timeframe, limit) -> list[Bar]`. Public REST, no auth. Verified live: pulled 3 BTC/USDT 1h bars from Binance. Real prices flowing in.

```
{'timestamp_ms': 1777136400000, 'open': 77397.27, 'high': 77407.03, 'close': 77245.77, ...}
```

8 tests including a network-error propagation case. The codebase had its first non-stub module.

---

## Session 8 — Backfill + Parquet

`data/backfill.py` paginates through history. `data/store.py` writes to `data/bars/{exchange}/{symbol}/{tf}.parquet`, idempotent (re-runs merge + dedup, don't duplicate). Verified: 30 days of BTC 1h pulled (720 bars, 34 KB on disk, byte-identical round trip).

11 new tests, 38/38 total.

---

## Session 9 — Features (causal)

`features/compute.py`: `bars_to_df`, `returns`, `ema`, `rsi` (Wilder), `atr` (Wilder), `volatility_regime`. Single source of truth — same code in train, backtest, live. Per `experiences.md` P-05: every feature must be causal (value at time `t` uses only data ≤ `t`).

The most important test in the suite was added here: a **lookahead guard** that perturbs future bars and asserts past feature values don't change. Parametrized over every public feature. If anyone ever adds a `.shift(-1)` by mistake, this test fails immediately.

---

## Session 10 — End-to-end pipe

This was the big one. `signals/types.py` + `strategies/baseline_ema_cross.py` + `risk/sizing.py` + `backtest/engine.py` + `backtest/metrics.py`. 24 new tests in one session.

The whole stack — data → features → strategy → risk → backtest — ran on the 30-day BTC parquet in **under 1 second**.

**First real backtest result:** 19 trades, **WR 26.3 %, total return −1.82 %** vs BTC buy-and-hold +12 %.

> S-50 (break-even win rate check) fired immediately on the very first run — actual WR 26 % < BE_WR 33 % for 2:1 R/R. The strategy has no edge.

This was a feature, not a bug. We built the pipe; the pipe surfaced honest negative numbers. Future ML/LLM layers must beat −1.82 % to earn their place.

---

## Session 11 — Risk caps

`risk/caps.py` — kill switch (env var or sentinel file), daily / weekly drawdown halts, max concurrent positions, max notional. 12 tests. Caps block entries only — exits are never blocked (a blocked exit would trap the account).

The caps don't yet plug into the executor; that comes in session 12.

---

## Session 12 — Executor + decision log

`memory/decision_log.py` (SQLite, UPDATE/DELETE blocked by triggers — I-6), `execution/broker.py` (Order/Fill/Position + Broker Protocol + idempotent client_order_id helper), `execution/ccxt_paper.py` (PaperBroker), `execution/runner.py` (Executor — bar-driven, single-position, long-only).

The bot can now run end-to-end with everything auditable. Live verify: 720 bars, 8 round-trip trades, **11 buys blocked by `risk_block(daily_drawdown_halt)`**, 755 decision rows.

The risk layer actually fired in production. That's confidence.

---

## Session 13 — Daily HW reset bug + parity proof

The 11 risk_blocks revealed a bug: daily high-water never reset at UTC dawn. A small early dip locked the executor out for the rest of the run.

Fix: track current UTC day (`ts // 86_400_000`) and ISO-week. On rollover, set HW to current equity.

After fix: **0 risk blocks**, 19 fills (full set), final equity **−1.91 %** vs the no-caps backtest's **−1.82 %**. The 0.09 pp gap is just commission/slippage rounding.

> **The executor matches the backtest engine** when caps don't bite. Two parallel code paths, same numbers. That consistency is the contract before turning on paper-soak.

---

## Session 14 — Live loop

`workers/live_loop.py::LiveLoop` — polls Binance every 30 s, detects newly-closed bars, persists, recomputes signals, feeds them to the executor. `run(max_iterations=None)` is the forever loop. Honors kill switch between iterations. Tick errors are caught + logged so a transient API hiccup doesn't kill the bot.

Live verify: pulled 9 closed BTC bars from real Binance, processed all through executor + log. Re-poll: 0 new bars (idempotent).

**The bot can now actually run.**

---

## Session 15 — Streamlit dashboard

First version of the dashboard. KPI cards, equity curve, trade history table, live log, kill switch toggle. Functional but plain.

`uv add streamlit`. 7 new tests for the pure summary functions (no Streamlit imports — the logic is testable in isolation).

---

## Session 16 — Telegram notifier

`tools/notifier.py` — Notifier Protocol, TelegramNotifier (silent no-op if creds missing), NoOpNotifier for tests. LiveLoop now sends 3 kinds of alerts: kill switch state change (edge-triggered, no spam), tick errors, hourly heartbeat.

Phase 1 Monitor checkbox done.

---

## Session 17 — Text report + bar-time fill bug

User asked "when can I actually see something?". Added `ui/report.py` — a CLI summary you run with `uv run python -m ui.report`. Same numbers as the dashboard, no server.

While testing it: discovered a bug. Fills used wall-clock time, so historical replays showed every trade timestamped to "now". Fix: `Broker.place()` accepts an optional `timestamp_ms`; Executor passes `bar.timestamp_ms`.

After re-replay: trade dates now show 2026-03-28 → 2026-04-24 correctly. 5 wins (all `target_hit`, ~$94 each), 14 losses split between `stop_hit` and `signal_exit`.

---

## Session 18 — CLI entry

`uv run python -m workers.live_loop` is now one command. `build_from_env()` reads `TRADERBOT_*` env vars; `main()` prints a startup banner; `KeyboardInterrupt` exits cleanly with a pointer to the log + dashboard.

Phase 1 in feature-complete shape.

---

## Session 19 — Phase 2 begins: LLM evaluator

The first AI layer. `agents/llm_evaluator.py` — `LLMEvaluator` Protocol, `ClaudeEvaluator` (real Anthropic API, defaults to `claude-opus-4-7`, system prompt cached, adaptive thinking, JSON output schema), `RuleBasedEvaluator` (deterministic stub for tests).

`strategies/llm_filtered.py` — wraps any base strategy. Routes every `buy` through the evaluator. Approved → passes through with the LLM's rationale appended. Rejected → demoted to `hold` with the rejection reason. Both end up in the decision log → post-mortem can see exactly which trades the LLM killed.

10 new tests with the rule-based evaluator (no API key needed for CI).

---

## Session 20 — Dashboard rebuild

User said the dashboard was too plain ("missig"). Shared 6 reference screenshots of real trading platforms. Rebuilt: dark Plotly theme, 5 KPI cards, equity curve with starting-cap reference line, hand-rolled HTML tables for trade history + positions + live log (Streamlit's defaults too plain), color-coded badges (STOP/TGT/EXIT) and log lines (BUY=green, SELL=red, BLOCK=yellow, SIG=blue).

`.streamlit/config.toml` for dark theme + gold accent + monospace font.

---

## Session 21 — Multi-symbol comparison

`backtest/compare.py`. Runs the baseline across N symbols, prints a side-by-side table, writes an interactive Plotly HTML report. Live result on 30 d × 1h:

| Symbol | Trades | WR | Strategy | Buy-and-hold | Sharpe |
|---|---|---|---|---|---|
| BTC/USDT | 19 | 26.3 % | **−1.82 %** | +12.06 % | −2.73 |
| **ETH/USDT** | 13 | **46.2 %** | **+2.22 %** | +11.73 % | **+3.27** |
| SOL/USDT | 12 | 16.7 % | −2.90 % | −1.16 % | −4.81 |

**ETH was the only symbol the strategy made money on.** This is exactly what S-25 (per-symbol expectancy + auto-blacklist) is meant to catch.

---

## Session 22 — Dashboard fixes (external review)

User shared an external review. Three real inconsistencies fixed:

- **Auto-refresh actually works now** (was just text in the header before — JS reload added).
- **Sidebar opens by default** so the kill switch isn't hidden.
- **Fees flow through to displayed P&L.** Executor writes `metadata={"fee": fill.fee}` on every fill; `trades_dataframe` and `equity_curve` parse + subtract. Dashboard now reads **−1.91 %** matching the live executor exactly (was −0.25 % gross before — a 1.66 pp lie).

---

## Session 23 — Tabs + dev-start.sh

`📊 Overview` + `🔬 Backtest compare` tabs. Compare tab takes symbols/days/timeframe widgets, runs in-process, embeds the Plotly figure inline. Status row in the header: last fill timestamp + active symbols.

`dev-start.sh` — one command starts the live loop in the background (`caffeinate -di`) + the dashboard in foreground. Ctrl+C cleans up both.

---

## Session 24 — Loop control from the dashboard

User: "ingen terminal någonsin, allt allt allt i dashboarden". Done.

`tools/loop_control.py` — start / stop / status / tail_log. PID file at `data/state/loop.pid`, stale PIDs auto-cleaned, subprocess detached so closing the dashboard doesn't kill the loop. PYTHONUNBUFFERED=1 so the log tail is real-time.

Sidebar gets a "Live loop" section with green/yellow status, primary Start button or Stop button. Overview gets a "Loop output" panel.

The user can now do everything in the browser.

---

## Session 25 — Soak health banner

User wanted to start a short overnight soak (~12 h) instead of waiting 7 days. Added a one-glance morning check.

5 health checks: bot process, kill switch, recent signals, tick errors, decision-log size. Each returns ok/warn/error. Green/yellow/red banner at the top of Overview: HEALTHY / ATTENTION / ISSUES.

User opens the dashboard at 09:00, sees one color, decides whether to dig.

---

## Session 26 — Telegram setup wizard + Reset for fresh soak

Two final niceties before the soak:

- **Reset button** in sidebar (with mandatory checkbox confirmation): stops the loop, moves the decision log to `data/decision_log/backups/`, leaves a fresh empty DB. Phase-1 replay data preserved as backup.
- **Telegram wizard** in sidebar: token + chat ID fields, "Send test" (immediate, no save), "Save to .env" (writes both keys, preserves all other lines). User can configure phone alerts without ever opening `.env`.

`tools/env_config.py` is narrow on purpose — it parses `KEY=value`, ignores comments + blanks, writes back preserving structure. 10 tests including "don't lose ANTHROPIC_API_KEY when setting TELEGRAM_BOT_TOKEN".

---

## Where it stands

```
✓ data layer            (binance, backfill, parquet store)
✓ features              (causal, lookahead-guarded)
✓ strategy              (baseline EMA-cross + LLM-filter wrapper)
✓ risk                  (sizing + caps + kill switch)
✓ backtest              (engine + multi-symbol compare)
✓ executor              (single-position, idempotent, audit-logged)
✓ decision log          (append-only, triggers, fee-aware)
✓ live loop             (REST polling, kill-switch-aware, PYTHONUNBUFFERED)
✓ dashboard             (rich, dark, tabbed, all controls in browser)
✓ monitor + alerts      (Telegram notifier + heartbeat)
✓ CLI + dev-start.sh    (one command runs everything)
```

**190 tests, all green.** Live-verified end-to-end against real Binance.

---

## What's next

Phase 2 still has open items: per-symbol expectancy auto-blacklist (S-25), an actual ML model trained on logged data, regime classifier, multi-strategy portfolio. None of them are blockers for paper-soak.

Phase 3 is real money — €200–500 calibration on Kraken (EU-licensed). Gated on 6+ months of clean live operation.

But honestly: the goal of this project was never to get rich. It was to **learn the craft of algorithmic trading by building something defensible**. If the system breaks even after costs and slippage in Phase 3, it's a win. If it makes money, even better.

Either way, the codebase is real, the lessons log is real, and the skill compounds.

---

*This whole project — knowledge gathering, design, scaffold, all 26 sessions — was built in one night with [Claude Code](https://claude.com/claude-code). The user is a non-coder. The model wrote the code. The user made every decision.*
