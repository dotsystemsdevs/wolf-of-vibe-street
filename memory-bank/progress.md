# progress.md — Milestone Log

> Append-only log of meaningful progress. Newest at top.
> Update at the end of every session that produced output.

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
