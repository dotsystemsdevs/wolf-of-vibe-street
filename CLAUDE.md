# CLAUDE.md — Operating Manual for This Project

> Read this first, every session, before doing anything else.
> If you skip it you'll re-make mistakes already documented in `experiences.md`.

---

## 1. What this project is

Building an **AI/agent-based trading bot**, inspired by `HKUDS/AI-Trader` and the multi-agent pattern from `TauricResearch/TradingAgents`. The goal is a system that can:

- Ingest market data + news/sentiment.
- Generate signals via rules + ML + LLM agents (in that priority order — see S-51).
- Size positions and execute trades through a broker adapter.
- Run paper-only by default; never touch real money without explicit human "go live" per session.
- Keep a decision log so every trade is auditable.

This is a *real* project that may eventually trade *real money*. Caution > cleverness.

---

## 2. Required reading — in this order, every session

1. `CLAUDE.md` (this file) — operating rules.
2. `memory-bank/@architecture.md` — current file map and invariants.
3. `memory-bank/@design-doc.md` — what we're building and why.
4. `memory-bank/implementation-plan.md` — what's next.
5. `memory-bank/progress.md` — what's done.
6. `knowledge.md` — domain knowledge (strategies, libraries, broker APIs).
7. `experiences.md` — pitfalls (P-01..) and success factors (S-01..) we've already learned. **Re-read the P-entries before any backtest, risk, or execution change.**

**Before writing any code, summarize back to the human:**
- What you understood from the above.
- The specific change you intend to make.
- Which experiences-rules apply to that change.
Wait for confirmation. Do not skip this.

---

## 3. Hard rules — non-negotiable

These come from real failure modes. Each maps to one or more entries in `experiences.md`.

### 3.1 General code rules

- **500-line ceiling.** No file may exceed 500 lines. Approaching the limit → refactor into modules first, then continue.
- **Never delete or overwrite existing code unless explicitly instructed.** If something looks wrong, *flag it and ask*. Don't "clean up" silently.
- **3-test rule.** Every new feature ships with three tests:
  1. **Expected** — happy path.
  2. **Edge** — boundary case (zero size, max size, NaN input, empty data, partial fill, market closed, etc.).
  3. **Failure** — error path (API down, malformed response, timeout, auth fail).
- **No invented APIs / libraries.** If you're not certain a function exists, look it up or ask. Don't hallucinate. (See P-16 — LLMs silently drop or invent things.)
- **No comments unless WHY is non-obvious.** Don't narrate WHAT the code does. Don't add changelog comments.
- **Secrets stay in `.env`.** Never hardcode keys. Never commit `.env`.

### 3.2 Trading-specific rules (extra hard)

- **Paper trading is the default.** Live trading is opt-in per session — the human must explicitly type or say "go live" *and* the executor must read a `LIVE_TRADING=true` env flag. Both required.
- **Risk caps live in the executor, not the strategy.** Maximum order size, maximum notional, maximum leverage, max concurrent positions, max daily drawdown, kill-switch — all enforced in `execution/` regardless of what the strategy returned. (S-05, P-15, P-31.)
- **Idempotent client order IDs always.** Every order placement carries a deterministic ID derived from `(strategy_id, signal_id, attempt)` so retries can never double-fill.
- **Reconcile on every startup.** Pull open orders + positions from the broker, match against local DB, log discrepancies, halt new orders if mismatch. (P-11.)
- **Same feature code in train/backtest/live.** A single function computes a feature; all three paths import it. If they diverge, your model is now lying to you. (S-57.)
- **No look-ahead, ever.** Features at time `t` may only use data ≤ `t`. Audit each new feature for "could I have known this then?" (P-05.)
- **Every trade writes a decision log row.** `{timestamp, signal_id, strategy_id, agent_chain, rationale, fill_price, slippage_bps, realized_pnl}`. (S-09.)
- **Stop-losses are not optional.** Every entry has a defined stop *and* a defined exit plan before the order is sent. (S-15, P-20.)
- **First 30 live trades are calibration, not P&L.** Tagged as such in the trade log. We're measuring slippage / fill rate / pipeline parity, not making money. (S-55.)
- **Output a confidence score, not BUY/SELL.** Models emit `[-1, +1]`. Trading layer applies thresholds + sizing. Keeps research and execution decoupled. (S-58.)

### 3.3 No-touch list — files Claude must never modify without explicit instruction

- `.env`, `.env.*` (any environment file).
- `*.lock`, `package-lock.json`, `uv.lock`, `poetry.lock`, `pnpm-lock.yaml` — only modify by running the package manager, never by hand.
- `data/state/*` — runtime state files (positions, balances, open orders cache). Corrupting these = corrupting reality.
- `data/decision_log/*` — append-only audit log. Never edit historical rows.
- `migrations/*` — DB migrations are append-only; never edit a migration after it has run.
- `config/live/*` — anything that configures real-money trading.
- Any file under `secrets/` or `credentials/`.
- `.git/`, `.github/workflows/*` — only when explicitly asked to change CI.

If you think one of these *needs* to change: stop, surface the reason, ask.

---

## 4. Default tech stack

Decisions still open (see `memory-bank/@design-doc.md` §Open). Until decided, default is:

- **Language:** Python 3.12+, package mgmt via `uv`.
- **Crypto broker:** `ccxt` → Binance / Coinbase first.
- **Equities broker (when added):** `alpaca-py`.
- **Indicators:** `TA-Lib` or `pandas-ta`.
- **ML/RL:** `scikit-learn` + `XGBoost` / `LightGBM`; `PyTorch` if needed; `stable-baselines3` for RL; `hmmlearn` for regime; `statsmodels` for cointegration; `CVXPY` for portfolio optimization.
- **Backtest:** `vectorbt` for research; `backtrader` for live-path strategies.
- **Hyperparameter search:** `optuna` — ⚠ overfitting machine, always validate on holdout (P-25).
- **LLM orchestration:** Claude Agent SDK or LangGraph; provider abstraction — never hardcode one model (S-02).
- **Storage:** SQLite (state) → Postgres (when production); Parquet (bars); Redis (queue/cache).
- **Web:** FastAPI + React, with **separate processes** for web/API and background workers (S-13).
- **Monitoring:** Prometheus + Grafana; Loki for logs; Telegram alerts (S-14).

For full justification, see `knowledge.md` §12.

---

## 5. Folder convention

Follow the cross-repo convention from `knowledge.md` §9.3:

```
traderbot/
├── CLAUDE.md                  # this file
├── knowledge.md               # domain knowledge
├── experiences.md             # lessons learned (P-** + S-**)
├── memory-bank/               # project memory (read every session)
│   ├── @architecture.md       # current file map + invariants
│   ├── @design-doc.md         # PRD-style: what + why
│   ├── implementation-plan.md # ordered task list
│   └── progress.md            # what's done
├── agents/                    # one file per role (analyst_*, trader, risk_manager, …)
├── strategies/                # rule/ML strategy classes
├── signals/                   # signal generators consumed by trader
├── data/                      # ingestion + feature store + bars + state
├── features/                  # SHARED feature code (used by train/backtest/live)
├── execution/                 # broker adapters + order router (the only thing that talks to brokers)
├── risk/                      # sizing, caps, kill switch
├── backtest/                  # engine + reports
├── memory/                    # persisted decisions/rationales (runtime, not memory-bank/)
├── tools/                     # MCP-exposable tools
├── api/                       # FastAPI route modules (currently empty; web/main.py owns routes)
├── ui/                        # pure data fns (views.py) + CLI report (report.py); no UI framework
├── web/                       # FastAPI dashboard: main.py + Jinja2 templates + Tailwind CDN + HTMX
├── workers/                   # background loops (data, signal, exec, monitor)
└── tests/                     # unit + integration; mirrors source tree
```

**Naming:**
- Python: `snake_case.py`. Classes `PascalCase`. Functions/vars `snake_case`. Constants `SCREAMING_SNAKE`.
- Frontend (when added): components `PascalCase.tsx`, files `kebab-case.tsx` for non-component modules.
- Tests: `test_<module>.py` mirrors `src/<module>.py`.

---

## 6. Session start protocol — paste this when in doubt

```
Read CLAUDE.md, memory-bank/@architecture.md, memory-bank/@design-doc.md,
memory-bank/implementation-plan.md, and memory-bank/progress.md.
Then summarize: (a) what you understand the current state to be,
(b) what task we should work on next, (c) which P-/S-rules apply.
Do not write code until I confirm.
```

---

## 7. When you finish a unit of work

Update `memory-bank/progress.md` with one bullet — what changed, what's next.
If architecture shifted, update `memory-bank/@architecture.md`.
If a new lesson was learned, add a P-** or S-** to `experiences.md`.

If a P-** or S-** turned out wrong, **edit it** — don't add a contradicting rule.

---

*Last updated: 2026-04-25.*
