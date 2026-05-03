# Experiences — Lessons Learned

> Living journal of what we've tried, what worked, what broke.
> The companion to `knowledge.md`. Knowledge says "this is how it's done" —
> experiences says "this is what we actually saw, and here's the scar tissue."
>
> **Rule:** every non-trivial trade outcome, surprise, bug, or design decision
> gets a one-paragraph entry here. Cheap to write, expensive *not* to.

---

## How to use this file

- **Format**: dated entries, newest at top within each section.
- **Be specific**: numbers, symbols, timestamps. "It went bad" is useless next year.
- **Capture the surprise**: what did you *expect* vs. what *happened*?
- **One actionable takeaway** per entry.

Entry template:
```
### YYYY-MM-DD — <short title>
**Context:** what we were doing.
**Expected:** what we thought would happen.
**Actual:** what happened (with numbers).
**Why:** root cause once understood.
**Takeaway:** what we'll do differently. Tag with #risk #data #model #exec etc.
```

---

## 1. Pre-loaded — known industry pitfalls

These are well-documented traps from the literature (see `knowledge.md` §5.1).
Treat them as priors before our own experiences fill in.

### P-01 — Backtest Sharpe doesn't predict live Sharpe
A study of 888 algorithmic strategies found backtest Sharpe → live Sharpe R² < 0.025. Live is ~30–50% worse than backtest is the rule, not the exception. **Takeaway:** budget for the gap; don't deploy anything that's only marginal in backtest. #backtest

### P-02 — 60% of bot failures are execution, not model
Anecdotal audits: ~60% execution risk, 25% timing, 15% model. They compound (latency worsens slippage; overfitting masks both). **Takeaway:** invest in execution plumbing before another model tweak. #exec

### P-03 — Slippage = 0 is the default lie
Most backtests assume perfect fills. Real slippage on thin markets or stress events can erase the entire edge. **Takeaway:** model spread + impact; stress-test at 2–5× expected. #backtest #exec

### P-04 — Survivorship bias inflates returns
Backtesting on today's index members hides delisted/bankrupt names. **Takeaway:** use point-in-time universes incl. delistings. #data

### P-05 — Look-ahead bias is sneaky
Using close-of-bar info to enter at the open of that same bar. Using restated fundamentals. Using normalization stats computed over the full period. **Takeaway:** build features as if streaming; audit each one for "could I have known this then?" #data

### P-06 — Full Kelly is psychologically and mathematically brutal
Optimal long-run growth, but drawdowns can be 50%+. Margin calls force liquidation at the worst moment. **Takeaway:** half- or quarter-Kelly. Or just vol-target. #risk

### P-07 — LLMs are bad at arithmetic
Asking an LLM "what's 3.2% of $84,317?" wrong answer is plausible-looking. **Takeaway:** every numeric step goes through a tool / calculator / Python eval, never raw LLM output. #model

### P-08 — Reddit/Twitter sentiment correlation is real but modest
~0.25 Spearman with next-day returns in a 200-ticker study. Useful as a feature, not a sole signal. Sarcasm, bot accounts, and stale data are persistent issues. **Takeaway:** sentiment = one input among many; preprocess (bot-filter, dedup, decay). #signal

### P-09 — Strategies have regimes
A momentum bot in 2017 prints money; in 2018 it gives it all back. Mean-reversion the inverse. **Takeaway:** test across bull/bear/chop; consider a regime-switch layer. #model

### P-10 — Time zones, DST, and exchange holidays will burn you
"Why did my cron fire 1h late in March?" / "Why is my backtest off by a day?" **Takeaway:** UTC everywhere; use exchange timestamps; explicit holiday calendars. #exec #data

### P-11 — Restarts must reconcile
Process dies mid-flight; on restart the bot doesn't know which orders are live or what positions it holds. Double-buys ensue. **Takeaway:** on every startup, fetch open orders + positions from the broker, reconcile against local state, log discrepancies, and only then resume. #exec

### P-12 — WebSocket disconnects are not errors, they're a Tuesday
Exchanges drop connections. Stale data → trading on prices that no longer exist. **Takeaway:** heartbeat watchdog (>N seconds without msg = reconnect); flag stale-data state and pause new orders. #exec

### P-13 — Fee/funding-rate drag eats small edges
Perp funding can be 0.01–0.1% every 8h; small commissions add up; gas on-chain. A "0.3% per trade" edge with 0.2% costs is barely a strategy. **Takeaway:** compute net-of-cost backtest before celebrating. #risk

### P-14 — Implicit overfitting via human iteration
You don't grid-search 1000 params, but you tweak the strategy 50 times based on backtest results. Same problem, slower. **Takeaway:** keep a sealed holdout you only touch at the end; walk-forward; pre-register the strategy spec. #backtest

### P-15 — One bug = catastrophic position
Off-by-one in size calc → 100x intended position. Wrong sign → bought when meant to short. **Takeaway:** sanity caps in the executor (max order size, max notional, max leverage) — independent of the strategy's calc. #exec #risk

### P-16 — LLMs silently drop requirements ([source: r/algotrading thread, pixelking385](https://www.reddit.com/r/algotrading/))
"chatGPT FORGOT TO ADD IT! …it had no logic for shorts when I specifically told it to." Also: AI tried to add API keys to a commit. **Takeaway:** after every code-gen pass, run a checklist *yourself* against the spec. Especially for: short logic, stop-loss, position-size cap, secrets-in-commit, exception handlers that swallow errors. The LLM "agrees" plausibly even when it didn't implement. #model #ops

### P-17 — "Many bots work… until they don't" ([source: LiveBeyondNow, same thread])
Friend ran several profitable bots; each one eventually nuked the account, fast or slow. The 3-week-up window is meaningless. **Takeaway:** treat any <6-month live track as anecdote, not evidence. Plan capital so that a full account wipe is survivable. #risk

### P-18 — Recent-bull-run flattery ([source: NotPossible1337, same thread])
OP made $300 in 3 weeks during a bull run; commenter asked specifically about October performance. Most strategies "work" in trend-up. **Takeaway:** in any P&L claim (yours or others'), the first question is *what regime did this run in*. Always test/run across bull, bear, and chop. #backtest

### P-19 — Optimize for profit, not accuracy ([source: UltraSPARC, same thread])
First Claude-built ML version "would only trade with near zero risk tolerance (when it absolutely knew QQQ would go up)." It optimized accuracy → barely traded → no money. **Takeaway:** train/score on **profit-aware** loss (PnL, Sharpe, expected utility), not classification accuracy. A 55%-accurate model that takes lots of asymmetric bets beats a 90%-accurate one that almost never trades. #model

### P-20 — Strategy "buy low, sell on profit, no real exit on losses" is the classic blow-up ([source: wentwj, same thread])
OP's pseudo-spec: buy on signal, DCA if it dips, sell when in profit. Comment: "I hope you can see the obvious potential flaw." The flaw: trades that move against you are held / averaged-down indefinitely; only winners exit; one trending bear and the account is gone. **Takeaway:** every trade must have a *symmetric* exit plan — both profit target *and* a hard stop, defined before entry. DCA only inside a pre-budgeted max position. #risk

### P-22 — "AI as oracle" outsources your ignorance ([source: Protocol7_AI, ilro_dev, Moist-Impress-7323 — r/algotrading])
"Asking ChatGPT 'what should I buy' is just outsourcing your ignorance. That's not AI-assisted trading, that's vibes with extra steps." Asking the LLM to predict the next candle gets you "confident nonsense." **Takeaway:** never let an LLM do the *prediction* without grounded data + structured reasoning. The LLM's role is synthesis and rule-enforcement, not crystal-ball. If you find yourself asking "will it go up?" you've already lost the plot. #model

### P-23 — AI on price-derived features is just a more complicated indicator ([source: anonymous, same thread])
If you train an LLM/ML on the same OHLCV your RSI is using, the output rhymes with RSI. **Takeaway:** stop. Either change the data domain (sentiment, positioning, on-chain, filings, options flow) or skip the AI layer entirely. Adding compute without adding information just inflates cost and lulls you into trusting it. #model

### P-24 — AI doesn't predict the future; it just runs rules faster ([source: kenyard, same thread])
"AI isn't predicting the future or learning or adapting continuously. It's following the same set of rules that algo traders program into things for years." Real risk: making changes via AI in a live environment is faster than understanding the consequences — "good luck if you fuck up and suddenly it buys everything." **Takeaway:** every AI-generated change to a live bot goes through the same review/test gate as a human change. AI speed ≠ permission to skip safety. #ops #model

---

### P-25 — Optuna / hyperparameter search = overfitting machine ([source: Far_Idea9616, r/Daytrading])
"Optuna tests millions of combinations to find the best parameter settings… but this technique often leads to parameters overfitting to data, the backtesting results are too beautiful." Library is genuinely fast and useful for *exploration* (e.g. finding plausible ATR multipliers), but the curve-fit output is meaningless out-of-sample. **Takeaway:** if your Optuna search returns a Sharpe of 4.5 you have *not* found alpha; you've found the corner of parameter space best fitted to noise. Always re-validate the picked parameters on a held-out walk-forward window. Same applies to grid search, genetic algorithms, neural architecture search. #backtest

### P-26 — "Same data → same conclusion" defeats LLM-ensemble redundancy ([source: ClawStreet platform, 72 agents / 3,870 trades, r/algotrading])
"A few different agents all bought AAPL at the same RSI dip within hours of each other. Same data, same conclusions." When all your LLMs see the same indicators, "consensus" across them is mostly correlated noise, not independent verification. **Takeaway:** caveat to S-34 (multi-vendor consensus): the agreement only filters *vendor-specific failure modes*, not *shared-input bias*. To get real diversification, ensemble across **different data domains** (TA + sentiment + fundamentals + flows) — not different LLMs on the same TA. #model

### P-27 — LLM data leakage in sentiment backtests ([source: Deep90 / Connect_Fishing_6378, r/algotrading])
"It's not really possible to backtest an LLM for something like this because it's trained on data from 'the future' (relative to the backtest), so you essentially have a data leakage issue." Even if you scrub dates and tickers, the LLM "knows" Y2K didn't materialize, knows airlines recovered post-COVID, knows NVDA exploded — and rates risks accordingly. **You cannot unlearn the LLM to make it point-in-time.** **Takeaway:** for sentiment/news/filings backtests using a frontier LLM, the result is contaminated by definition. Either (a) use a *small fine-tuned model trained only on pre-cutoff data*, (b) use rule-based / classical NLP (FinBERT-base, VADER), or (c) use **forward** paper-trading instead of backtesting for the LLM-touched parts. Running a parallel "ghost portfolio" of trades the LLM rejected is a clever sanity check. #backtest #model #data

### P-28 — N trades ≠ N samples — concentration kills your statistics ([source: pickupandplay22 / anonymous, r/algotrading])
"120 trades across 66 symbols is 1.8 trades per symbol — that's not 120 independent samples, that's closer to 5–10." On a momentum scanner in a 3-week window, the scan finds the 3–5 symbols that *happened* to trend. Equity curve says "strategy works"; what it actually shows is "those symbols moved." **Takeaway:** before celebrating any backtest/paper run, **segment P&L by symbol**. If the top 5 contributors carry 60%+ of return, your effective sample size is single digits and your stats are noise. Likely true for almost every short-horizon scanner. #backtest

### P-29 — Symbol-universe selected by intuition = pre-baked overfit ([source: Leading_Falcon_3705, same thread])
"How did you choose the 66 assets? If you didn't choose them systematically you are overfitting." Picking the universe based on "stocks I've heard of being volatile" or yesterday's gainers is selection on the dependent variable. **Takeaway:** the symbol list itself is a parameter — pick it by rule (top N by liquidity, sector mix, market-cap band) and ideally make the list **point-in-time** (constituents as of each backtest date, not today's roster). Otherwise your backtest is a tautology. #data #backtest

### P-30 — LLM-routing service is a single point of failure ([source: domain_expantion, r/passive_income])
Polymarket bot built on Crew.ai + LiteLLM + Kimi K2 + Groq fallback was hitting **70% success over a month** of testing — then **LiteLLM got hacked** and the whole project had to be nuked / re-architected. **Takeaway:** any third-party LLM router/aggregator (LiteLLM, OpenRouter, custom proxies) is a supply-chain SPOF. Either (a) talk directly to provider APIs and own your own thin router, or (b) keep the secrets/keys/state separate from the routing layer so a router compromise doesn't take the whole system with it. Bake key-rotation into the runbook. #ops #security

### P-32 — "You need 90%+ win rate to be profitable" is wrong ([source: r/ShitcoinTrades comment thread, recurring elsewhere])
A common gym-bro take: someone shows a 63% WR strategy and gets told "63 is very bad… for a bot to be successful it should be hitting 93 plus." This is mathematically illiterate — it ignores the win/loss ratio. By S-50's BE_WR formula, a strategy with 4× avg-win / avg-loss only needs 20% WR to break even. **Takeaway:** if anyone evaluates your strategy by WR alone, they don't understand expectancy. Show them: BE_WR = avg_loss / (avg_win + avg_loss). Filter your own thinking the same way — never tune for WR. Tune for `WR × avg_win − (1−WR) × avg_loss` (per-trade expected value), or even better, multi-period Kelly-adjusted growth. Many of the best systems have 30–45% WR and asymmetric payoffs. #risk #backtest

### P-33 — Lucky-streak survivor bias on YouTube/Reddit ([source: r/vibecoding "24 straight wins in 9 minutes" pattern])
"Built an AI trader from scratch in 2 days. Risked $100/trade. In 9 minutes won 24 straight trades. Made $2,200." Mathematically: 24 straight wins on a 50/50 strategy is ~1 in 16M; on a 70% strategy still ~1 in 4,800. If 5,000 people try, by chance ~1 will see this. Survivors post screenshots; the other 4,999 quietly delete. **Takeaway:** never update on someone's *single* hot streak — the population of attempts is invisible. The credible signal is *months* of live track with a Sharpe quoted, drawdowns, sample size in the hundreds. Apply the same skepticism to your own first week. #marketing #backtest

### P-31 — 6% risk-per-trade is account-killer territory ([source: The_AI_Trader / AromaticPlant8504, r/algotrading "Claude bot"])
A real-world example: 38% WR + 6% portfolio risk per trade. Loss clusters are *normal* at that WR — 5 losers in a row is a 24%+ drawdown; 8 in a row (which happens) is ~38%. Recoverable mathematically, brutal psychologically and often forced-liquidating in practice. **Takeaway:** P-06 (Kelly is brutal) reinforced with arithmetic. Default fixed-% risk should be 0.5–1.0%. If your strategy "needs" 6% to make meaningful money on small capital, the strategy itself is wrong, not the sizing. Don't size around an underpowered edge — find a better edge. #risk

### P-34 — Multi-symbol idempotency keys must include the symbol ([source: 2026-04-27 production-found bug, this repo])
Found while debugging a phantom `qty mismatch: ETH/USDT(broker=0 log=0.263143)` on a multi-asset paper run. Two unrelated bugs interlocked:
1. `make_client_order_id(strategy_id, signal_id)` did **not** include the symbol. BTC/ETH/SOL all signal at the same bar timestamp ms; collision → identical COID across symbols → paper broker's idempotency cache returned the *first symbol's* fill for the second and third → log records the wrong symbol on the fill.
2. The `Executor` carried `_open_stop`, `_open_target`, `_open_signal_id` as single fields, not per-symbol. Multi-symbol BUYs overwrote each other; a later bar on symbol A would compare its own bar.high against symbol B's target and trigger a phantom `target_hit`.
**Takeaway:** any I-3-style idempotency key in a multi-instrument system must include the *instrument*. And any "open position" state must be keyed by symbol from day one — single-instrument shortcuts are a trap waiting for the first multi-symbol PR. Both: assume future multi-symbol from day one even if launching with one. **Symptom to watch:** reconcile mismatch where the broker is correct and the log is wrong is almost always a duplicated/colliding COID. #execution #multi-symbol

### P-35 — Live-loop bootstrap replays the last N bars on every start ([source: same incident, this repo])
On startup `LiveLoop` fetches the last 10 bars from CCXT and processes any whose `timestamp_ms > _last_processed_ts`. With the checkpoint initialized to `None`, *every* bar in that window qualifies → the bot re-fires every signal and order it already executed in a prior session. In paper this corrupts the log; in live this would double-fill (caught by the broker's COID dedup, but logged as rejections — noise). **Fix:** seed `_last_processed_ts[sym]` from `MAX(timestamp_ms WHERE event_type='signal' AND symbol=sym)` in the decision log. **Takeaway:** any "process new since X" loop needs durable X. In-memory `None` is a footgun across process restarts. #execution

### P-21 — How to read AI / EA / prop-firm bot marketing ([source: r/metatrader "Bolt AI" thread, low-signal sample])
Recurring scam-shaped pattern in the MT4/5 EA + prop-firm niche:
- **30-second screen recording** of one good session as "proof."
- **"Consistency is getting hard to ignore"** without any cumulative metric.
- **Funnel off-platform** ("I can't drop links here, but my IG is @…").
- **Deflects technical questions** — no broker statement, no myfxbook / FX Blue, no description of the strategy or risk model.
- **Prop-firm angle** (FTMO, Goat, futures prop) — implies the bot beats the firm's risk rules, which is exactly what fits-to-noise EAs do until they don't.
- **Single product name** ("Bolt AI", "Golden Bolt") and a single asset, usually XAUUSD or NAS100.
**Takeaway:** when evaluating any bot you didn't build:
1. Ask for a public **myfxbook** or broker-verified track ≥ 6 months across regimes.
2. Ask for the **strategy class** (TA-rule? ML? grid? martingale?) — refusal = grid/martingale.
3. Ask for **max drawdown** and **biggest single losing day**.
Most "AI trader" products in this niche are martingale or grid bots in disguise — they print until one trend kills them. (See P-17 "many bots work… until they don't.") #marketing #risk

---

## 1b. Pre-loaded — success factors from surveying 11 open-source AI trader repos

We compared `AI-Trader`, `Vibe-Trading`, `TradingAgents`, `ai-hedge-fund`, `ritmex-ai-trader`, `nofx`, `whchien/ai-trader`, `polymarket-paper-trader`, `PowerTrader_AI`, `QuantGPT`, `ORSTAC`. What converges is what works.

### S-01 — Multi-agent with single-responsibility roles
6/11 use a multi-agent architecture. Canonical roster: 4 Analysts (Fundamentals, Technical, News, Sentiment) → Bull/Bear Researchers (debate) → Trader (synthesizes) → Risk Manager (vetoes/sizes) → Portfolio Manager (executes). **Takeaway:** if going LLM-agent, follow this skeleton instead of inventing one. #arch

### S-02 — LLM provider abstraction is non-negotiable
Every LLM-using project supports 5–15 providers (OpenAI/Anthropic/Gemini/DeepSeek/Qwen/Kimi/Groq/Ollama/OpenRouter…). **Takeaway:** wrap LLM calls behind one interface from day 1; never hardcode a vendor. #arch

### S-03 — MCP is the converging integration standard
Vibe-Trading exposes 17 tools, polymarket-paper-trader 26, whchien/ai-trader and NOFX also MCP-native. Lets Claude Desktop / Claude Code / Cursor / OpenClaw drive your bot via natural language. **Takeaway:** expose core actions (read positions, place order, run backtest) as MCP tools — small effort, big ergonomics win. #arch

### S-04 — Persistent cross-session memory beats stateless
Vibe-Trading uses FTS5 file memory; TradingAgents persists historical decisions; FinMem has layered memory. Bots that remember yesterday's reasoning outperform those that don't. **Takeaway:** persist `{timestamp, ticker, decision, rationale, outcome}` from the very first trade. #model

### S-05 — Risk layer must be independent of the strategy
The executor enforces caps (max notional, leverage, drawdown halt, kill switch) regardless of what the model "decided." This pattern is in AI-Trader, ritmex, NOFX. **Takeaway:** code-level: risk checks live in `execution/`, not inside the strategy class. Hard to bypass. #risk #exec

### S-06 — Dry-run / paper mode native from day 1
ritmex defaults to dry-run; AI-Trader gives $100k paper capital; polymarket-paper-trader is paper-only by design. **Takeaway:** the same code path runs paper or live, switched by a config flag. Build paper first; live is a flip, not a rewrite. #exec

### S-07 — Heuristic fallback when LLM is unavailable
ritmex falls back to TA heuristics if model API fails. **Takeaway:** never let a 502 from OpenAI freeze your bot's decisions. Either fall back cleanly or pause-and-alert — never hang. #exec

### S-08 — Validation contracts between agents
ritmex uses Zod schemas on the inter-agent message bus. **Takeaway:** define JSON schemas (Pydantic in Python, Zod in TS) for every signal/order/risk-check. Garbage-in errors are loud, not silent. #arch

### S-09 — Decision logs with realized P&L attribution
TradingAgents logs decisions + realized returns + alpha. AI-Trader logs the full signal chain. **Takeaway:** every order writes a row: `{signal_id, agent_chain, rationale, fill, realized_pnl, alpha}`. This is the only way you'll improve. #model

### S-10 — Backtest + live share the same strategy code
Vibe-Trading, ritmex, AI-Trader, whchien — all run the same strategy class in both modes. **Takeaway:** if your live and backtest paths diverge, drift is guaranteed. One code path; differ only in the broker adapter. #backtest #exec

### S-11 — Bull/Bear adversarial debate ≈ ensemble
TradingAgents runs paired bullish + bearish researchers debating before the Trader synthesizes. Reduces single-perspective bias. **Takeaway:** at minimum, ask the LLM to argue both sides explicitly before deciding — even single-agent. #model

### S-12 — Investor personas as parallel perspectives
ai-hedge-fund runs 13 personas (Buffett/Munger/Burry/Wood/Lynch/Damodaran/Ackman/Graham/Pabrai/Fisher/Druckenmiller/Taleb/Jhunjhunwala) in parallel and aggregates. Cheap, surprisingly powerful. **Takeaway:** if you're already running an LLM, running 4 persona-prompts is +0 architecture cost and gives you a "vote" you can audit. #model

### S-13 — Separate web/API from background workers
AI-Trader explicitly split FastAPI (user-facing) from background workers (prices, profit, settlements). Vibe-Trading does the same. **Takeaway:** any computation that takes >100ms goes off the request thread. Process model, not threads in one script. #arch

### S-14 — Telegram / Discord notifications are universal
Jesse, NOFX, ritmex, AI-Trader all ship with chat notifications. **Takeaway:** Telegram bot or Discord webhook from day 1; you'll catch problems hours earlier. #ops

### S-15 — PowerTrader's "no stop-loss" is the documented anti-pattern
That repo explicitly states: no stop-losses, no forced liquidation. Combined with DCA, this is how you blow up an account on a bad regime. **Takeaway:** stop-losses are not optional. ATR-based or fixed-%, but always present. #risk

### S-16 — Multi-market from the start (or hard later)
AI-Trader and Vibe-Trading support crypto + stocks + polymarket out of the gate. Single-market repos (PowerTrader = Robinhood-only) are stuck. **Takeaway:** even if you launch with crypto, design the broker adapter interface so adding Alpaca / IB / Polymarket is one new file, not a rewrite. #arch

### S-17 — Visual dashboard for human oversight
NOFX has a leaderboard, AI-Trader has a Financial Events board, Vibe-Trading has a swarm dashboard. **Takeaway:** even a tiny Streamlit page beats tailing logs. You'll catch silent failures by *seeing* state. #ops

### S-18 — Naming/structure conventions to copy
`agents/`, `skills/`, `tools/`, `strategies/`, `risk/`, `execution/`, `memory/`, `workers/`. Endpoints: `selfRegister`, `heartbeat`, `feed`, `signals/{strategy|realtime|discussion}`, `positions`, `me`. **Takeaway:** stand on shoulders. Reusing AI-Trader's endpoint names alone makes future MCP/agent integration trivial. #arch

### S-19 — Pipeline order: backtest → walk-forward → paper → live ([source: thor_testocles, r/algotrading])
"Backtesting then walk-forward testing. Paper testing is just to make sure the machine works and to adjust slippage and fill assumptions, as is first go-live." Each stage answers a different question:
- **Backtest** = "is the *idea* alive?"
- **Walk-forward** = "does it survive parameter drift?"
- **Paper** = "does the *plumbing* work? Are slippage/fees realistic?"
- **Live small** = "does anything break that paper hid?"
Skipping any of them swaps cheap learning for expensive learning. **Takeaway:** bake all four stages into the project plan; never collapse them. #backtest #exec

### S-20 — "Predicting the past" reconciliation ([source: thor_testocles, same thread])
After running live for a session, **rerun the backtester over that exact session and verify it produces the same trades**. If they diverge, the live executor and the backtest engine have drifted and your backtest results are now lies. **Takeaway:** automate this as a daily reconciliation job — alert if live trades and backtest-on-live-data don't match. #backtest #exec

### S-21 — 6-month ultra-low-risk soak ([source: LiveBeyondNow, same thread])
"Keep your risk ultra low for at least 6 months. Don't get greedy." Bots that look good for weeks routinely fail at month 4–6 when a regime shifts. **Takeaway:** scale-up schedule = 0–6 months at 0.25–0.5% risk/trade max; only ramp after a real bear or chop episode is in the live record. #risk

### S-22 — Red-team-the-strategy prompt set (a workflow lesson)
OP iterated his idea with Claude using these prompts before writing code:
- "Are there any edge cases I should handle?"
- "Give me your honest opinion about this strategy."
- "Imagine you are a hedge fund trader — give me your feedback."
- **"Give me 5 reasons why this is a bad strategy."**
- **"Give me 5 cases in which this strategy will underperform the market."**
Then: code with heavy debug logging → run → paste log back → iterate.
**Takeaway:** before writing code for any new strategy/feature, run this prompt set. Cheap; catches obvious flaws. The "5 reasons it's bad" prompt is the highest-leverage one. #workflow

### S-23 — Multi-stage pre-evaluation filtering ([source: faot231184 crypto-bot dev, same thread])
Don't run signal logic on every symbol. **Filter first**:
1. Volume / liquidity floor.
2. Spread ceiling.
3. Erratic-candle / outlier rejection.
4. Conflicting timeframe rejection (1m says up, 1h says down → skip).
Only the survivors get evaluated by the actual signal logic. **Takeaway:** a "universe filter" pass before signal generation kills 80% of bad-fill scenarios for free. #signal #exec

### S-24 — Multi-timeframe = context, not signal ([source: faot231184])
"Multiple layers of indicators (1m/5m/15m) used to generate **context and directional bias** — not for raw signal execution." Higher TFs say "we're in a bull regime"; lower TFs say "here's the entry." Don't mix them. **Takeaway:** use HTF for regime/bias, LTF for trigger. Disagreement = pass on the trade. #signal

### S-25 — Dynamic blacklists for repeat offenders ([source: faot231184])
Symbols that lose repeatedly or behave erratically get demoted/blocked for N cycles automatically. **Takeaway:** track per-symbol expectancy in real time; auto-pause symbols whose rolling expectancy drops below a floor. Don't wait for you to notice. #risk #signal

### S-26 — Per-cycle telemetry as default output ([source: faot231184])
"Full telemetry: per-cycle JSON snapshots, shell logs, audit records." PostgreSQL as single source of truth. **Takeaway:** every loop iteration writes one structured JSON: `{cycle_id, timestamp, universe_filtered, signals_evaluated, trades_taken, balances, errors}`. You can replay the bot's "thoughts" later. #ops

### S-27 — AI-coding workflow that actually scales ([source: UltraSPARC, same thread])
Two-step prompt protocol:
1. **Research prompt** ("I want to create a ML model for X, detailed write-up, multiple features, be as detailed as possible") → produces a 30-page MD spec.
2. In a fresh chat: "Create extremely detailed prompts using this research guide. **One prompt per file. Include testing parameters.**" → spits out 20–25 prompts.
3. Each prompt in its own chat → generates one source file + matching pytest file.
**Takeaway:** generate the *plan* with the LLM, then materialize file-by-file with isolated context. Avoids the monolithic-script trap. Save the spec and prompts in repo so future-you can regenerate. #workflow

### S-28 — Wash-sale rule (US tax) ([source: OP, same thread])
Day-trading the same stock in/out within 30 days triggers wash-sale: losses get *deferred* to the cost basis, not realized for tax. "It simply differs the losses. At some point in future it will settle out." **Takeaway:** for taxable US accounts: it doesn't kill the strategy but you need a CSV/1099 reconciliation tooling at year-end. Crypto and tax-advantaged accounts: irrelevant. #ops

### S-29 — Selectivity over activity ([source: OP, same thread])
Account had $27k; bot used max $7k because "did not see opportunity to trade." That's a feature, not a bug. **Takeaway:** a bot that sits in cash 60% of the time but waits for clean setups beats one that's always invested. Idle ≠ broken. #signal

### S-30 — Boring + survival = winning ([source: drguid, same thread])
"+8% real money in year 1. Only quality value stocks. No leverage/CFDs/exotic. Fully automated selling. Survived Christmas '24 and April tariff chaos." **Takeaway:** target metric for v1 isn't "best Sharpe" — it's "still alive and net positive after a real shock." Boring beats clever. #risk

### S-31 — AI for **synthesis & monitoring**, not prediction ([source: Moist-Impress-7323, r/algotrading])
"AI for prediction is almost always hype. AI for synthesis and monitoring is where it actually earns its place." Strong examples:
- Watch N correlated assets simultaneously, flag when one breaks the correlation pattern.
- Surface the news headline from 3 hours ago that explains the move.
- Second-order reasoning across asset classes: rate hike → dollar → EM debt → crude → defense ETFs.
**Takeaway:** treat the LLM as a **context-holding ambient monitor**, not as an oracle that predicts the next candle. Build it into the system as a "what changed and why" reporter, not a "will it go up" guesser. #model

### S-32 — AI on the same data as your indicators is just a more expensive indicator ([source: anonymous comment, same thread])
"Most 'AI trading tools' collapse everything into the same type of signal — trained on the same price-derived features." Real edge appears when the model pulls from a **different data domain** than your core signal: cross-sectional behavior, positioning, regime context, on-chain flows, filings, options skew. **Takeaway:** before adding any "AI feature," ask: *what data is this looking at that my existing indicators aren't?* If the answer is "the same OHLCV," don't add it. #model #signal

### S-33 — Hybrid: rule-based trigger fires LLM evaluator ([source: StevenVinyl, same thread])
Architecture:
1. Cheap technical triggers (RSI, MACD, HMA-cloud, …) run continuously.
2. When a trigger fires, an **LLM evaluator** receives the trigger + your strategy spec + recent context.
3. LLM reasons through the conditions and decides *execute / skip*.
LLM never runs in the hot path; only when a trigger has already filtered noise.
**Takeaway:** this is the pattern to default to. Indicators handle 99% of "is this remotely interesting?", LLM handles "given what's actually going on, would I take this trade?" Cheap, debuggable, no LLM cost on idle ticks. #arch

### S-34 — Multi-model consensus as **disagreement detector** ([source: PassiveBotAI, same thread])
Run 3 LLMs from different labs (e.g. Claude / Gemini / DeepSeek). All must agree on the setup before firing. "The model isn't making the trading decision. It's a disagreement detector." 114 signals evaluated → 0 trades in their paper run is the filter doing its job. **Takeaway:** when you don't trust any single LLM (you shouldn't), require unanimous agreement across vendors as a low-bar gate. Different from S-11 (Bull/Bear debate inside one model) — this one defends against *vendor-specific* failure modes. #model

### S-35 — Tiered model split (orchestrator / tool-caller / analyst) ([source: StevenVinyl, same thread])
Not every step needs the smartest model. A practical split:
- **Orchestration** → Haiku 4.5 (cheap, fast routing).
- **Tool-calling** → GPT-4.1-mini class (good function-calling, low latency).
- **Analysis / final decision** → Sonnet 4.6 / Opus 4.7 / Qwen 3.6 (deep reasoning).
**Takeaway:** wrap LLM calls so the model is a parameter per role. Costs drop ~10× with no quality loss for the structural steps. #arch #cost

### S-36 — "Regime switcher" pattern ([source: StevenVinyl, same thread])
LLM is given current ADX, ATR, EMA, etc. and asked: *what regime is this — trending up, trending down, ranging, volatile?* Output drives a **leverage / size multiplier** (or "trade nothing"), not the entry. Keeps strategy entries simple while letting context dictate aggression. **Takeaway:** separate "what is the world right now" (regime model) from "is there a trade here" (signal model). Compose them. #signal #risk

### S-37 — Canary bot for live-vs-backtest parity ([source: disarm, same thread])
Run a deliberately **known-bad, high-frequency** strategy in parallel with your real bot. You don't care that it loses; you care that it loses *the same amount in live as in backtest*. If the canary's live P&L diverges from backtest by > X%, your data pipeline / execution / fee model has drifted — pause everything and audit. If the canary suddenly stops trading in live but fires 500/day in backtest, your live data feed is broken. **Takeaway:** brilliant cheap monitoring. Plug a guaranteed-loser into the same pipeline as production; treat divergence as a critical alert. Variant of S-20 "predicting the past" but cheaper to run continuously. #ops #backtest

### S-38 — "I'm the driver, AI is the tool" ([source: JonnyTwoHands79, same thread])
"I never use it to make decisions for me or for trade execution, but rather only to assist in building something." Learn the trading craft yourself; let the LLM accelerate code-gen, data analysis, and idea iteration. **Takeaway:** the moment you start letting Claude pick trades because you don't understand the strategy, you've outsourced your ignorance (P-22). Keep the model as scaffolding, not as substitute. #workflow

### S-39 — LLM's emotional flatness is itself an edge ([source: Protocol7_AI, same thread])
"LLM has no emotion, no FOMO, no revenge trading, no ego on a losing setup. That alone already beats 90% of retail." **Takeaway:** even when the LLM's reasoning is mediocre, *consistency* is part of the value prop — it doesn't tilt after 3 losers. Lean into that: have the LLM enforce the rules a human would break. Stops the "this time will be different" override. #model #psych

### S-40 — The moat is the data pipeline and orchestration, not the model ([source: Protocol7_AI, same thread])
"Most powerful models are in public access now (Claude Opus, GLM, Qwen, DeepSeek). The bottleneck is not the model anymore, it's **the data you feed it, the pipeline you build around it, the prompts, the orchestration, and your ideas**." Bloomberg terminal is $15–20k/year for a reason. **Takeaway:** invest 80% of effort in data ingestion, feature engineering, prompt design, agent orchestration, evaluation harness. Investing in "which model" beyond a basic provider switch is low-leverage — that's commoditized. #arch #data

### S-41 — HFT and "AI agents" are different layers — don't conflate ([source: VioAce / Traditional_Ear5237 / DesertFoxHU, r/Daytrading])
"HFT is a max 3-level if-statement logic — you try to play with anything bigger it gets slow enough to eat away the edge." Real HFT runs on FPGAs / co-located bare metal, sub-millisecond, deterministic logic. LLM agents reason in **seconds**. They don't compete with HFT — they operate above it. **Takeaway:** when designing, decide which layer you're playing in. >70% of volume being "algo" doesn't mean you need to be HFT-fast; it means HFT owns the mid-spread game and you must pick a horizon (minutes / hours / days) where reasoning beats raw speed. #arch

### S-42 — Never override the bot — codify the override ([source: BlackOpz, r/metatrader])
"My bot makes trades I never would have taken. I never override the bot either. Better to program that into the bot if I see something new it should always or never do. The longer you run the bot, the more unexpected conditions you will encounter. It will have plenty of leaks you will need to patch." **Takeaway:** every override impulse is signal that a rule is missing. Add the rule, redeploy. Manual overrides are how disciplined systems become discretionary trading with extra steps. Bug ledger > intuition. #workflow #risk

### S-43 — TradingView CSV export → LLM as the cheapest analysis pipeline ([source: Far_Idea9616, r/Daytrading])
TradingView lets you export the visible chart data as CSV — *including* the values of any indicators on the chart. Hand the CSV to an LLM with a one-line schema explanation and it can compute backtests, look for divergences, suggest parameter ranges. **Takeaway:** before paying for a data API, exhaust the TV-export → LLM-Python pipeline. Free, fast, and the LLM-generated Python is portable to a real backtester later. #workflow #data

### S-44 — Annotate the chart for vision-LLMs ([source: ukSurreyGuy, r/Daytrading])
GPT-4-vision / Claude vision are *bad* at reading exact numerical values off a chart (POC at 6785 → reads as 6780). Workaround: have your indicator print the values as labels directly on the chart, plus colored dots tagging key levels (green = POC, red = VAH, yellow = VAL). Vision LLM reads the *label*, not the price-axis pixels. **Takeaway:** if you must use vision LLMs on charts, render the data into the image as text. Don't expect them to OCR the y-axis. #workflow #model

### S-45 — Use AI to bootstrap, then bake stable parts into code ([source: sigstrikes, r/Daytrading])
"All models still have their quirky off-the-rails moments. My goal is actually to code/program for more consistency and decrease reliance on the AI." Pattern: prototype with LLM in chat → once a prompt+output stabilizes → convert it into a Python function with deterministic logic → LLM only handles the residual genuinely-needs-reasoning step. **Takeaway:** treat LLM-in-the-loop as a *frontier* you push back over time. The mature system has a small LLM perimeter and a large deterministic core. #arch #cost

### S-46 — Composite + core-composite scoring ([source: iMysteryGamer, r/SideProject])
Two-tier scoring: **composite grade** = full indicator stack; **core composite** = only the heaviest-weight indicators. Cross-checked with multiple LLMs to agree on the rubric. Trade only when both grades agree. **Takeaway:** score-then-filter pattern. Lets you trade aggressively on "everything aligned" while a partial composite is just a watchlist signal. Single number per ticker is also far easier to log and audit than 12 separate indicator values. #signal

### S-47 — **Position sizing > win rate** (empirical, n=3,870 trades) ([source: ClawStreet, r/algotrading])
Across 72 agents and 3,870 trades on a public agent-trading platform: **top agent = +20% return at 50% win rate; second place = +1.6% return at 100% win rate**. Sizing up on high-conviction trades beat winning more often. **Takeaway:** when iterating, *don't* tune for win rate. Tune for expected value × position size on conviction. A 50% WR system that triples size on the strongest setups is the empirically winning shape. #risk #signal

### S-48 — **Multi-indicator confluence beats LLM choice** (empirical, n=72 agents) ([source: ClawStreet, same])
Agents requiring **3+ indicators to agree** before entering beat single-signal agents — *regardless of which LLM they ran on*. The structural rule mattered more than the model brand. **Takeaway:** strategy *architecture* dominates *model* choice. Spend time on confluence rules, not picking between Claude/GPT/Gemini. Reinforces S-40 (the moat is structure, not the model). #arch #signal

### S-49 — Always start P&L analysis with **per-symbol attribution** ([source: pickupandplay22, r/algotrading])
First thing to look at on any equity curve: which symbols carried it? Build a one-line check: `pnl_by_symbol.sort_descending().head(5).sum() / total_pnl`. If that's > 60%, you don't have a strategy, you have a small bet on those symbols. Then look at **per-symbol expectancy** — strategies that "work" on 8/66 symbols and lose on the rest are usually broken everywhere except where the regime favored them. **Takeaway:** always ship the per-symbol breakdown alongside any aggregate result. It's the cheapest reality check that exists. #backtest #ops

### S-50 — Breakeven WR formula (memorize this) ([source: pickupandplay22 calc, same thread])
`BE_WR = avg_loss / (avg_loss + avg_win)`
Example: 2.94R win, 1.18R loss → BE_WR = 1.18 / (1.18 + 2.94) = **28.7%**. So a 38% WR system has 9 points of margin — fine in principle, *within sample error* for 120 trades. **Takeaway:** never look at WR alone. Always pair it with avg-win / avg-loss and compute BE_WR. A 70% WR with avg-loss 3× avg-win has BE_WR = 75% → it's losing. A 35% WR with avg-win 4× avg-loss has BE_WR = 20% → it's winning. WR alone is meaningless; expectancy is the metric. #risk #backtest

### S-51 — **Math first, ML second** ([source: FilmFreak1082, r/ai_trading])
"Throwing a neural net at bad data just gives you confident bad decisions. Start with solid math, prove it works live, THEN layer in ML where the data supports it." His own bot: 2,300+ live trades since April 2025, 92.7% WR on Binance crypto spot — *no LLMs, no sentiment*, just deterministic momentum + mean-reversion math + position splitting. **Takeaway:** order of operations for a new strategy:
1. Hand-coded rules in Python.
2. Backtest + walk-forward.
3. Paper.
4. Live small.
5. *Once you have your own real-trade dataset*, consider an ML layer on top — **trained on your data, not on synthetic / public**.
Reverse order = "confident bad decisions" at scale. #workflow #model

### S-52 — Trailing stop must use **current-bar ATR**, not entry-bar ATR ([source: PassiveBotAI, r/algotrading])
Common bug: ATR is computed at entry, then the trailing-stop multiplier applied as a fixed number of points for the rest of the trade. Volatility expands mid-move and the stop gets eaten. **Fix:** recalculate ATR every bar; the trailing distance is `current_ATR × multiplier`, so the stop *breathes* with the market. **Takeaway:** any "stop too tight in volatility" symptom → check if ATR is stale. This single fix often saves a poorly-performing trailing logic. #exec #signal

### S-53 — Different regime → different algo (or no algo) ([source: rainman4500, r/algotrading])
"I do not run the same algo in a sideways market nor on the same set of stocks." A momentum strategy *off* in chop, *on* in trend; a mean-reversion strategy the inverse. Determine the regime first (ADX / vol regime / HMM), select the strategy. Sometimes the right move is "don't trade." **Takeaway:** combine with S-36 (regime switcher) — output of the regime model is a *strategy selector*, not just a leverage multiplier. Cleanest API: `regime → strategy_id ∈ {momentum, mean_rev, vol_breakout, off}`. #signal #risk

### S-54 — **Compliance-audit your bot's rules** ([source: Kensea98, r/algotrading])
"Document every rule the bot actually follows, not what you think it follows. Print the logic, read it like a compliance audit." If you can't explain in plain English why the bot enters and exits each trade, you don't have a strategy — you have **vibes wrapped in Python**. **Takeaway:** ship a `rules.md` per strategy that lists, in plain language: entry conditions, exit conditions, sizing, stops, regime gates. Regenerate from code, never the other way around. Auto-fail any deploy where `rules.md` is out of date relative to code. Powerful as a self-test for AI-generated strategies — if the LLM can't write the rules.md cleanly, the code is probably wrong. #workflow #ops

### S-55 — First **30 live trades = data collection**, not a P&L run ([source: pickupandplay22, r/algotrading])
"Go small on IBKR, size like you expect to lose, and treat your first 30 live trades as data collection, not a P&L run." Goal of the first month live isn't to make money — it's to **measure the gap** between paper and live: actual fill prices, realized slippage, partial-fill behavior, fee impact, broker latency, error-handling edge cases. **Takeaway:** explicitly tag the first 30 trades as "calibration period" in the trade log. Compare them to backtest expectations. *Then* decide whether to scale or to fix the gap. Going in expecting profits = trading on tilt when reality bites. #exec #ops

### S-56 — Pick the symbol universe **systematically** (and ideally point-in-time) ([source: Leading_Falcon_3705, r/algotrading])
"How did you choose the 66 assets? If you didn't choose them systematically you are overfitting." The universe is a strategy parameter — picking by intuition or "what was hot this month" is selection bias. **Takeaway:** rule-based universe definition (e.g. "top 100 by 30-day average volume on US equities, refreshed monthly, point-in-time so delisted names appear historically"). Document the rule. Backtest with the *historical* universe at each date, not today's snapshot. #data #backtest

### S-57 — Two-phase ML pipeline: offline train → online stream → periodic retrain ([source: `asavinov/intelligent-trading-bot`, production reference])
Concrete production architecture from a small-but-real running bot:
1. **Offline phase** — bulk-fetch historical data (Binance + Yahoo), engineer features, generate labels (e.g. "did price go up >X% in next N minutes?"), train classifier/regressor, save model artifact.
2. **Online phase** — service wakes every minute, fetches latest candles, computes the *same* features (single source of truth — same code as offline), runs the model, outputs a confidence score in `[-1, +1]`. Threshold-based action: > +T → buy signal; < −T → sell.
3. **Retraining loop** — schedule daily/weekly retrain on accumulated new data. Model artifact versioned. Old model kept as fallback.
**Takeaway:** the structural lesson is **same feature code in train and serve**. If your live features compute differently than your training features (different time-windows, different fillna, different dtype handling), the model output is meaningless. Single function, called from both paths. Refactor toward this from day 1. #arch #model

### S-58 — Output a confidence score, not a binary decision ([source: same repo, common pattern])
Model emits a number in `[-1, +1]` (or 0..100) representing conviction. Trading layer applies thresholds + sizing. Why this matters:
- Lets you tune trading aggressiveness *without retraining* — just change thresholds.
- Lets risk layer scale position with conviction (S-47: position sizing > win rate).
- Logs the score → backtest "what if threshold was 0.3 vs 0.5?" trivially.
**Takeaway:** never have the model emit `BUY` / `SELL`. Always emit a score; let an explicit, separate threshold/sizing rule consume it. Keeps research and execution decoupled. #arch #signal

### S-59 — **Curate external repos; don't Frankenstein stacks** ([source: 2026-04 curation of public AI-trading projects])
The space has excellent **reference** codebases (agent platforms, DEX frameworks, multi-agent papers). None of them share our exact stack (Streamlit, single-position CEX loop, Kraken interlocks). **Takeaway:** add links + lessons to `knowledge.md` and optional UI pointers; only **vendor in** another framework (e.g. DeFi executor, full LangGraph floor) after an explicit design decision. Mixing two “main apps” without that decision produced unmaintainable glue in other projects. #arch #planning

---

## 2. Our experiences

### 2026-04-25 — Project kickoff
**Context:** Starting from an empty directory. Goal: build an AI/agent-style trading bot inspired by `HKUDS/AI-Trader`.
**Done so far:**
- Compiled `knowledge.md` with strategies, AI/ML/LLM-agent approaches, indicators, risk, backtesting, data sources, exec frameworks, AI-Trader's API surface, recommended architecture, and a deployment checklist.
- Surveyed 11 open-source AI/agent trader repos (`AI-Trader`, `Vibe-Trading`, `TradingAgents`, `ai-hedge-fund`, `ritmex-ai-trader`, `nofx`, `whchien/ai-trader`, `polymarket-paper-trader`, `PowerTrader_AI`, `QuantGPT`, `ORSTAC`). Patterns + comparison table now in `knowledge.md` §9.
- Pre-loaded this file with industry pitfalls (P-01..P-15) and cross-repo success factors (S-01..S-18).
**Open questions (still):**
- Which markets first — crypto (24/7, CCXT, easy) or US equities (Alpaca, deeper liquidity)?
- Build skeleton ourselves, or **fork/adapt** an existing repo? Strong candidates: `whchien/ai-trader` (clean MCP-backtest start) or `ritmex-ai-trader` (TS multi-agent if we want TypeScript).
- Self-host, or register an agent on `ai4trade.ai`?
- Paper-only first quarter — confirmed (S-06 + P-01 leave no doubt).
**Takeaway:** Choose stack (Py vs TS) and a reference repo to clone-or-take-from before scaffolding. #planning

---

## 3. Strategy-specific journals

Once we start running strategies, log per-strategy under its own heading.

<!-- Example:
### Strategy: btc-mom-1h
**Spec:** long when 20EMA > 50EMA on 1h, ATR-stop 2x, 1% risk per trade.
**Backtest period:** 2022-01 → 2026-03, BTC/USDT Binance.
**Backtest result:** Sharpe X, MaxDD Y%, hit rate Z%.
**Live period:** YYYY-MM-DD → ...
**Live observations:**
- ...
-->

---

## 4. Incidents log

Anything that went wrong in production. Even small ones.

<!-- Example:
### YYYY-MM-DD HH:MM UTC — <title>
**Severity:** low / med / high.
**What happened:** ...
**Impact:** $X PnL, Y orders affected.
**Root cause:** ...
**Fix:** ...
**Prevention:** ...
-->

---

## 5. Model / signal experiments

Track what we tried, what we kept.

<!-- Example:
### EXP-001 — FinBERT sentiment as feature for SPY 1d direction
**Hypothesis:** ...
**Setup:** ...
**Result:** AUC 0.X on holdout; not better than baseline. Discarded.
-->

---

*Update at least weekly while developing. Cheap to write, priceless to re-read.*
