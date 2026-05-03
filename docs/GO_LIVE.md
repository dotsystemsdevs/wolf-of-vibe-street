# Go-Live Checklist — 1000 SEK / 30-day TikTok run

The complete step-by-step from "I have a working paper bot" to "real-money
orders are flowing on Kraken." Every item maps to one of the 11 readiness
checks in the dashboard's Go-Live Readiness panel.

**Read every line before pressing anything. Real money is on the line.**

### Pre-flight (1 minute)

| Step | Action |
|------|--------|
| 1 | Kraken: KYC done, USDT funded, **API key without withdraw** (see §0.3) |
| 2 | This repo: `cd traderbot`, `./dev-start.sh` or `uv run python -m web.main` — open http://localhost:8000, sidebar build matches the code you expect |
| 3 | SETTINGS → Telegram alerts → Send test works (optional but recommended) |
| 4 | **DESK** → soak = HEALTHY after ≥12h paper run (Phase 2) |
| 5 | Symbol expectancy (30d) reviewed — you picked a strategy that isn’t all red (Phase 3) |

If any box is a no, fix it before **GO LIVE** or real-money toggles. Kraken API keys and live validation stay with you; the bot is paper- and dry-run–safe until you flip those.

---

## Phase 0 — Operator prerequisites (you do these once)

These have to be done before any of the in-app buttons work.

### 0.1 Kraken account + KYC
1. Sign up at <https://www.kraken.com>.
2. Complete identity verification (KYC). Takes **1–3 business days** — this
   is the longest wait on the whole list, so start it first.
3. Wait for "Intermediate" or higher verification tier (basic doesn't allow
   crypto trading on most jurisdictions).

### 0.2 Funding
1. Deposit **1000 SEK** via SEPA / Swish / your local rails to your Kraken
   account.
2. Convert to **USDT** on Kraken (Buy/Sell → Market). Spread is ~0.5%, so
   ~990 USDT after.
3. Verify you see ~990 USDT in your Kraken spot wallet.

### 0.3 Kraken API key
1. Kraken → **Settings** → **API** → **Create new key**.
2. Permissions to **enable**:
   - ✅ Query Funds
   - ✅ Query Open Orders
   - ✅ Query Closed Orders
   - ✅ Modify Orders
   - ✅ Cancel/Close Orders
   - ✅ Create & Modify Orders
3. Permissions to **NEVER enable**:
   - ❌ Withdraw Funds — *this is the only safety property between the bot and
     a drained account; don't grant it.*
4. Copy the API key + private key somewhere safe (password manager).

### 0.4 Mac Mini 24/7 prep
Open Terminal, paste:
```bash
sudo pmset -a sleep 0 disksleep 0 powernap 0 autorestart 1 womp 1
```
Enter your password. This makes the Mac stay awake, restart after power
loss, and accept Wake-on-LAN. Reboots and lid-close events won't kill the
bot.

### 0.5 Telegram alerts
1. Open Telegram → message **@BotFather** → `/newbot`.
2. Choose name + username; copy the bot token.
3. Message **@userinfobot** in Telegram; copy your chat ID.
4. (We'll paste both into the dashboard in Phase 1.)

---

## Phase 1 — Wire credentials into the bot (5 min in dashboard)

Open the dashboard at <http://localhost:8501>.

### 1.1 Paste Telegram credentials
- Sidebar → **TELEGRAM ALERTS** → paste token + chat ID → **Save to .env**.
- Click **Send test** — you should get a Telegram message within 5 seconds.
- If no message: check the bot has been added to a chat with you (start it
  first), and verify the chat ID is *your* numeric ID.

### 1.2 Paste Kraken API keys
- Sidebar → **KRAKEN API KEYS** → paste API key + secret → **Save**.
- Both stored in `.env` (which is git-ignored — never commits).

### 1.3 Install launchd auto-start
- Sidebar → **AUTO-START (launchd)** → expand.
- Copy the plist XML. Save to `~/Library/LaunchAgents/com.dotsystemsdevs.wolfofvibestreet.plist`.
- Copy the install command, paste into Terminal, run.
- The expander should now show "Installed — bot will auto-start on reboot."

### 1.4 Verify Go-Live Readiness panel
- Scroll to the bottom of the **DESK** tab.
- Every item from #1 to #11 should be ✓ except possibly #9 (soak) which
  needs the loop to have been running ≥12h.

---

## Phase 2 — Final paper soak (12h minimum)

If the loop hasn't been running for ≥12h on paper, start it now and wait.

- Sidebar → **LIVE LOOP** → **Start**.
- Watch the dashboard for 12 hours. Things to verify:
  - Heartbeats arrive in Telegram every hour.
  - **Soak status** banner stays HEALTHY (not yellow/red).
  - At least 1–2 signal rows appear in the Activity feed.
  - Equity curve doesn't drop suddenly (paper is stable).

If something goes red during the 12h: investigate, fix, restart the soak
timer. The whole point is to catch instability before real money is exposed.

---

## Phase 3 — Switch to live (one careful flip)

Only proceed if every readiness item is ✓ AND the bot has run paper for ≥12h
without a crash.

### 3.1 Pick a strategy that has positive expectancy
- Scroll to **Symbol Expectancy** on the Desk.
- Switch the strategy dropdown between "Baseline EMA-cross",
  "Mean-reversion RSI", "Baseline + conviction filter",
  "Mean-reversion + conviction filter".
- Pick the one with the highest expectancy on your symbol. **If all four
  have negative expectancy on your symbol, do not go live** — pick a
  different symbol or wait for better market conditions.

If you want LLM filtering on the live path:
- Sidebar → **LLM FILTER (Claude)** → paste `ANTHROPIC_API_KEY` →
  set threshold (default +0.30) → **Enable LLM filter**.

### 3.2 Activate the live-session gate
- Sidebar → **LIVE SESSION GATE** → type exactly `LIVE` (uppercase, no
  spaces) → **Activate live session**.
- Should show "ACTIVE · expires in 24h 00m". Token auto-expires daily;
  you'll need to refresh it.

### 3.3 Flip to LIVE in dry-run mode
- Sidebar → **GO LIVE** → tick all 4 confirmation checkboxes carefully.
- Click **Switch to LIVE (dry-run)**.
- Sidebar → **LIVE LOOP** → **Stop** → **Start**.
- Loop now starts with KrakenBroker in **dry-run** mode: it constructs the
  Kraken connection but every place() returns a synthetic fill. NO real
  orders flow yet.
- Watch the next bar. The decision log should show a fill row with
  `mode=live_calibration` in metadata.
- If it crashes or fills look wrong: switch back to PAPER, debug, retry.

### 3.4 Enable real-money orders
**This is the one button that costs money.**

- Sidebar → **GO LIVE** → **Enable real-money orders**.
- Sidebar → **LIVE LOOP** → **Stop** → **Start**.
- The next BUY signal will place a REAL order on Kraken.
- First order should be at most **25% of your portfolio** (= ~$25 on $100).
- Daily loss kill is set to **5% of portfolio** (= ~$5 on $100).

### 3.5 Watch the first 5 trades like a hawk
- Open Telegram alerts.
- Open Kraken's web UI in another tab (verify orders appear there too).
- Sidebar → **CALIBRATION PROGRESS** counter ticks up (1/30, 2/30, …).

If anything looks wrong (order not appearing on Kraken, fill price way off
spot, balance not updating): **press the kill switch immediately** in the
sidebar, then investigate.

---

## Phase 4 — Calibration (first 30 trades)

The bot stays in `live_calibration` mode for the first 30 fills. Caps are
tight (25% per trade, 5% daily loss). Goal: validate that fills, slippage,
and P&L attribution match what backtest predicted, NOT to make money.

- Watch fills as they come in. Compare against the backtest:
  - Are fills happening at expected prices (within slippage)?
  - Is the daily P&L tracking what equity curve predicted?
  - Are exits firing (stop / target / signal-exit) correctly?
- If cumulative P&L drops past **-$5 in one day**, the daily-loss kill
  fires automatically. The bot pauses. Investigate before resuming.

---

## Phase 5 — Promote to full live (after 30 calibration trades)

- Sidebar → **CALIBRATION PROGRESS** shows "30 / 30 — ready to promote".
- Click the promotion button (writes `TRADERBOT_TRADE_MODE=live` to .env).
- Sidebar → **LIVE LOOP** → **Stop** → **Start**.
- Caps widen to **50% per trade, 10% daily loss, 2 concurrent positions**.
- Continue running for the rest of the 30-day TikTok arc.

---

## Emergency stops (memorize these)

| Situation | Action | Effect |
|---|---|---|
| Bot is doing something weird | Sidebar → **KILL SWITCH** → Enable | Pauses new entries; existing positions still managed (stops/targets respected) |
| Need to cancel an open Kraken order | Stop the loop, log into Kraken web, cancel manually | Bot's reconcile picks up the change next start |
| Catastrophic — drain risk | Sidebar → KILL SWITCH on, then **Stop loop**, then `rm ~/Library/LaunchAgents/com.dotsystemsdevs.wolfofvibestreet.plist` | Stops everything, kills auto-restart |
| Token expired mid-day | Sidebar → **LIVE SESSION GATE** → re-type LIVE | Re-arms the gate; loop continues at next restart |
| Bot crashed in your absence | Telegram alert "Tick failed" should fire; launchd auto-restarts within seconds | Reconcile-on-startup catches any orphan state |

---

## What you should NOT do

- ❌ Disable the kill switch when the daily-loss limit fires. Investigate why first.
- ❌ Manually trade on the same Kraken account while the bot is running.
  Reconcile will detect it and refuse to start; you'll have to manually
  log the trade into the decision log.
- ❌ Edit `.env` while the loop is running. Stop the loop first.
- ❌ Change strategy mid-position. Wait until the position closes.
- ❌ Withdraw funds via API (you can't anyway — the key doesn't have permission).

---

## Code vs operator — what's left

**Already implemented in this repo (nothing for you to "build"):** paper + Parquet pipeline, strategies (incl. conviction-filtered variants in compare), decision log, risk caps (calibration vs full live), Kraken adapter with dry-run, human gate, kill switch, dashboard (soak, go-live, promotion button → `TRADERBOT_TRADE_MODE=live` in `.env`), Telegram hooks, Binance fetch retries, tests, and this checklist.

**Only you can do (we cannot do it for you):** open a Kraken account, pass **KYC**, **deposit and convert to USDT**, create an **API key** (no withdraw) and keep the secret private, run the **12h+ paper soak** on your machine, then **dry-run and live validation** with your key so fills, symbols, and `userref` idempotency match ground truth. Real money and legal/tax context are always yours.

When those operator steps are done, you are not "waiting on the codebase" — you are in **Phase 3–5** of this document.

---

## When in doubt

1. Press the **KILL SWITCH** in the sidebar.
2. Read the most recent rows in the Activity feed (Decisions tab).
3. Check Telegram for unread alerts.
4. Open Kraken web UI to compare ground truth vs. dashboard.

The bot is paper-by-default. Every interlock fails closed. The worst case
is "bot did nothing" — not "bot drained the account."
