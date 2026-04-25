# progress.md — Milestone Log

> Append-only log of meaningful progress. Newest at top.
> Update at the end of every session that produced output.

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
