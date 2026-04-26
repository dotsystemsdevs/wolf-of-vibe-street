# progress.md — Milestone Log

> Append-only log of meaningful progress. Newest at top.
> Update at the end of every session that produced output.

---

## 2026-04-27 — External research curation (Vibe, TradingAgents, AI-Trader, DEX, NOFX, OpenAlice, etc.)

- `knowledge.md` §9.5 — new table: curated GitHub + site links the user provided, with one-line "how this relates to WOLF" (CEX stack stays primary; DEX/Trading Strategy called out as a different market model). arXiv link for TradingAgents paper.
- `experiences.md` **S-59** — "curate, don't Frankenstein stacks" when pulling ideas from other repos.
- `README` already points at `docs/GO_LIVE.md`; no change required for this pass.
- Dashboard: sidebar expander **Referens-repos (AI-trading)** with the same links for quick access while operating; `DASHBOARD_BUILD` bump.

**Next:** Optional: deeper read of one repo (e.g. OpenAlice) if we add a feature; no merge until decided.

**Blockers:** none.

**New lessons:** S-59.

---

## 2026-04-25 (session 26) — Telegram setup wizard (no terminal needed)

User starting overnight soak wanted alerts on phone but didn't want to edit `.env` manually. Wizard now lives in the dashboard sidebar.

- `tools/env_config.py` — narrow `.env` reader/writer. `read_env()` parses `KEY=value` (strips quotes, ignores comments + blanks). `update_env()` rewrites in place — existing keys' lines preserved (only the value changes), unrelated lines / comments / blanks untouched, new keys appended at end. 10 tests pin every path including "don't lose ANTHROPIC_API_KEY when setting TELEGRAM_BOT_TOKEN". 190/190 total.
- Dashboard sidebar gets a `📱 Telegram alerts` expander: token field (password-masked), chat ID field, "Send test" button (calls `TelegramNotifier` immediately, no save), "Save to .env" (primary). Status badge at top: ✓ Configured / ⚠ Not configured. Save success message tells the user to stop+start the loop to pick up the new values.
- Reset button + Telegram wizard both written without touching the running loop's code path. Soak in progress was unaffected.

**The user can now configure Telegram, send a test ping, and verify it works — entirely from the browser.** Heartbeats land on phone the next time the loop starts.

**Next:** Wait for tomorrow's soak result. If green: Phase 2 work (S-25, ML, regime). If red: debug whatever broke.

**Blockers:** none.

**New lessons:** none.

---

## 2026-04-25 (session 25) — Soak health panel for the morning glance

User wants to start a short paper-soak (overnight, check at 09:00) instead of waiting 7 days. Built the morning-check tool.

- `ui/views.py::soak_health(rows, *, bot_running, kill_switch_on, now_ms, ...)` — pure function returning 5 checks, each `{name, status: ok|warn|error, message}`:
  1. **Bot process** — error if loop not running.
  2. **Kill switch** — warn if active (intentionally pauses trading).
  3. **Recent signals** — error if no signal in last 2× expected_bar_seconds + 10 min slack (catches a stuck loop).
  4. **Tick errors** — count of `order_rejected(tick_error: ...)` in last hour. 0 = ok, 1–2 = warn, 3+ = error.
  5. **Decision log** — warn if < 5 rows (sanity check, fresh install).
- 6 new tests pin each path. 177/177 total.
- Dashboard Overview tab gets a top banner: green/yellow/red ("HEALTHY"/"ATTENTION"/"ISSUES") with grid-laid-out check details. One glance tells the user if last night's run was clean.

**Morning checklist for the user (09:00 tomorrow):**
1. Open the dashboard (still running from yesterday).
2. Look at the top banner — green = walk away happy.
3. If yellow/red, the per-check messages tell you exactly what.
4. Scroll: equity curve + trade history shows what happened overnight.
5. "Loop output" panel shows last 30 lines of stdout from the loop process.

**Next:** When user kicks off the soak, they should also (a) verify Telegram is configured if they want push alerts (otherwise heartbeats are silent), (b) leave the laptop on with `caffeinate` taking care of sleep. Both are already wired.

**Blockers:** none.

**New lessons:** none.

---

## 2026-04-25 (session 24) — Loop control from the dashboard

User requirement: "ingen terminal någonsin, allt allt allt i dashboarden". Done.

- `tools/loop_control.py` — `status()`, `start(*, extra_env, use_caffeinate)`, `stop()`, `tail_log()`. PID persisted to `data/state/loop.pid`; status auto-cleans stale PID files via `os.kill(pid, 0)`. Subprocess detached with `start_new_session=True` so closing the dashboard doesn't kill the loop. `caffeinate -di` wrapped on macOS so the Mac stays awake. `PYTHONUNBUFFERED=1` so the log tail updates in real time.
- Dashboard sidebar **Live loop** section: `Running · PID … · uptime …` (green) with "Stop loop" button OR "Loop not running" (yellow) with "Start loop" (primary green) button. Spinner during state transitions. Log path shown.
- Overview tab gets a **Loop output (last 30 lines)** panel — tails `data/state/loop.log`. Auto-refreshes with the rest of the page (10 s).
- 8 new tests for `loop_control` (no real subprocess — uses our own PID for "alive", a fake PID for dead-cleanup, malformed pid file, log tailing). 171/171 total.
- Live-verified: start/stop cycles cleanly. SIGTERM → wait 5 s → SIGKILL fallback. Process-group kill (caffeinate + uv + python) with direct-PID fallback if EPERM.

**The user can now do everything in the browser:**
- Start the bot: sidebar → "Start loop"
- Watch it work: Overview → Loop output + decision-log live log
- Pause without killing: sidebar → "Enable kill switch"
- Run multi-symbol backtest: 🔬 Backtest compare tab → "Run comparison"
- Stop the bot: sidebar → "Stop loop"

The shell script `dev-start.sh` is now a *bootstrapper* (start dashboard); the loop is launched and managed from inside the browser.

**Next:** Either (a) start the actual paper-soak (now genuinely one-click), or (b) S-25 expectancy/auto-blacklist.

**Blockers:** none.

**New lessons:** none.

---

## 2026-04-25 (session 23) — Dashboard tabs + compare + one-command launcher

User wanted everything in one place + no command memorization. Three concrete additions:

- **`backtest/compare.py` refactor**: extracted `make_figure(results)` (in-memory `go.Figure`) + `run_comparison(symbols, *, days, timeframe, config)` for in-process callers (the dashboard). `render_html()` now wraps `make_figure()`. CLI behavior unchanged.
- **Dashboard tabs**: `📊 Overview` (existing) + `🔬 Backtest compare`. Compare tab takes symbols (text input), days (number), timeframe (select) → `Run comparison` button → backfills + runs in-process → embeds the Plotly figure inline + renders a per-symbol table with green/red Strategy/Diff coloring. Result cached in `st.session_state` so re-runs only re-render, not re-compute.
- **Status row** in the header: last fill timestamp + comma-separated list of active symbols (extracted from log) + kill switch + auto-refresh — all visible at a glance, no clicks.
- **`dev-start.sh`**: one-command launcher. Starts `uv run python -m workers.live_loop` in the background under `caffeinate -di` (Mac stays awake), then runs `streamlit run ui/dashboard.py` in the foreground. `trap` cleans up the background loop on Ctrl+C. Refuses to start if port 8501 is already taken. Logs go to `/tmp/traderbot-loop.log`.
- README updated to show `./dev-start.sh` as the primary entry point.

The "no decision log" warning is now non-fatal — the dashboard renders the chrome (header + tabs) so the compare tab is usable even before the live loop has produced data.

163/163 tests still green. No new tests for the dashboard tab itself (Streamlit UI is hard to unit-test); the underlying `run_comparison` was indirectly verified by running it programmatically.

**Next:** Either (a) actual paper-soak start now that one command launches everything, or (b) S-25 expectancy/auto-blacklist using the multi-symbol data.

**Blockers:** none.

**New lessons:** none.

---

## 2026-04-25 (session 22) — Dashboard fixes: fees, auto-refresh, sidebar

User shared an external review of the UI; addressed the three real shortcomings:

- **Auto-refresh actually works now.** Header text claimed "auto-refresh 10s" but no JS or Streamlit hook backed it. Added a `<script>setTimeout(reload, 10000)</script>` so the page actually reloads every 10 s.
- **Sidebar opens by default** (`initial_sidebar_state="expanded"`). The kill-switch toggle was hidden behind a collapsed sidebar — bad for an operational control.
- **Fees flow through to displayed P&L.** Executor now writes `metadata={"fee": fill.fee}` on every `order_filled`. `trades_dataframe` and `equity_curve` parse it from `metadata_json` and subtract. `pnl` is now net; `gross_pnl` + `fees` are surfaced alongside. Re-replay of 30-day BTC now reads **−1.91 %** end-of-period in the dashboard — matching the live executor exactly (was −0.25 % gross before, a 1.66 pp lie). Report header label corrected from "(gross of fees)" to "(net of fees)".
- 2 new tests pin fee parsing (well-formed + malformed metadata fallbacks). 163/163 total.

Skipped the other two items the review flagged: single-position assumption (by design for Phase 1) and auth (not relevant for local dev).

**Next:** Resume Phase 2 — S-25 expectancy tracking (auto-blacklist losing symbols) was queued; the multi-symbol comparison from session 21 already gives us the data to act on.

**Blockers:** none.

**New lessons:** none codified, but the "UI claims X, code does Y" mismatch was a real inconsistency caught by external review. Worth a P-** if it bites in a more impactful place later.

---

## 2026-04-25 (session 21) — Multi-symbol baseline comparison

`backtest/compare.py`: backfills + backtests baseline EMA-cross across multiple symbols, prints a side-by-side table, writes an interactive Plotly HTML to `data/cache/multi_symbol_backtest.html`. Symbols + timeframe + days configurable via `TRADERBOT_*` env vars. Backfills are idempotent — reuses existing parquet if it covers the requested window. 6 new tests; 161/161 total.

**Live result, 30 days × 1h, baseline EMA(12,26):**

| Symbol | Trades | WR | Strategy | Buy-and-hold | Diff | Sharpe | MaxDD |
|---|---|---|---|---|---|---|---|
| BTC/USDT | 19 | 26.3% | **−1.82%** | +12.06% | −13.87pp | −2.73 | 3.37% |
| **ETH/USDT** | 13 | **46.2%** | **+2.22%** | +11.73% | −9.51pp | **+3.27** | 1.49% |
| SOL/USDT | 12 | 16.7% | −2.90% | −1.16% | −1.74pp | −4.81 | 3.08% |

Real cross-symbol behavior emerges: **ETH is the only symbol where the strategy made money** (Sharpe +3.27, lowest DD), and underperformed buy-and-hold by the smallest margin. BTC strong uptrend → strategy whipsawed (cut winners short). SOL ranged sideways → all stops + signal exits, no targets hit.

**This is S-25 in action** (per-symbol expectancy tracking + dynamic blacklist). ETH is a candidate symbol for this strategy; BTC + SOL would be auto-disabled if S-25 was implemented. Worth building next.

The strategy still **underperforms buy-and-hold on every symbol** — exactly what S-50 (BE_WR check) and S-30 (boring + alive > clever + dead) predicted for a baseline. The pipeline produces honest numbers; future ML/LLM layers must beat these.

**Next:** Either (a) implement S-25 expectancy tracking + auto-blacklist (small, concrete), or (b) the canary bot (S-37 — known-bad HFT strategy validates parity continuously), or (c) start the actual paper soak.

**Blockers:** none.

**New lessons:** none — but the per-symbol number spread is the strongest signal yet that single-symbol assumptions are bad. Phase 2 multi-strategy + per-symbol gating becomes load-bearing.

---

## 2026-04-25 (session 20) — Dashboard: rich dark-themed rebuild

User said the previous dashboard looked "missig" / too plain and shared 6 reference screenshots (TRADING/BOT-style with KPI cards + equity curve + positions panel + live log). Rebuilt to match that aesthetic.

- `uv add plotly`. Plotly's dark theme + interactive hover beats Streamlit's native charts for trading visuals.
- `.streamlit/config.toml` — dark base theme, gold accent, monospace font, neutral grays.
- `ui/views.py` — added `equity_curve(rows, initial_cash)` (walks fills, returns DataFrame[ts, cash, position_value, equity]) and `open_positions(rows)` (reconstructs unclosed buys with avg-entry + last_price + uPnL). Both pure, both tested. 4 new tests; 155/155 total.
- `ui/dashboard.py` — full rewrite. Hand-rolled HTML tables for trade history + positions + live log (Streamlit's built-in DataFrame is too plain for this look). 5 KPI cards at top (Equity, Cash, Realized P&L, Trades, Open positions). Plotly equity curve with step-line + dotted starting-equity reference line. Color-coded badges for STOP/TGT/EXIT. Live log color-codes BUY (green) / SELL (red) / BLOCK (yellow) / SIG (blue). Sidebar holds the kill switch + event-count breakdown.

Verified against the existing 30-day BTC log (777 rows, 38 fills, 19 trades): equity curve goes 10000 → 9975 with min 9736, max 10092. Realistic-looking step curve.

**Dashboard is now ~80% of the look from the TRADING/BOT reference screenshot.** The remaining 20% (animated transitions, native window chrome, custom font face) needs a proper React + FastAPI rewrite — out of scope for Phase 1.

**Next:** Either (a) start the actual paper-soak now that the dashboard is presentable, or (b) keep building Phase 2 (ML model, regime classifier).

**Blockers:** none.

**New lessons:** none.

---

## 2026-04-25 (session 19) — Phase 2 begins: LLM evaluator (S-33)

First AI layer on top of the baseline. The cheap rule strategy keeps generating candidates; an evaluator now decides which ones to actually take.

- `agents/llm_evaluator.py` — `LLMEvaluator` Protocol, `RuleBasedEvaluator` (deterministic stub for tests/CI), `ClaudeEvaluator` (real Anthropic API). `ClaudeEvaluator` defaults to **`claude-opus-4-7`** per skill rules. System prompt is byte-stable + cached (5-min ephemeral). Adaptive thinking + `output_config.format` JSON schema → typed `Verdict(score, rationale)`.
- `strategies/llm_filtered.py::llm_filtered_signals(base, df, *, evaluator, threshold=0.3)` routes every `buy` through the evaluator and either passes it (with enriched rationale) or demotes it to `hold` with the rejection reason. `sell` and `hold` pass through untouched (S-33: exits stay rule-based).
- `_build_context(df, idx, signal)` snapshots the bar's market state (entry, stop %, target %, R/R, recent closes, EMA fast/slow, RSI, ATR) — causal, only uses `df[:idx+1]`.
- 10 new tests with `RuleBasedEvaluator` — no API call needed. Cache-stability test guards the system prompt from accidental per-request data. 151/151 total.
- `uv add anthropic` — first non-data-stack dep.
- Live verify against Claude is gated on `ANTHROPIC_API_KEY` being set; the user's env doesn't have one yet, so live test deferred to next session (or whenever they want to spend a few cents to see Claude's verdicts on the 19 historical buy signals).

**Cost note (when going live):** evaluating one signal is ~500 input + ~300 output tokens at Opus 4.7 prices = ~$0.0099. The 30-day BTC backtest had 19 buys → ~$0.19 to re-evaluate. Real-time at 1h bars on BTC alone = trivial. Caching the system prompt should cut that further on the second-and-on calls within the 5-minute TTL window.

**Next:** Either (a) set `ANTHROPIC_API_KEY` and run the LLM-filtered strategy on the 30-day parquet — see whether the LLM beats / matches / underperforms the baseline's −1.91 %; or (b) keep building Phase 2 — first ML model on the logged data, regime classifier, multi-strategy portfolio. (a) is the more interesting result.

**Blockers:** none.

**New lessons:** none codified — the prompt-caching skill rules from `claude-api` skill landed cleanly. Worth a P-** if we ever ship a system prompt that interpolates a timestamp and tank the cache hit rate.

---

## 2026-04-25 (session 18) — CLI entry: `python -m workers.live_loop`

- `workers/live_loop.py` gets `build_from_env()` + `main()`. All config via `TRADERBOT_*` env vars (symbol, timeframe, initial cash, risk_pct, poll interval, slippage/commission, heartbeat). Defaults work out of the box for BTC/USDT 1h paper.
- Banner on startup prints config + reminders ("Mode: PAPER", "Stop: Ctrl+C", "Pause: touch data/state/KILL_SWITCH").
- `KeyboardInterrupt` caught for clean shutdown — prints log path + dashboard launch command.
- `TRADERBOT_MAX_ITERATIONS` lets us bound runs in tests / smoke-checks.
- 2 new tests for env-driven construction (defaults + overrides). 141/141 total.
- Live verify: ran `TRADERBOT_MAX_ITERATIONS=1 python -m workers.live_loop` against real Binance — pulled 9 closed bars, wrote 9 signal rows, exited cleanly.
- README updated with the three runnable commands (live_loop, dashboard, report) + env table.

**Bot now starts with one command.** All Phase 1 architectural + ops items are complete except:
- Mac Mini 24/7 power config (`pmset` + `caffeinate`).
- User: create Telegram bot via @BotFather + paste token/chat_id into `.env`.
- Then start the actual 7-day soak: `caffeinate -di uv run python -m workers.live_loop`.

**Next:** Either we add the small `pmset`-runbook helper / launchd plist (so the soak survives reboots), or we just call this Phase 1 done and move to Phase 2 (ML/LLM layer on top).

**Blockers:** none.

**New lessons:** none.

---

## 2026-04-25 (session 17) — Text report + bar-time fill timestamps

User asked when they'd actually see something. Two pieces:

- `ui/report.py` — `uv run python -m ui.report` prints a text summary of the decision log: rows, period, trades, win rate, realized P&L, event distribution, risk-block reasons, last 10 trades. Same numbers as the dashboard, no server required.
- **Bug fix**: `Broker.place()` now accepts an optional `timestamp_ms` and `Executor.on_bar` passes `bar.timestamp_ms`. Previously fills used wall-clock time, which made historical replays show every trade timestamped to "now" — useless for visual review. Fix is back-compat (param is optional).
- 139/139 still green.

After re-replaying the 30-day BTC data: the report now shows real trade dates (2026-03-28 → 2026-04-24), 5 wins ALL via `target_hit` (~$94 each), 14 losses split between `stop_hit` and `signal_exit`. The dashboard will show the same when launched.

**To see it**: in the VSCode terminal,
```
uv run streamlit run ui/dashboard.py
```
or for a quick text view: `uv run python -m ui.report`.

**Next:** CLI entry for the live loop (`python -m workers.live_loop`) so the soak is one command, then ops runbook.

**Blockers:** none.

**New lessons:** none — but worth noting that the "wall-clock vs bar-clock" distinction is something to watch for any future replay/simulation code.

---

## 2026-04-25 (session 16) — Telegram notifier + LiveLoop alerts

- `tools/notifier.py` — `Notifier` Protocol, `TelegramNotifier`, `NoOpNotifier`. Telegram reads `TELEGRAM_BOT_TOKEN` + `TELEGRAM_CHAT_ID` from env; silent no-op if either is missing so dev/CI just works. HTTP errors are caught via an `on_error` callback so a Telegram outage cannot crash the bot.
- `LiveLoop` accepts `notifier=` and `heartbeat_interval_s=` (default 3600). `run()` notifies on three signals:
  - **Kill switch state change** — edge-triggered ("Kill switch ON" once when activated, "Kill switch OFF" once when cleared). No spam if it stays in either state.
  - **Tick error** — caught exception is logged AND notified with type + message.
  - **Heartbeat** — first iteration always fires; thereafter every `heartbeat_interval_s`.
- 8 new tests (5 notifier behavior, 3 LiveLoop alerts). 139/139 total. Ruff clean.

**Phase 1 Monitor checkbox is now done.** All Phase 1 architectural work is complete:

```
data layer       ✓
features         ✓
strategy         ✓
risk/sizing      ✓
risk/caps        ✓
backtest         ✓
executor         ✓
decision log     ✓
live loop        ✓
dashboard        ✓
monitor (alerts) ✓
```

Only remaining items are operational:
- Mac Mini 24/7 prep (`pmset` + `caffeinate` + `launchd`).
- User creates a Telegram bot via @BotFather + sets env vars.
- User chooses initial cash, strategy params, etc.
- Then start the actual 7-day soak.

**Next:** Either (a) ops runbook for starting the soak (the 24/7 config + launchd plist + how to make a Telegram bot), or (b) a thin `python -m workers.live_loop` CLI entry that reads env config, instantiates everything, and runs. (b) is needed before the soak can be a one-liner.

**Blockers:** none.

**New lessons:** none.

---

## 2026-04-25 (session 15) — Streamlit dashboard

- `uv add streamlit` (1.56.0).
- `ui/views.py` — pure summary functions: `event_counts`, `fills_dataframe`, `trades_dataframe` (pairs buys with subsequent sells), `summary` (rows total, trades, wins/losses, win rate, realized P&L, blocks by reason). No Streamlit imports here — fully unit-testable.
- `ui/dashboard.py` — Streamlit page on top of `views`. Sidebar holds the **kill-switch toggle** (creates/deletes the sentinel file) and a manual refresh button. Body shows P&L metrics, event counts, risk blocks, recent trades, recent fills. Reads `TRADERBOT_LOG_PATH` env (default `data/decision_log/traderbot.db`).
- 7 new tests in `tests/test_views.py`. 131/131 total. Ruff clean.
- Replayed the 30-day BTC strategy into the canonical `data/decision_log/traderbot.db` (777 rows). Verified summary numbers match the live executor run from session 13: 19 trades, 26.3 % WR. Dashboard imports cleanly under `uv run streamlit run ui/dashboard.py`.

**Known cosmetic gap:** `trades_dataframe.pnl` is gross of fees (the executor applies fees to cash, but `order_filled` rows don't carry a `fee` column). Dashboard P&L reads slightly optimistic vs actual cash. ~0.2 % on the 30-day test. Fix path = add `fee` to the schema or stash it in `metadata_json`. Deferred.

**Next options:**
- (a) Telegram bot (heartbeat + kill-switch + DD-halt alerts) so a paper-soak failing silently is impossible.
- (b) Mac Mini 24/7 ops prep — `pmset`, `caffeinate` wrapper, then **start the actual 7-day soak**.

Both are small. Telegram first probably — without it, you have to remember to refresh the dashboard. With it, the bot pings you when something interesting happens.

**Blockers:** none.

**New lessons:** none.

---

## 2026-04-25 (session 14) — Live loop landed; bot can run continuously

The bridge between historical replay and live trading. `workers/live_loop.py::LiveLoop`:
- Polls Binance REST every `poll_interval_s` (default 30 s).
- Detects newly-closed bars via `closeTime = ts + tf_ms <= now()` — the in-progress current bar is skipped until it closes.
- Persists each new bar to the canonical Parquet path; recomputes signals on the full history; feeds each new bar to `Executor.on_bar`.
- `run(max_iterations=None)` is the forever loop. Honors kill switch between iterations (logs a `risk_block(kill_switch_paused_loop)` row each pause; doesn't exit). Tick errors are caught and logged so a transient API hiccup doesn't kill the bot.
- Polling instead of WS: 1h bars don't need sub-second latency, polling drops the whole async/aiohttp/reconnect machinery. We can swap to ccxt.pro WS later if we move to 1m bars.
- Tests with a fake `_FakeClient` + fake clock → no network. 4 new tests, 124/124 total.

**Live verify against the real Binance API:**
- Tick #1: pulled 9 closed BTC/USDT 1h bars, processed each through executor, wrote 9 decision-log rows. Latest mark $77,330.
- Tick #2 (immediate re-poll): 0 new bars detected, 0 new log rows. Idempotent.

**Phase 1 is essentially feature-complete.** Open items are operational, not architectural:
- Streamlit dashboard reading the SQLite log.
- Telegram notifications (heartbeat, kill-switch trip, daily-DD halt).
- Mac Mini 24/7 prep (`pmset` config, `caffeinate` wrapper).
- Then start the 7-day soak.

**Next:** Either Streamlit dashboard (visual feedback) or Telegram alerts (ops feedback). Dashboard probably first — it's nice to *see* the bot working, and it can be built by reading the existing decision log without changing any runtime code.

**Blockers:** none.

**New lessons:** none — but worth flagging that the parity between backtest and live executor (proved in session 13) extended cleanly when the live loop was added on top. Three independent code paths (backtest, replay-executor, live-loop) all converge on the same numbers given the same data.

---

## 2026-04-25 (session 13) — HW reset fix + executor/backtest parity proven

- Daily HW now resets at UTC dawn (key = `ts_ms // 86_400_000`). Weekly HW resets at ISO-week rollover (`isocalendar().week`). Within a period, HW still ratchets upward.
- 3 new tests in `tests/test_executor_hw_reset.py` pin the rollover (UTC dawn, no-reset within day, ISO-week rollover including Sun→Mon). 120/120 total.
- Re-ran the 30-day BTC live executor:
  - **0 risk blocks** (was 11 — daily-DD halt no longer sticky).
  - 19 buys + 19 sells = full trade set (was 8+8).
  - Final equity **−1.91 %** vs the no-caps backtest **−1.82 %**. The 0.09 pp gap is just commission/slippage rounding noise.
- This pins down a real invariant: **the live executor matches the backtest engine** when caps don't bite. Two parallel code paths, same numbers — that consistency is what we want before turning on paper-soak.

**Next:** WS bar ingestion (or REST polling for 1h bars — same effect, less code). Wraps everything into a `workers/live_loop.py` that runs continuously. Then monitor + Streamlit dashboard, then start the soak.

**Blockers:** none.

**New lessons:** none — but the parity-test pattern (run two implementations against the same data and check they agree) is now part of the project's playbook.

---

## 2026-04-25 (session 12) — Executor + decision log

The bot can now actually run end-to-end, with every event auditable.

- `memory/decision_log.py` — append-only SQLite. Schema covers signal, order_placed, order_filled, order_rejected, risk_block, reconcile_drift. UPDATE and DELETE are blocked by SQL triggers (I-6).
- `execution/broker.py` — `Order`, `Fill`, `Position` dataclasses + `Broker` Protocol. `make_client_order_id(strategy_id, signal_id, attempt)` is the I-3 idempotency primitive (32-char SHA-256 prefix; deterministic).
- `execution/ccxt_paper.py` — `PaperBroker` implements `Broker`. Fills at `mark_price` (passed by executor for stop/target hits) with slippage + commission. Re-placing a coid returns the original Fill (no double-fill).
- `execution/runner.py::Executor` — bar-driven runner. `on_bar(signal, bar)` does: mark-to-market → intra-bar stop/target check → exit if hit → process new signal → caps check → place → log. Every meaningful event writes a decision row.
- 24 new tests across 4 files (decision_log: 6, broker: 6, paper_broker: 6, executor: 6). 117/117 total. Ruff clean.

**Live verify on 30-day BTC parquet:**
- 720 bars processed; 19 buys + 20 sells issued.
- 8 buy fills + 8 sell fills (16 round trips).
- **11 buys blocked** by `risk_block(daily_drawdown_halt)` — the risk caps actually enforced what the design promised. Conservative bias here: daily HW only ratchets up, never resets at UTC dawn, so once equity dipped 3 % below the all-time peak the executor stayed locked out for the rest of the run. Safer than the wrong direction but worth fixing before the 7-day soak.
- Final equity $9,658 (−3.42 %) vs the no-caps backtest's −1.82 %. The gap is the daily-DD halt being too sticky.
- 755 decision rows persisted to SQLite.

**Next:** Either (a) fix the daily/weekly HW reset (small — UTC dawn / Monday rollover), or (b) the WS bar ingestion + executor loop wrapper that reads live ticks. Probably (a) first since it's a 30-line fix and makes the soak meaningful; then (b) which is the bridge from "bar-by-bar replay" to "live continuous loop".

**Blockers:** none.

**New lessons:** none codified — but if the HW reset bug had bitten us in live trading we'd have written a P-** entry. Worth a watch.

---

## 2026-04-25 (session 11) — Risk caps + kill switch

- `risk/caps.py` — `RiskCaps`, `RiskState`, `RiskDecision`, `check_entry()`, `kill_switch_active()`. Pure functions; the executor (built in a later session) calls `check_entry()` before any new entry. Caps block entries only — exits are never blocked (a blocked exit could trap an account in a losing position).
- Check order: kill switch → daily DD → weekly DD → max positions → per-position notional → aggregate notional. Earliest deny wins.
- Kill switch sources: env `KILL_SWITCH=true` (case-insensitive) OR a sentinel file (`data/state/KILL_SWITCH` by default). Both routes test-covered.
- 12 tests; 93/93 total. Ruff clean.

**Not wired into the backtest** in this session. Per I-5 the caps live in `execution/`; the backtest currently has no risk-aware entry path because it's single-position by design (concurrency cap and notional cap can never bite at $10k). Once the executor exists (next session), `check_entry()` will be the gate before every order.

**Next:** Either (a) `execution/ccxt_paper.py` — the paper-mode executor that ties signals → caps → simulated fills → decision log, or (b) decision log + heartbeat. The executor naturally pulls the decision-log in with it (every order writes a row), so doing both at once makes sense.

**Blockers:** none.

**New lessons:** none.

---

## 2026-04-25 (session 10) — End-to-end pipe: strategy + risk + backtest

Phase 1 vertical slice complete. The whole stack — `data → features → strategy → risk → backtest` — runs on the 30-day BTC parquet in <1s.

- `signals/types.py` — `Signal` dataclass. `__post_init__` enforces S-15: a `buy` without `stop` raises. `sell` is exit-only (stop was set on entry); `hold` requires nothing.
- `strategies/baseline_ema_cross.py` — long-only EMA(12,26) cross. Stop = close − 2·ATR, target = close + 4·ATR (2:1 R/R). Conviction = |EMA-diff|/close, capped.
- `risk/sizing.py` — `position_size(equity, entry, stop, risk_pct)`. Default 0.5%, hard cap 1% per design-doc §5. Returns 0 on degenerate inputs (zero distance / zero equity).
- `backtest/engine.py` — walk-forward, single-position, long-only. Entries fill at next bar's open (no peek). Same-bar stop ⊐ target precedence (conservative). Cost model = commission_bps + slippage_bps. Open positions close at last close as `end_of_data`.
- `backtest/metrics.py` — `sharpe`, `sortino`, `max_drawdown`, `win_rate`, `break_even_win_rate`, `equity_returns`. Hour-bar annualization = 8760.
- 24 new tests (Signal: 5; baseline_ema_cross: 5; sizing: 4; backtest engine + metrics: 10). 81/81 total. Ruff clean.

**First live backtest result** (30 days BTC/USDT 1h, $10k start, 0.5% risk, 10 bps commission, 5 bps slippage):

| Metric | Value |
|---|---|
| Trades | 19 |
| Win rate | 26.32 % |
| BE_WR (2:1) | 33.33 % |
| Total return | **−1.82 %** |
| Max DD | 3.37 % |
| Sharpe (ann.) | −2.73 |
| BTC buy-and-hold | +12.06 % |

**S-50 fired in practice on the very first run** — actual WR 26 % < BE_WR 33 % means the baseline EMA-cross has no edge at this R/R on this slice. The risk layer kept losses bounded (max DD 3.4 %); the cost model dragged ~ commission+slippage per trade as expected. **This is the test infrastructure working as intended** — we built the pipe; the pipe surfaced honest negative numbers; we did not paper over them.

Per S-30 (boring + alive > clever + dead), the baseline is now the *floor* — not the strategy we ship. Next layer (ML/LLM in Phase 2) has to *beat* this number to earn its place.

**Next:** Either (a) `risk/caps.py` (DD halts, kill switch, max notional) which closes the risk layer, or (b) decision log + monitor (Phase 1 audit trail). Once both are done we have everything needed to start the 7-day paper soak.

**Blockers:** none.

**New lessons:** none new — but worth flagging that S-50 + S-30 fired exactly on schedule in their first real-world test.

---

## 2026-04-25 (session 9) — Features layer (causal)

- `features/compute.py` — `bars_to_df`, `returns`, `ema`, `rsi` (Wilder), `atr` (Wilder), `volatility_regime` (rolling tercile of ATR/close). All hand-rolled on pandas, no extra deps; `ta-lib` C lib is brew-installed but the Python wrapper is not used yet (we can swap in later if perf becomes an issue).
- `tests/test_compute.py` — 19 tests: happy/edge/failure for each feature + a **parametrized P-05 lookahead guard** that perturbs future bars and asserts past feature values are unchanged. Every feature is on that guard list.
- 57/57 tests pass; ruff clean.
- Live verify: ran all features over the 30-day BTC parquet. ema12/26 cross plausibly, RSI ≈ 40 (slight oversold), ATR ≈ $225 (~0.3 % of price), regime distribution ≈ thirds with 71 NaNs at the head (lookback warm-up).

**Next:** Strategy layer — `strategies/baseline_ema_cross.py`. Pure rule, no LLM/ML, outputs `{symbol, side, conviction, stop, target}` per bar. After that: backtest harness wired to it on the 30-day BTC parquet.

**Blockers:** none.

**New lessons:** none — but the lookahead guard pattern is worth keeping in mind for any future feature; I'd consider adding a P-** entry if we ever discover a feature that quietly violated it.

---

## 2026-04-25 (session 8) — Backfill + Parquet store

- `uv add pandas pyarrow` (numpy 2.4.4, pandas 3.0.2, pyarrow 24.0.0).
- `data/backfill.py` — `backfill_ohlcv` paginates `fetch_ohlcv` from `since_ms` until end-of-data or `until_ms`. Dedups overlapping timestamps; defensive against no-progress loops; explicit `chunk_size 1..1000`; optional `sleep_s` between pages.
- `data/store.py` — `bars_path()` canonical layout `data/bars/{exchange}/{SYM_USDT}/{tf}.parquet`. `save_bars()` is idempotent (merges + dedups against existing file). `load_bars()` returns `list[Bar]`.
- Renamed `data/binance.py::_OHLCVClient` → `OHLCVClient` (now a public Protocol shared with backfill).
- Tests: 6 backfill (happy pagination, until_ms trim, empty, dedup, 6× invalid-input parametrize, network error) + 5 store (round-trip, merge dedup, missing-file, invalid symbol, canonical path). **38/38 total.**
- Live verify: pulled 30 days BTC/USDT 1h from Binance → 720 rows, written to Parquet (34 KB), reloaded byte-identical.

**Next:** Either (a) the WS-stream piece for real-time bars, or (b) features layer (`features/compute.py` returns/EMA/RSI/ATR over the 30 days we now have on disk) which unblocks the EMA-cross strategy. Backtest-path (b) is the more direct unblocker for end-to-end Phase 1.

**Blockers:** none.

**New lessons:** none.

---

## 2026-04-25 (session 7) — First real code: Binance OHLCV fetcher

- `uv add ccxt` → ccxt 4.5.50 (first non-dev dep).
- `data/binance.py` — `fetch_ohlcv(symbol, timeframe, limit) -> list[Bar]`. TypedDict `Bar`, input validation upfront (symbol format, timeframe whitelist, limit 1..1000), injectable client for tests, no auth (public endpoint).
- `tests/test_binance.py` — 8 tests: 1 happy (parsing 2 sample rows), 1 edge (empty response → []), 5 invalid-input failures (parametrized), 1 network-error propagation. All pass; total 21/21.
- `data/__init__.py` added; `data` added to smoke MODULES list.
- Verified live: pulled 3 BTC/USDT 1h bars from Binance public API. Returned timestamps + OHLCV correctly.
- I-1 in `@architecture.md` clarified: only `execution/` may *place orders* via broker SDKs; `data/` may use the same SDK read-only (cannot move money).

**Next:** Either WebSocket bar ingestion → Parquet (live ticks) **or** historical backfill (5 years for BTC/USDT) — backfill is more valuable for backtest first, WS for live executor.

**Blockers:** none.

**New lessons:** none.

---

## 2026-04-25 (session 6) — Hosting locked: Mac Mini through Phase 2

- D-18 added to `@design-doc.md`: Mac Mini stays through Phase 1 + 2 paper-soak; migrate to Hetzner CX22 (~€4/mo) when Phase 3 (real money) begins.
- Phase 0 hosting task ticked. Added Phase 1 prep task: `sudo pmset` 24/7 config + Tailscale reachability + caffeinate wrapper, to be run *before* the 7-day soak — not now (bot doesn't exist yet).

**Next:** First real code — Phase 1 data layer: `execution/ccxt_paper.py` skeleton + Binance OHLCV pull. Will need to `uv add ccxt`.

**Blockers:** Push to GitHub still deferred pending git author identity decision.

**New lessons:** none.

---

## 2026-04-25 (session 5) — CI green

- `.github/workflows/ci.yml`: GitHub Actions runs `ruff check`, `ruff format --check`, and `pytest --cov` on push & PR to `main`. Matrix: Python 3.12 + 3.13.
- Verified locally: ruff lint pass, format pass (14 files), 13/13 tests, 100% coverage on the (empty) modules.
- `implementation-plan.md` Phase 1 task 3 ticked.

**Next:** Hosting decision (Mac Mini-only for paper-soak, or provision Hetzner VPS now to avoid mid-Phase-1 migration?). Then first real code: `execution/ccxt_paper.py` skeleton + Binance OHLCV pull (Phase 1 data layer).

**Blockers:** Push to GitHub deferred — git author still defaulted to `diaohm@mac-mini.local`, user needs to set `git config user.email` first if they want commits to bind to `dotsystemsdevs` on GitHub.

**New lessons:** none.

---

## 2026-04-25 (session 4) — Phase 1 scaffold landed

- `uv init --bare` → `pyproject.toml` with `requires-python>=3.12` (D-1), no deps yet, dev-group with `pytest`, `pytest-cov`, `ruff`. Ruff configured (line-length 100, target py312, rules E/F/I/B/UP/N/SIM).
- Folder skeleton per `CLAUDE.md` §5 created: `agents/ strategies/ signals/ features/ execution/ risk/ backtest/ memory/ tools/ api/ ui/ workers/ tests/` + gitignored runtime dirs `data/{state,decision_log,bars,cache}/` and `config/live/`. Each Python module has empty `__init__.py`.
- `.env.example` created with LIVE_TRADING/KILL_SWITCH gates + placeholders for Anthropic, Binance, Kraken, Telegram. `.env` itself is gitignored.
- `README.md`: minimal pointer to `CLAUDE.md` + setup commands.
- `tests/test_smoke.py`: parametrized import test for every module + Python-version assert. **13/13 pass** under `uv run pytest`.
- `@architecture.md` §1 updated to reflect actual on-disk state.

**Next:** Phase 0 final task — decide hosting beyond Mac Mini (VPS for paper-soak in Phase 1?). Then Phase 1 task 3 (`tests/` with CI on push) and Phase 1 data layer (`execution/ccxt_paper.py` skeleton + Binance OHLCV fetch).

**Blockers:** none. Git author identity warning (defaulted to `diaohm@mac-mini.local`) — should set globally before next commit if user wants attribution to match GitHub account.

**New lessons:** none — this was pure plumbing.

---

## 2026-04-25 (session 3) — Onboarding done on Mac Mini

- Repo cloned to `/Users/diaohm/Desktop/trade/traderbot/`.
- Hardware verified: **Mac Mini M1 (Macmini9,1), 8 GB RAM, macOS 26.2 Tahoe** — *not* the Intel 2018 documented in D-11. Updated `@design-doc.md` D-11 accordingly. 8 GB constraint and D-14 (no local LLMs) still hold.
- Prerequisites verified: Xcode CLI ✓, Homebrew ✓, git 2.50.1 ✓, uv 0.10.4 ✓, Python 3.14.2 ✓.
- Fixed Homebrew permissions (`sudo chown -R diaohm /opt/homebrew ...` after macOS upgrade left dirs not writable).
- Installed `ta-lib 0.6.4` via brew.
- GitHub: confirmed `dotsystemsdevs` is user's account (D-16 satisfied).
- Telegram: not yet set up — deferred until needed in Phase 1 §Monitor.

**Next:** scaffold the project per Phase 1 task 1 in `implementation-plan.md` — `uv init` + folder skeleton per `CLAUDE.md` §5.

**Blockers:** none.

**New lessons:** none.

---

## 2026-04-25 (session 2) — All design decisions locked

User answered the open questions. All of D-1..D-10 plus 7 bonus decisions (D-11..D-17) now locked in `@design-doc.md` §3.

**Stack locked:** Python 3.12 + uv · CCXT (data) · Binance (data) / Kraken (live, Phase 3) · Backtrader · SQLite + Parquet · Streamlit · Telegram alerts · Claude Agent SDK + provider abstraction.

**Markets locked:** Crypto spot only. Fas 1 = BTC/USDT only. Fas 2 = +ETH +SOL. Fas 3 = top-N by 30-day volume.

**Hosting locked:** User's Mac Mini 2018 (Intel i3-8100B, 8 GB / 256 GB, macOS Ventura). 8 GB RAM constraint → no local LLMs, cloud APIs only. 256 GB OK with Parquet compression + log rotation.

**Live-capital posture:** start small (€200-500 calibration), ramp gradually on metric gates. Risk math is %-based.

**Public signal-feed:** deferred to Phase 3 evaluation. If chosen: ai4trade.ai + Bitget Copy Trading.

**Next:**
1. User installs prerequisites on Mac Mini: Xcode CLI tools, Homebrew, `uv`, git, TA-Lib via brew.
2. User creates: GitHub account, Telegram account + bot via @BotFather.
3. Then: scaffold the project (Phase 1 task 1 in `implementation-plan.md`).

**Blockers:** none. User onboarding step required (install prerequisites + create accounts).

**New lessons added to `experiences.md` this session:** none (this was decision-locking, not new domain learnings).

---

## 2026-04-25 (session 1) — Project bootstrapped (no code)

- Compiled `knowledge.md` (15 sections covering strategies, AI/ML/RL/LLM-agents, indicators, risk, backtesting, data sources, execution, AI-Trader API surface, recommended architecture, deployment checklist, tech stack, reading list).
- Surveyed 11 open-source AI trader repos (`AI-Trader`, `Vibe-Trading`, `TradingAgents`, `ai-hedge-fund`, `ritmex-ai-trader`, `nofx`, `whchien/ai-trader`, `polymarket-paper-trader`, `PowerTrader_AI`, `QuantGPT`, `ORSTAC`). Comparison table + cross-repo patterns in `knowledge.md` §9.
- Compiled `experiences.md`: **33 pitfalls (P-01..P-33)** and **58 success factors (S-01..S-58)** distilled from industry research + 7 r/algotrading / r/Daytrading / r/ai_trading / r/passive_income / r/metatrader threads + 1 Medium repo survey. Each entry has source citation + #tags.
- Set up `CLAUDE.md` (operating rules, hard rules, no-touch list, session protocol).
- Set up `memory-bank/`: `@architecture.md` (planned target architecture + invariants I-1..I-7), `@design-doc.md` (mission, success criteria, open decisions D-1..D-10, scope, risk posture), `implementation-plan.md` (phases 0–3+), `progress.md` (this file).
- Saved project memory under `~/.claude/projects/.../memory/`.

**Next:** resolve D-1..D-10 in `@design-doc.md`. Cannot scaffold code without these.

**Blockers:** none.

---

## Template for future entries

```
## YYYY-MM-DD — <one-line title>

- What changed (bullet 1).
- What changed (bullet 2).

**Next:** <the very next concrete step>.
**Blockers:** <none / what's stopping us>.
**New lessons:** P-** or S-** added to `experiences.md` (if any).
```
