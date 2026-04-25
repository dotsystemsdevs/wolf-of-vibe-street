# progress.md — Milestone Log

> Append-only log of meaningful progress. Newest at top.
> Update at the end of every session that produced output.

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
