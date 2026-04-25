# implementation-plan.md — Ordered Task List

> The next thing to do, always. Cross off as we ship.
> When in doubt, work top-to-bottom. Don't skip to "fun" tasks before foundations.

---

## Phase 0 — Setup & decisions (NOW)

- [x] Compile `knowledge.md` (domain).
- [x] Compile `experiences.md` (pitfalls + success factors).
- [x] Set up `CLAUDE.md` + `memory-bank/`.
- [x] **Resolve open decisions D-1..D-10 in `@design-doc.md`.** Cannot scaffold without these.
- [x] Initialize git repo: `git init` + first commit (CLAUDE.md, knowledge.md, experiences.md, memory-bank/).
- [x] Add `.gitignore` covering `.env`, `data/state/`, `data/decision_log/`, `*.lock` (auto-managed), Python/TS build artefacts.
- [x] Decide hosting: **Mac Mini through Phase 2; migrate to Hetzner VPS at Phase 3** (D-18).

## Phase 1 — Skeleton + paper-trading baseline

Goal: end-to-end pipeline running paper trades on one symbol with one trivial rule, with full audit trail.

- [x] Pick package manager (`uv` for Python). Initialize project (flat layout per `CLAUDE.md` §5, not src — top-level mappar = Python-modul).
- [x] Create folder skeleton per `CLAUDE.md` §5.
- [x] Wire `tests/` with pytest (or vitest if TS). CI on push. *(pytest + ruff lint + ruff format check on push & PR via `.github/workflows/ci.yml`, matrix: Python 3.12 + 3.13.)*
- [x] **Data layer:**
  - [x] CCXT client wrapper for one exchange (Binance default). *(`data/binance.py` — OHLCV REST fetch with validation + injectable client.)*
  - [x] WebSocket bar ingestion → Parquet. *(REST polling instead — `workers/live_loop.py` polls every `poll_interval_s` and detects new closed bars via `closeTime <= now`. Functionally equivalent for 1h bars; no async machinery.)*
  - [x] Backfill historical bars (5 years if available). *(`data/backfill.py` paginates; `data/store.py` writes idempotent Parquet. Verified 30d×1h BTC/USDT round-trip; 5y pull is now a one-shot script away.)*
- [x] **Features layer:**
  - [x] `features/compute.py` — single source of truth, importable from train/backtest/live (I-2).
  - [x] First few features: returns, EMA, RSI, ATR, volatility regime label. *(All causal; P-05 lookahead guard parametrized over every feature in test suite.)*
- [x] **Strategy layer:**
  - [x] `strategies/baseline_ema_cross.py` — trivial EMA crossover. Outputs `{symbol, side, conviction, stop, target}`.
  - [x] No LLM, no ML — just to prove the pipe end-to-end.
- [ ] **Risk layer:**
  - [x] `risk/sizing.py` — fixed-% sizing on stop distance.
  - [x] `risk/caps.py` — max notional, max positions, daily/weekly DD halts, kill switch. *(Pure module + 12 tests; will be wired into the executor in the next session — backtest doesn't need it for the single-position baseline.)*
- [ ] **Execution layer:**
  - [x] `execution/broker.py` — Order/Fill/Position + `Broker` Protocol.
  - [x] `execution/ccxt_paper.py` — `PaperBroker` (mark-based fills + slippage + fee; coid-keyed idempotent).
  - [x] Idempotent client_order_id (`make_client_order_id`, I-3).
  - [x] `execution/runner.py::Executor` — bar-driven runtime tying signal → caps → broker → log.
  - [ ] Reconcile-on-startup logic (P-11). *(Live broker only; paper has no out-of-band state to reconcile.)*
- [x] **Backtest layer (v1):**
  - [x] Walk-forward harness with realistic costs (commission + spread + slippage). *(`backtest/engine.py`; entries at next-bar open; stop > target precedence on same-bar.)*
  - [x] Output: Sharpe, Sortino, max DD, BE_WR check (S-50). *(in `backtest/metrics.py`; per-symbol attribution trivial for Phase 1 since BTC-only — generalize when Phase 2 adds ETH/SOL.)*
- [x] **Decision log:**
  - [x] Append-only SQLite table. Every signal + order + fill writes a row with full rationale. *(`memory/decision_log.py`; UPDATE/DELETE blocked by triggers; metadata is JSON-serialized; lives in `data/decision_log/`.)*
- [ ] **Monitor:**
  - [ ] Heartbeat from each worker.
  - [ ] WS-disconnect detection → pause new orders.
  - [ ] Telegram bot for alerts.
- [x] **Dashboard:**
  - [x] Streamlit page: positions, P&L, recent signals, recent decisions, kill-switch status. *(`ui/dashboard.py` reads SQLite log; `ui/views.py` is the testable summary layer. Run with `uv run streamlit run ui/dashboard.py`. Known gross-vs-net P&L gap noted in `@architecture.md`.)*
- [ ] **Mac Mini 24/7 prep** (D-18) — before starting soak: `sudo pmset -a sleep 0 disksleep 0 powernap 0 autorestart 1 womp 1`, disable display sleep in Settings, ensure Tailscale (D-17) reachable, verify auto-login or `caffeinate` wrapper for the executor process.
- [ ] **Run paper for 7 days continuous.** Daily review of decision log. Note any divergence.
- [ ] **Phase 1 retro:** what surprised us? Add P-** / S-** entries to `experiences.md`.

## Phase 2 — Intelligence layer

Goal: a non-trivial signal worth running, with self-learning components.

- [ ] **Canary bot** running in parallel — known-bad high-frequency strategy, validates live-vs-backtest parity continuously (S-37).
- [ ] **First ML model** trained on Phase 1 logged data:
  - [ ] Target: short-horizon directional move (regression or classification).
  - [ ] Train/walk-forward/holdout split.
  - [ ] Output: confidence score in [-1, +1] (S-58).
- [ ] **Hybrid trigger + LLM evaluator** (S-33):
  - [ ] Cheap rule triggers candidate setups.
  - [ ] LLM evaluator (Claude Sonnet default) reasons: trigger context + recent prices + relevant news → execute / skip with rationale.
  - [ ] LLM rationale stored in decision log.
- [ ] **Regime model** (S-36, S-53):
  - [ ] HMM or volatility-cluster classifier → regime ∈ {trend_up, trend_down, range, vol_breakout, off}.
  - [ ] Strategy selector keyed on regime.
- [ ] **Multi-strategy portfolio:**
  - [ ] At least one momentum + one mean-reversion strategy.
  - [ ] Capital allocation by regime.
- [ ] **Per-symbol expectancy tracking + dynamic blacklist** (S-25).
- [ ] **Phase 2 retro.**

## Phase 3 — Multi-agent depth + live calibration

Goal: agent debate where useful, and small real-money calibration.

- [ ] **Multi-agent layer** (TradingAgents-style):
  - [ ] Bull/Bear researcher pair on top contested setups.
  - [ ] Risk Manager agent as final veto.
  - [ ] Persistent memory: agents read past decisions on the same ticker (S-04).
- [ ] **MCP-expose** key tools (read positions, place order, run backtest, query memory) so Claude Code / Desktop can drive the bot in dev (S-03).
- [ ] **Provider abstraction** for LLMs: switch Claude / GPT / Gemini / DeepSeek / Ollama via env (S-02).
- [ ] **Tiered model split** (S-35): Haiku for orchestration, mini-class for tool-calling, Sonnet/Opus for analysis.
- [ ] **Public signal publishing** (optional) — register on `ai4trade.ai`, post strategies + operations.
- [ ] **Live small** — €500 cap, first 30 trades tagged "calibration" (S-55).
- [ ] **Live calibration retro.** Measure paper→live gap. Decide whether to scale or fix.

## Phase 4+ — TBD

Decided after Phase 3 retro. Likely candidates: equities path (Alpaca), options strategies, on-chain integrations, multi-user.

---

## Working agreement

- One Phase 1 task per session unless trivial. Don't try to do five at once.
- Every task ends with: `progress.md` update + tests green + (if new lesson) `experiences.md` entry.
- If a task is blocked, move to the next; document the blocker in `progress.md`.

---

*Last updated: 2026-04-25.*
