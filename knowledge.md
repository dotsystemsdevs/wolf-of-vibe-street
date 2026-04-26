# Master Knowledge — AI Trader Bot

> Living knowledge base for building an AI/agent-based trading bot.
> Compiled from public research, frameworks, and industry sources (April 2026).
> Pair with `experiences.md` (lessons learned) and update both as you learn.

---

## 0. Mental Model — what is an "AI Trader"?

There are three broad architectural styles. Pick consciously.

| Style | What it is | When to use |
|---|---|---|
| **Rule-based bot** | Hand-coded entry/exit rules using indicators (e.g. "buy when RSI<30 + MACD cross"). | Simple, transparent, easy to debug. Baseline. |
| **ML / RL bot** | A model learns the policy or signal from historical data (supervised, or RL agent like FinRL/PPO). | When you have lots of data and want adaptive behavior. |
| **LLM-agent bot** | One or more LLMs reason over news, prices, fundamentals; tools execute trades. | Mixed-modality data (text + numbers); explainability; rapid prototyping. Higher latency, cost, hallucination risk. |

Modern systems (2026) often **stack** these: rule-based risk layer + ML signal generator + LLM "research analyst" agent for context.

---

## 1. Core Trading Strategies

### 1.1 Momentum / Trend-following
Premise: assets that are moving keep moving.
- Tools: moving averages (SMA/EMA), MACD, ADX, breakout detection, Donchian channels.
- Works in: trending markets. Fails in: choppy/sideways.
- Classic: 12-26-9 MACD; 50/200 EMA crossover; price > 20-day high.

### 1.2 Mean reversion
Premise: prices revert to a historical mean.
- Tools: Bollinger Bands, RSI, z-score of price/return, pairs trading.
- Works in: range-bound markets. Fails in: strong trends ("knife catching").
- Classic: short when RSI>70 + price > upper Bollinger; long inverse.

### 1.3 Statistical arbitrage / pairs
Long one asset, short a correlated one when their spread diverges from the historical mean. Needs cointegration testing (Engle-Granger / Johansen).

### 1.4 Market making / grid
Place buy & sell limit orders around a reference price, profiting from spread. Sensitive to inventory risk and adverse selection.

### 1.5 Event-driven / news
Trade reactions to earnings, economic releases, on-chain events, regulatory news. Requires fast data + NLP.

### 1.6 Sentiment-driven
Use NLP scores from Twitter/Reddit/news as a feature or standalone signal. Reddit can lead price by 15–30 min on mid-cap tokens; correlation is real but modest (Spearman ~0.25 next-day) — best as a complementary input, not a sole signal.

---

## 2. AI / ML Approaches

### 2.1 Supervised learning
Predict next-period return / direction.
- Features: technical indicators, returns, volume, volatility regime, calendar effects, macro data.
- Models: gradient-boosted trees (XGBoost, LightGBM) often beat fancier nets on tabular price data; LSTMs / Transformers if you have rich sequence data.
- Target engineering: predicting direction (classification) is usually more robust than predicting magnitude.

### 2.2 Reinforcement learning
Agent learns a policy that maps state → action (buy/sell/hold/size).
- **FinRL** (AI4Finance) is the dominant open-source library: trains A2C, DDPG, PPO, TD3, SAC via Stable-Baselines3 in OpenAI-Gym-style envs.
- State design matters more than algo choice: include price, volatility (VIX), turbulence, technicals.
- RL is sample-hungry and unstable on financial data (non-stationary); start with PPO + small action space.

### 2.3 LLM-driven trading agents
Latest trend (2025–2026):
- **Multi-agent frameworks** (TradingAgents, FinMem, AI-Trader): specialized roles — Fundamentals Analyst, Sentiment Analyst, News Analyst, Technical Analyst, Researcher, Trader, Risk Manager. Agents debate, then a Trader synthesizes.
- **Memory layers**: persist trade rationales, regime labels, risk preferences (FinMem-style layered memory).
- **Tool use**: agents call price APIs, run backtests, post orders.
- **Orchestration**: LangChain / LangGraph; OpenAI Agents SDK; Claude Agent SDK.
- **LLM choice**: GPT-5 / Claude Opus 4.7 / Gemini 3 / DeepSeek for cost. Long context matters when feeding news + price history.
- **Watch-outs**: hallucination on numbers (always make the LLM call a calculator/tool, never trust raw arithmetic), latency (don't put LLMs in the hot execution path — use them for research and signal generation, not for order routing).

---

## 3. Technical Indicators (cheat-sheet)

Use **TA-Lib** (200+ indicators, C-fast) or **pandas-ta** (130+, pure Python) in Python.

| Indicator | What it measures | Common params | Typical signal |
|---|---|---|---|
| SMA/EMA | Trend | 9, 20, 50, 200 | Price > 200 EMA = bull regime; cross of fast/slow |
| RSI | Momentum (overbought/sold) | 14 | <30 oversold, >70 overbought |
| MACD | Trend + momentum | 12/26/9 | MACD crosses signal line |
| Bollinger Bands | Volatility | 20, 2σ | Touch upper/lower; squeeze = vol expansion incoming |
| ATR | Volatility (raw) | 14 | Used to size stops (e.g. stop = 2×ATR) |
| ADX | Trend strength | 14 | >25 trending, <20 ranging |
| OBV | Volume / accumulation | — | Divergence with price = warning |
| VWAP | Intraday fair price | session | Mean-reversion anchor |
| Stochastic | Momentum | 14,3,3 | %K crossing %D |

**Feature engineering tips**: rolling z-scores, return windows (1d, 5d, 20d), volatility regimes, time-of-day / day-of-week dummies, lagged features (no look-ahead!).

---

## 4. Risk Management (the part that actually keeps you alive)

### 4.1 Position sizing
- **Fixed % risk per trade**: 0.5%–2% of equity per trade, sized so that hitting your stop = that loss. The most common, robust default.
- **Kelly criterion**: `f* = (p·b − q) / b` where p=win rate, q=1−p, b=win/loss ratio. Optimal long-run growth — but full Kelly = brutal drawdowns. Use **half- or quarter-Kelly** in practice.
- **Volatility targeting**: size = target_vol / realized_vol. Auto-de-risks in turbulent regimes.

### 4.2 Stops
- **Hard stop**: fixed % or ATR-multiple below entry.
- **Trailing stop**: follows favorable moves (e.g. 2% trailing on BTC 65k → 70k locks stop at 68.6k).
- **Time stop**: exit if thesis hasn't played out in N bars.
- Never trade without one. "I'll watch it" doesn't survive sleep.

### 4.3 Portfolio-level guards
- **Max daily drawdown** (e.g. stop trading if down 3% on the day).
- **Max weekly drawdown** (e.g. 7%).
- **Max concurrent positions** / **max correlated exposure**.
- **Kill switch**: a single env var or DB flag that halts all new orders.

### 4.4 Cost realism
- Commissions + spread + **slippage** (often 0.05–0.5% on liquid; multiples worse on thin markets).
- Funding rates (perps), borrow fees (shorts), gas (on-chain).
- Backtests that ignore these typically inflate Sharpe by 30–50%.

---

## 5. Backtesting Done Right

### 5.1 The big traps
| Trap | What it is | Fix |
|---|---|---|
| **Overfitting** | Tuning to historical noise. R² of backtest Sharpe vs live Sharpe is < 0.025 in big studies. | Few parameters, walk-forward, OOS test, simpler is better. |
| **Look-ahead bias** | Using info you wouldn't have had at decision time (e.g. close of same bar). | Shift signals by 1, use bar opens, audit each feature. |
| **Survivorship bias** | Testing only on assets that exist today. | Use point-in-time universes incl. delisted. |
| **Data snooping** | Trying 1000 strategies, picking best. | Reserve a holdout you only touch once; multiple-testing correction. |
| **Slippage = 0** | Assumes perfect fills. | Model spread + impact; stress with 2×–5× expected slippage. |
| **Regime cherry-pick** | Tested only on 2017 bull. | Cover bull/bear/chop, multiple cycles. |

### 5.2 Walk-forward analysis
Roll a window: optimize on `[t-train, t]`, evaluate on `[t, t+test]`, advance, repeat. Sums to a continuous out-of-sample track. More realistic than single train/test split. Cost: compute-heavy, still tests one price path → combine with Monte Carlo / noise testing.

### 5.3 Metrics that matter
- **Sharpe** (annualized) — risk-adjusted return; >1 acceptable, >2 good, >3 suspicious.
- **Sortino** — only penalizes downside vol.
- **Max drawdown** + **time-to-recover**.
- **Calmar** = annual return / max DD.
- **Hit rate**, **avg win/avg loss**, **profit factor** (gross win / gross loss).
- **Turnover** + **net-of-cost** return.

### 5.4 Python libraries
| Library | Strength | Use when |
|---|---|---|
| **VectorBT** | Vectorized, Numba-fast, huge param sweeps | Research, optimization, large universes |
| **Backtrader** | Class-based, intuitive, live-broker support (IB, Alpaca, Oanda) | Single-strategy dev → live path |
| **backtesting.py** | Tiny API, easy start | Quick prototypes |
| **Zipline-Reloaded** | Equity factor research, pipeline API | Quantopian-style factor work |
| **NautilusTrader** | High-performance, Rust core, event-driven | Pro / HFT-ish |
| **Jesse** | Crypto-focused, batteries included | Crypto-only bots |

**StrateQueue** lets you deploy VectorBT / Backtrader / backtesting.py / Zipline strategies to Alpaca or IB with one command.

---

## 6. Data Sources

### 6.1 Crypto
- **Binance API** — public market data without auth (REST + WS); broadest coverage; rate-limited.
- **Coinbase Advanced Trade API** — REST + WS, US-friendly.
- **Bybit / OKX / Kraken** — similar capabilities, regional differences.
- **CCXT** — unified Python/JS API across 100+ exchanges (the de-facto standard for multi-exchange crypto).
- **CoinGecko / CoinMarketCap** — aggregated, broad coverage; good free tiers.
- **CoinAPI** — institutional-grade, paid.

### 6.2 Stocks / equities
- **Alpaca** — commission-free US stocks + crypto; great free paper trading; clean REST/WS; the go-to retail algo broker.
- **Interactive Brokers (TWS/IBKR API)** — 150+ order types, 150 markets, <50ms latency, professional-grade. Steeper learning curve.
- **Polygon.io / Alpha Vantage / Tiingo** — historical + real-time market data.
- **Yahoo Finance** (`yfinance`) — free EOD data; good enough for research, NOT for live.

### 6.3 News / sentiment
- **NewsAPI**, **Benzinga**, **Marketaux**, **Finnhub** — news feeds.
- **Reddit (PRAW)**, **Twitter/X API** (paid now), **Pushshift archives** — social.
- **FinBERT** — pre-trained finance sentiment transformer.
- **AlphaTrace.ai / Accern** — managed sentiment APIs.

### 6.4 On-chain / DeFi
- **Dune**, **The Graph**, **Etherscan API**, **Glassnode** (paid).

### 6.5 Prediction markets
- **Polymarket** public API for slugs, outcomes, token IDs (used by AI-Trader's polymarket skill).

---

## 7. Execution & Order Management

### 7.1 Order types
- **Market** — fills now, pays the spread. Avoid in thin books.
- **Limit** — your price or better; may not fill.
- **Stop** / **Stop-limit** — triggers at price.
- **Trailing stop** — dynamic stop.
- **TWAP / VWAP / Iceberg / POV** — execution algos for large size (mostly pro brokers).

### 7.2 Best practices
- **Idempotent client order IDs** — survive retries without double-filling.
- **Reconcile on startup** — pull open orders + positions from broker, match against your DB; never assume.
- **Heartbeat/liveness** — monitor WS connection, auto-reconnect, alert on stale data.
- **Rate-limit backoff** — exponential, respect `Retry-After`.
- **Paper first**, then small size, then scale. Always.

### 7.3 Latency
- Sub-50ms = pro / co-located.
- 100–500ms = retail-cloud realistic.
- LLM-in-loop = seconds; therefore LLMs decide *what* to trade, not *when* to fill the next tick.

---

## 8. AI-Trader (HKUDS) — Reference Platform

GitHub: `HKUDS/AI-Trader` · Site: `ai4trade.ai`

### 8.1 Concept
Agent-native social trading: any AI agent registers, gets $100k paper capital, can publish signals, follow others, copy-trade. Three signal types:
1. **Strategy** — analytical content for discussion (+10 pts).
2. **Operation** — actionable trade (+10 pts, +1 per follower copy).
3. **Discussion** — community talk.

### 8.2 Stack
- **Backend**: FastAPI, separated into web service (user-facing/health) + background workers (prices, profit, settlements, market intel).
- **Frontend**: React + TypeScript.
- **Skills**: per-capability docs (`ai4trade`, `copytrade`, `tradesync`, `polymarket`, `heartbeat`, `market-intel`).
- **Languages in repo**: ~55% Python, ~38% TypeScript.

### 8.3 Key endpoints
- `POST /api/claw/agents/selfRegister` — register, returns Bearer token (`claw_*`) + bot_user_id + 100 starter pts.
- `POST /api/claw/agents/login` — login.
- `GET /api/claw/agents/me` — points/cash/reputation.
- `POST /api/claw/agents/heartbeat` — pull pending msgs/tasks (poll loop).
- `POST /api/signals/strategy|realtime|discussion` — publish.
- `GET /api/signals/feed` — read feed (filter by symbol/market/type).
- `POST /api/signals/follow` — subscribe to a provider.
- `GET /api/positions` — own + copied positions w/ P&L.
- `wss://ai4trade.ai/ws/notify/{bot_user_id}` — push notifications.

### 8.4 Markets supported
`crypto`, `us-stock`, `a-stock`, `polymarket`. Polymarket data is fetched directly from Polymarket public APIs, not proxied.

### 8.5 Useful as
- **Reference architecture** for an agent-native trading layer.
- **Place to publish signals** and copy-trade against other agents.
- **Sandbox** ($100k paper) before risking real capital.

---

## 9. Reference Repos & Cross-Repo Patterns

We surveyed **11** open-source AI/agent trading projects in the first pass (table §9.1). **§9.5** adds a 2026-04 **curated link list** (Vibe, TradingAgents, AI-Trader, DEX stack, NOFX, OpenAlice, etc.) with how each relates to *this* bot — not merged code, just knowledge. The patterns that show up in *most* of them are the success factors — what's converged is what works.

### 9.1 Comparison table

| Repo | Lang | Approach | LLM(s) | Strategy | Markets | Notable |
|---|---|---|---|---|---|---|
| `HKUDS/AI-Trader` | Py + TS | Agent-native **social** trading | any | Signal publish + copy-trade | crypto / stocks / polymarket | $100k paper, signal economy, FastAPI split web/worker |
| `HKUDS/Vibe-Trading` | Py + React | NL → strategy, **multi-agent swarm** | 12+ (Claude/GPT/Gemini/DeepSeek/Qwen/Kimi/Ollama…) | 71 skills, 29 swarm presets, 7 backtest engines | global multi-asset | MCP, FTS5 cross-session memory, Pine v6 / TDX / MT5 export |
| `TauricResearch/TradingAgents` | Py (LangGraph) | Multi-agent **debate** | 10+ providers | Hybrid TA/FA/sentiment | mostly equities | Bull/Bear researcher debate, checkpoint recovery, memory |
| `virattt/ai-hedge-fund` | Py (Claude Agent SDK) | 19 agents, **investor personas** | OpenAI/Anthropic/DeepSeek/Groq/Ollama | Multi-perspective signals (no live exec) | stocks | Buffett, Munger, Burry, Wood, Lynch, Druckenmiller, Pabrai, Fisher, Taleb, Damodaran, Ackman, Graham, Jhunjhunwala |
| `discountry/ritmex-ai-trader` | TS (Bun) | Multi-agent, **JSON message bus** | Gemini, GPT (via `ai` SDK) | TA (EMA/RSI/ATR) + LLM validation | crypto (Binance) | Zod contracts, audit logs, supervisor SLA, dry-run |
| `NoFxAiOS/nofx` | Go + React/TS | **AI competition** platform | 15+ via Claw402 | Visual builder | 9 CEX/DEX (Binance, Bybit, OKX, Bitget, KuCoin, Gate, Hyperliquid, Aster, Lighter) | **x402 USDC micropayments** instead of API keys, leaderboard |
| `whchien/ai-trader` | Py | **Backtester + MCP server** | Claude (via MCP) | 20+ built-in strategies | stocks / crypto / forex / TW | YAML config, SQLite cache, CLI + MCP |
| `agent-next/polymarket-paper-trader` | Py | **Paper-trader + MCP** | any | Event-driven, momentum, mean-rev, grid | Polymarket | 26 MCP tools, level-by-level orderbook sim, slippage in bps |
| `rnikitin/QuantGPT` | Py | **RAG over vectorbt PRO docs** | GPT-4, GPT-3.5 | n/a (dev assistant) | n/a | LlamaIndex + Chainlit; helps *write* strategies |
| `garagesteve1155/PowerTrader_AI` | Py | **Instance-based predictor** (no LLM) | — (custom ML) | DCA + trailing-profit, multi-timeframe | crypto (Robinhood) | No stop-loss, online weighted patterns, 5%/2.5% trailing |
| `alanvito1/ORSTAC` | XML / HTML | Curated bot library | — | 4000+ rule-based scripts | Deriv DBot, binary | Massive community catalog, drop-in XML uploads |

### 9.2 Patterns that converged ("success factors")

**Architecture**
- **Multi-agent** with clear single-responsibility agents (6/11). Decouple via JSON/Zod contracts on a message bus.
- **Separate web/API from background workers** (AI-Trader, Vibe-Trading) — keeps the dashboard alive when compute spikes.
- **Backtest + live share the same strategy code path** (Vibe-Trading, ritmex, AI-Trader, whchien) — anything else drifts.
- **Risk layer independent of strategy** — the executor enforces caps regardless of what the model says.

**Standard agent roles** (when you go multi-agent, this is the canonical set)
1. **Analysts** — Fundamentals · Technical · News · Sentiment.
2. **Researchers** — Bullish vs. Bearish, structured debate (TradingAgents-style).
3. **Trader** — synthesizes analyst output into an order intent.
4. **Risk Manager** — sizes, vetoes, enforces caps.
5. **Portfolio Manager** — approves/rejects, executes.
6. **Memory / Supervisor** — persists rationales, monitors SLA.

`virattt/ai-hedge-fund` extends roles with **investor personas** (Buffett, Munger, Burry, Wood, Lynch, Damodaran, Ackman, Graham, Pabrai, Fisher, Druckenmiller, Taleb, Jhunjhunwala) — useful as parallel "perspectives" on a ticker.

**Model layer**
- **Multi-provider LLM** (10/11 of LLM-using repos) — never hardcode one. Default abstractions: provider switch via env / config; treat OpenAI / Anthropic / Gemini / DeepSeek / Qwen / Kimi / Ollama as interchangeable.
- **Local fallback** (Ollama) for offline / cost / privacy.
- **Tool-call everything numeric** — LLM never does arithmetic alone.

**Integration**
- **MCP (Model Context Protocol)** is the converging standard (Vibe-Trading 17 tools, polymarket-paper-trader 26 tools, whchien/ai-trader, NOFX). Expose your bot's actions as MCP tools and any AI client (Claude Desktop, Claude Code, Cursor, OpenClaw) can drive it.
- **WebSocket + heartbeat polling** (AI-Trader) — both push and pull paths.
- **Telegram / Discord** notifications appear in nearly every project.

**Memory**
- **Persistent cross-session memory** (Vibe-Trading FTS5; TradingAgents historical decisions; FinMem layered memory). Bots that learn from yesterday beat bots that don't.
- **Decision logs with realized returns and alpha attribution** — needed for both improvement and post-mortem.

**Onboarding / safety**
- **Paper trading default** with $10k–$100k simulated capital.
- **Dry-run mode** (ritmex) — strategy runs, orders are logged but not sent.
- **Heuristic fallback when LLM unavailable** (ritmex) — degraded but live, not dead.

**Markets / exchanges**
- **CCXT** for crypto (de-facto unified API).
- **Hyperliquid / Aster / Lighter** are the perp-DEXes increasingly listed alongside Binance/Bybit/OKX.
- **Polymarket** has its own niche (event-driven prediction markets).
- **Robinhood crypto** is doable but US-only and limited (PowerTrader path).

**Emerging / experimental**
- **x402 USDC micropayments** for AI model access (NOFX) — pay-per-call instead of API-key plans. Worth watching; not yet mainstream.
- **AI competition / leaderboards** (NOFX, AI-Trader signal economy) — agents tournament-style.
- **NL → executable strategy** (Vibe-Trading) — vibe-code your strategy, system materializes it.

### 9.3 Recurring naming / structure (pick one and be consistent)

```
project/
├── agents/            # one file per role (analyst_technical.py, trader.py, risk_manager.py)
├── skills/            # capability docs / SKILL.md files (AI-Trader, Vibe-Trading)
├── tools/             # MCP tools the agents call
├── strategies/        # rule/ML strategy classes
├── data/              # ingestion, feature store
├── execution/         # broker adapters (ccxt, alpaca, ib)
├── risk/              # sizing, caps, kill switch
├── backtest/          # engine + reports
├── memory/            # persisted decisions/rationales
├── api/               # FastAPI routes (or Go/TS equivalent)
├── ui/                # React dashboard
└── workers/           # background loops (data, signal, exec, monitor)
```

Common file/endpoint names worth reusing for familiarity:
- `selfRegister`, `heartbeat`, `feed`, `signals/{strategy|realtime|discussion}`, `positions`, `me`
- `dry-run` flag, `paper` mode, `kill-switch` env var
- agent files: `*_analyst.py`, `*_researcher.py`, `trader.py`, `risk_manager.py`, `portfolio_manager.py`

### 9.4 What to copy if we build our own

A pragmatic synthesis:
1. **Start** like `whchien/ai-trader`: backtester + MCP server, YAML configs, one strategy. Ship fast.
2. **Add agents** like `TradingAgents`: 4 analysts → bull/bear debate → trader → risk. LangGraph or Claude Agent SDK.
3. **Add memory** like `Vibe-Trading`: file-based + searchable, persists rationales.
4. **Add execution** like `ritmex`: CCXT, Zod-validated contracts, dry-run, audit trail.
5. **Add social** like `AI-Trader` (optional): publish signals to `ai4trade.ai` for a public track record.
6. **Don't** copy `PowerTrader`'s "no stop-loss" stance — it's the documented anti-pattern.

### 9.5 Curated follow-up (2026-04) — links & how they relate to *this* bot

These are **not** dependencies of Wolf of Vibe Street. They are **comparable systems** to read for patterns (multi-agent, DEX, pedagogy, platform economics). We **curate into `knowledge.md`**; we do **not** merge unrelated stacks (e.g. our path is CEX+CCXT+Streamlit, not the entire Trading Strategy / DeFi executor unless we explicitly add it later).

| Resource | What it is | WOLF-relevant angle |
|----------|------------|---------------------|
| [HKUDS/Vibe-Trading](https://github.com/HKUDS/Vibe-Trading) | NL → strategy, swarms, MCP, memory | Same HKUDS lineage as AI-Trader; best reference for **skills + MCP + persistent memory** |
| [TradingAgents site](https://tradingagents-ai.github.io) | Paper + visual overview | Multi-agent “trading floor”: analysts → bull/bear research → trader → **risk** |
| [tauricresearch/tradingagents](https://github.com/tauricresearch/tradingagents) | Code + LangGraph | Aligns with our **LLM filter + risk caps** story; debate layer is optional |
| [HKUDS/AI-Trader](https://github.com/HKUDS/AI-Trader) | Agent-native platform, ai4trade | **Signals + copy-trade** model; **FastAPI + workers** split (see §8) |
| [tradingstrategy-ai (org)](https://github.com/tradingstrategy-ai) | DeFi / DEX focus | [trading-strategy](https://github.com/tradingstrategy-ai/trading-strategy) lib + trade-executor — **different market model** (on-chain) than our spot CEX; useful if we ever add DEX |
| [NoFxAiOS/nofx](https://github.com/NoFxAiOS/nofx) | Go+React, AI competition, x402 | **Leaderboard + multi-model** pressure-testing; pay-per-call ideas |
| [TraderAlice/OpenAlice](https://github.com/TraderAlice/OpenAlice) | One-agent research→exit | **End-to-end** narrative (equities/crypto/commodities/forex) — good checklist for *our* single-symbol loop |
| [MrFadiAi/ai-agents-for-trading](https://github.com/MrFadiAi/ai-agents-for-trading) | Moon-style multi-agent (risk/entry/exit) | Reinforces **separate risk agent** from strategy code (we use caps + kill switch) |
| [Harvard-Algorithmic-Trading-with-AI](https://github.com/moondevonyt/Harvard-Algorithmic-Trading-with-AI) | RBI: Research → Backtest → Implement | Same discipline as our **backtest before live** rule |

Canonical paper link for TradingAgents: [arXiv:2412.20138](https://arxiv.org/abs/2412.20138) (Xiao et al., 2024).

---

## 10. Recommended Bot Architecture

```
┌─────────────────────────────────────────────────────────────┐
│                      DATA LAYER                              │
│  Market data (WS) │ News/Social (poll) │ On-chain (poll)    │
└─────────────┬───────────────────────────────────────────────┘
              │
┌─────────────▼───────────────────────────────────────────────┐
│                    FEATURE STORE                             │
│  Bars/ticks → indicators → sentiment scores → regime label  │
│  Point-in-time correctness; no look-ahead                   │
└─────────────┬───────────────────────────────────────────────┘
              │
┌─────────────▼───────────────────────────────────────────────┐
│                  SIGNAL / RESEARCH LAYER                     │
│  Rule engine │ ML model │ LLM analyst agent(s)              │
│  Outputs: {symbol, side, conviction, horizon, rationale}    │
└─────────────┬───────────────────────────────────────────────┘
              │
┌─────────────▼───────────────────────────────────────────────┐
│                  RISK / SIZING LAYER                         │
│  Position sizing (vol-target / Kelly fraction)              │
│  Portfolio caps · drawdown guards · kill switch             │
└─────────────┬───────────────────────────────────────────────┘
              │
┌─────────────▼───────────────────────────────────────────────┐
│                  EXECUTION LAYER                             │
│  Order router (CCXT / Alpaca / IB) · idempotent IDs         │
│  Reconcile · retry · rate-limit · slippage tracking         │
└─────────────┬───────────────────────────────────────────────┘
              │
┌─────────────▼───────────────────────────────────────────────┐
│              MONITORING / LOGGING / ALERTS                   │
│  Structured logs · metrics (Prometheus) · dashboards         │
│  Telegram/Discord alerts · daily P&L report                  │
└─────────────────────────────────────────────────────────────┘
```

**Process model**: separate concerns into processes, not threads in one script.
- `data_worker` — WS feeds, persists bars/ticks.
- `signal_worker` — runs research/ML/LLM, writes signals to a queue/DB.
- `executor` — only thing that talks to the broker; consumes signals + risk-checks.
- `web/api` — dashboard + manual override.
- `monitor` — heartbeats, alerts, daily report.

This mirrors AI-Trader's "split FastAPI from background workers" lesson: user-facing stays responsive even when compute spikes.

---

## 11. Live Deployment Checklist

- [ ] Paper-trade for ≥ 4 weeks across different regimes.
- [ ] All secrets via env vars / vault, never in code.
- [ ] Structured JSON logs + log rotation; one trace ID per signal→fill.
- [ ] Metrics: latency, fill rate, slippage, P&L, error rate, queue depth.
- [ ] Alerts: WS disconnect, order reject, drawdown breach, model unavailable, broker auth fail.
- [ ] Idempotent retries with capped attempts.
- [ ] Time sync (NTP); use exchange timestamps, not local.
- [ ] Reconcile job at startup + every N minutes.
- [ ] Kill switch tested.
- [ ] Daily P&L report + weekly drift check (live vs backtest expected).
- [ ] Disaster runbook: what to do if process dies / broker down / model returns garbage.

---

## 12. Tech Stack Cheat-Sheet (a sane default)

```
Language:       Python 3.12+
Package mgmt:   uv  (fast, modern)
Data:           pandas / polars · numpy · pyarrow
Indicators:     TA-Lib  or  pandas-ta
ML:             scikit-learn · XGBoost / LightGBM · PyTorch
                hmmlearn (regime detection) · statsmodels (cointegration, stats)
                CVXPY (constrained portfolio optimization)
RL:             stable-baselines3 · FinRL
LLM agents:     Claude Agent SDK / OpenAI Agents SDK / LangGraph
Backtesting:    VectorBT (research) + Backtrader (live path)
Hyperparam:     Optuna (param search) — ⚠ overfitting machine, validate on holdout
Broker (crypto):CCXT  → Binance / Coinbase / Bybit / Kraken
Broker (stock): alpaca-py  or  ib_insync
Storage:        SQLite/Postgres (state) · Parquet (bars) · Redis (queue/cache)
Web:            FastAPI + React (mirror AI-Trader)
Monitoring:     Prometheus + Grafana · Loki for logs
Alerts:         Telegram bot or Discord webhook
Hosting:        VPS (Hetzner/Vultr) or cloud (AWS/GCP); colocate near exchange if latency matters
```

---

## 13. Reading List & Resources

### Repos (surveyed — see §9 for cross-repo patterns)
- `HKUDS/AI-Trader` — agent-native social trading platform.
- `HKUDS/Vibe-Trading` — NL → strategy multi-agent workspace, MCP, memory.
- `TauricResearch/TradingAgents` + [project site](https://tradingagents-ai.github.io) — multi-agent debate framework (LangGraph); paper on arXiv.
- `virattt/ai-hedge-fund` — 19-agent investor-persona hedge fund (Buffett/Munger/Burry/…).
- `discountry/ritmex-ai-trader` — TS/Bun multi-agent with Zod contracts, dry-run.
- `NoFxAiOS/nofx` — Go+React multi-AI competition platform, x402 micropayments.
- `whchien/ai-trader` — Python backtester + MCP server, YAML configs.
- `agent-next/polymarket-paper-trader` — Polymarket paper-trader + 26 MCP tools.
- `garagesteve1155/PowerTrader_AI` — Robinhood crypto, instance-based predictor.
- `rnikitin/QuantGPT` — RAG over vectorbt PRO docs, dev assistant.
- `alanvito1/ORSTAC` — 4000+ Deriv DBot XML scripts.
- `pipiku915/FinMem-LLM-StockTrading` — layered-memory LLM trading agent.
- `AI4Finance-Foundation/FinRL` — RL for trading.
- `tradingstrategy-ai/trading-strategy` — Python DEX data + backtest (DeFi; AGPL — read licence before reusing).
- `TraderAlice/OpenAlice` — full lifecycle agent narrative (reference only).
- `MrFadiAi/ai-agents-for-trading` — experimental multi-agent (Moon Dev lineage).
- `moondevonyt/Harvard-Algorithmic-Trading-with-AI` — RBI pedagogy (Research/Backtest/Implement).

### Live trading bots / frameworks (production-ready or near-it)
- `freqtrade/freqtrade` (~40k stars, active 2025) — most popular open-source crypto bot. Strategy in Python class, CCXT-based, supports Binance/Bybit/Kraken/OKX/KuCoin/Bitmart and many more. **FreqAI** module adds adaptive ML — train classifiers/regressors/NNs on historical data, retrain online during live runs. Web UI + Telegram. Sane default starting point.
- `jesse-ai/jesse` (~6.5k stars, active 2025) — clean Python framework, `should_long()`-style strategy API, no-look-ahead enforced backtester. **JesseGPT** assistant for strategy code. ⚠ Live-trading plugin is **closed-source / paid licence** — fine for backtesting free, budget for live.
- `asavinov/intelligent-trading-bot` (~1.4k stars, active 2025) — production case-study. Two-phase pipeline: offline ML training (feature engineering + label generation) → online streaming (compute same features live, run model, output a -1..+1 confidence score). Includes config-driven retraining schedule and a public Telegram channel running BTC/USDT 1-min signals. Worth reading as a *small, real* reference implementation.
- `nautilus-trader/nautilus_trader` (~9k stars, active 2025) — Python API, Rust core. Event-driven, low-latency. CEX + some DEX. AI-ready (you bring the model). For when you outgrow Freqtrade/Jesse.

### Libraries
- `ccxt/ccxt` — unified crypto exchange API.
- `polakowo/vectorbt` — fast vectorized backtesting.
- `mementum/backtrader` — class-based backtesting + live broker support.
- `tensortrade-org/tensortrade` (~5k stars, last update 2023) — RL framework for trading; explicitly Beta, "use cautiously in production." Good for prototyping, not for live.

### Papers / books
- López de Prado — *Advances in Financial Machine Learning* (must-read on overfitting, walk-forward, meta-labeling).
- Ernie Chan — *Algorithmic Trading*, *Quantitative Trading* (practical strategies).
- Stefan Jansen — *Machine Learning for Algorithmic Trading*.
- FinRL paper (Liu et al., 2020).
- TradingAgents paper (Tauric Research, 2024).

### Sites
- `quantinsti.com/blog`, `hudsonthames.org`, `interactivebrokers.com/campus` — quality long-form quant content.
- `arxiv.org/list/q-fin.TR/recent` — latest research.

---

## 14. Decision Log Template

When making non-trivial design choices, log them here so future-you knows *why*.

```
## YYYY-MM-DD — <decision title>
**Context:** what problem.
**Options considered:** A, B, C.
**Choice:** B.
**Why:** key tradeoff.
**Revisit when:** what would change this.
```

---

*Last updated: 2026-04-25.*
