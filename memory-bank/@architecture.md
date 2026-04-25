# @architecture.md — Current File Map & Invariants

> Always read at session start. Updated whenever architecture changes.
> Status as of 2026-04-25: **no code yet — knowledge-gathering phase complete.**

---

## 1. What exists right now

```
traderbot/
├── CLAUDE.md          # operating rules
├── knowledge.md       # domain knowledge (15 sections)
├── experiences.md     # 33 pitfalls (P-01..P-33), 58 success factors (S-01..S-58)
└── memory-bank/
    ├── @architecture.md      # this file
    ├── @design-doc.md         # what + why
    ├── implementation-plan.md # ordered tasks
    └── progress.md            # log of milestones
```

No source code yet. No package manifest, no tests, no executor, no broker connection.

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
| I-1 | Only `executor/` imports broker SDKs. | Single point of order placement; impossible to bypass risk checks. | Lint rule + CI grep. |
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
