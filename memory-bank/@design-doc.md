# @design-doc.md — What We're Building & Why

> The PRD. Updated when scope shifts. Read before scoping any new feature.

---

## 1. Mission

Build a **disciplined, emotionless, paper-first AI/agent trading bot** that:
1. Survives across market regimes (boring + alive > clever + dead — S-30).
2. Has reasoning that's auditable per trade (S-09 decision logs).
3. Can be improved iteratively without rewrites (modular, broker-adapter pluggable).
4. Eventually trades real money — but only after a measured calibration period (S-21, S-55).

**Non-goals**:
- HFT / sub-millisecond strategies (we'd lose to FPGA-co-located firms — S-41).
- Predicting the next candle with LLMs (P-22 oracle anti-pattern).
- Selling the bot / monetizing as SaaS (this is for personal use; if it's good, share via signal-publishing on `ai4trade.ai`, not subscriptions).
- Single-vendor lock-in on LLMs (S-02).

---

## 2. Success criteria

### Phase 1 (skeleton + paper)
- ☐ Project scaffold builds and tests pass (CI green).
- ☐ Backtest harness runs end-to-end on a baseline rule (e.g. EMA crossover) with realistic costs.
- ☐ Paper-trading executor runs continuously for ≥ 7 days without manual restart.
- ☐ Decision log captures every order, fill, and rejected signal.
- ☐ Dashboard shows live positions, P&L, and signal feed.

### Phase 2 (intelligence layer)
- ☐ At least one ML signal layer (simple supervised model) trained on bot's own data, plugged in alongside rules.
- ☐ At least one LLM-evaluator agent gating trades (S-33 hybrid trigger + LLM).
- ☐ Per-symbol P&L attribution dashboard (S-49).
- ☐ Canary bot running in parallel, parity vs backtest measured (S-37).

### Phase 3 (live trading)
- ☐ 30+ live calibration trades with real money, very small size.
- ☐ Live-vs-paper slippage gap measured and within acceptable band.
- ☐ 6 months of low-risk live operation across at least one regime change (S-21).

We do *not* set return targets. Returns are an output of process; we measure process.

---

## 3. Decisions locked (2026-04-25)

| # | Decision | **Locked choice** | Rationale |
|---|---|---|---|
| D-1 | Primary language | **Python 3.12 + uv** | User non-coder; Python = most readable + best lib coverage + Claude is best at Python. |
| D-2 | First market | **Crypto, spot only** | 24/7 ops, CCXT unified API, no PDT rule, no fractional-share friction. Spot only — perps/leverage deferred (P-13 funding drag, blow-up risk). |
| D-3 | First broker | **Binance for data** (public API, no auth), **Kraken for live** (Phase 3+) | EU/Sweden user. Binance = best data/liquidity but MiCA gray zone for live. Kraken = EU-licensed (Ireland), Sweden-friendly, Skatteverket-compatible reporting. |
| D-4 | LLM-orchestration | **Claude Agent SDK**, with provider abstraction via env (S-02) | User already on Claude Code. Native Anthropic SDK. Swappable to GPT/Gemini/DeepSeek/Ollama via config. |
| D-5 | MVP style | **Rule-based first → hybrid trigger+LLM in Fas 2 → multi-agent in Fas 3** | S-51 math first ML second. Baseline EMA-cross proves the pipe; LLM/ML layered on top of working bot. |
| D-6 | Public signal publishing | **Private in Fas 1-2.** Re-evaluate in Fas 3. If publishing: ai4trade.ai + Bitget Copy Trading parallel. | Don't publish before 3+ months live track record. Phase-3 decision based on then-current platform landscape. |
| D-7 | Memory / state storage | **SQLite (state) + Parquet (bars)** | Zero setup, file-based, backupable. 8 GB RAM constraint on Mac Mini favors lightweight. Postgres only if/when concurrency demands it. |
| D-8 | Backtest engine | **Backtrader** for v1 (research + live-path); add Vectorbt later for param sweeps | Best docs for non-coder. Same code path as live trading. Vectorbt is overkill until we're sweeping at scale. |
| D-9 | Frontend | **Streamlit** | 30-min setup. No React knowledge required. All a personal-use trading dashboard needs. |
| D-10 | Tax handling | **Defer until live with non-trivial size.** External Koinly export when needed. | Paper trading = 0 tax. €200-500 calibration = trivial. Build when capital warrants it. |

### Bonus locked decisions

| # | Decision | **Locked choice** |
|---|---|---|
| D-11 | Hosting | **User's existing Mac Mini 2018 (i3-8100B, 8GB, 256GB, Intel x86, macOS Ventura)** |
| D-12 | Live capital posture | **Start small, ramp gradually.** Fas 3 calibration: €200-500 cap. After 30 trades + 4 weeks → scale to €1-2k if parity holds. After 6 months across regimes → scale further on metric-based gates. Risk math = percentages of bankroll, not absolutes. |
| D-13 | First symbol(s) | **Fas 1: BTC/USDT only.** Fas 2: add ETH/USDT, SOL/USDT. Fas 3: top-N by 30-day volume, systematic universe (S-56). |
| D-14 | Local LLMs (Ollama etc.) | **No.** 8GB RAM on Mac Mini insufficient. Use cloud APIs (Anthropic/OpenAI). |
| D-15 | Notifications | **Telegram bot** (free, fast, in-pocket). Slack/Discord later if needed. |
| D-16 | Version control | **GitHub** (private repo). User must create account. |
| D-17 | Remote access to Mac Mini | **Tailscale** (free, 3-min setup). |

---

## 4. Scope — what's IN and what's OUT

### In scope
- Crypto first; equities later if D-2 stays "crypto first."
- Hybrid architecture: rules + ML + (optional) LLM agents.
- Paper trading from day 1; live as Phase 3.
- Decision logs, per-symbol attribution, canary bot.
- Telegram or Discord alerts.
- One human-readable dashboard.

### Out of scope (for v1)
- Multi-account / multi-user.
- Mobile app.
- Anything DeFi / on-chain (gas, MEV, custody is its own beast).
- Options strategies (separate engine; revisit Phase 3+).
- HFT / market-making.
- Fully autonomous "set and forget" — there will always be a human in the loop for going-live decisions.

### Explicitly *not* in scope, ever
- Scams, "92% WR EAs," Discord-pump signals.
- Strategies without stop-losses (P-20, S-15).
- Borrowing real-money capital to chase a backtest.
- Letting an LLM execute orders without rule-based gating + risk caps.

---

## 5. Risk posture

Hard rules:
- **Default: paper.** Live is opt-in per session.
- **Default size: 0.5% portfolio risk per trade.** Cap at 1% even at high conviction. (P-31.)
- **Max daily DD: 3%. Weekly: 7%.** Bot halts new orders on breach.
- **No DCA against a stop-out.** DCA only inside a pre-budgeted max position. (P-20.)
- **Max 3 concurrent positions** in v1; revisit when correlation modeling is in place.
- **Kill switch:** single env var (`KILL_SWITCH=true`) halts all new orders. Monitor process polls this file every 5s.

---

## 6. Why this exists (the "boring" version)

To learn the craft of algorithmic trading by *building* something defensible — not to get rich. If the system breaks even after costs and slippage in Phase 3, it's a win. If it makes money, even better. Either way, the project produces a documented codebase, a real lessons log, and skill that compounds.

If we ever start treating "next month's return" as the success metric, re-read this section.

---

*Last updated: 2026-04-25.*
