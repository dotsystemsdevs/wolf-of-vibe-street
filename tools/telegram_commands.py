"""Two-way Telegram support — handle /commands sent BY the operator FROM their phone.

Runs in a daemon thread spawned by the live loop. Long-polls Telegram getUpdates,
dispatches recognized commands, replies via the same bot. Read-only by default —
no /kill or /resume that could pause real-money trading remotely.

Whitelisted commands:
  /help     — list commands
  /start    — alias for /help (Telegram convention)
  /status   — loop process state, kill switch, last signal age
  /pnl      — equity, cash, today P&L, total trades
  /trades   — last 5 trades with entry/exit/P&L

Security note: anyone holding the bot token can read whatever this thread exposes.
We further filter incoming messages to `chat_id` so messages from other users are
ignored — but if the token leaks, treat /trades and /pnl as compromised.
"""

from __future__ import annotations

import json
import threading
import time
import urllib.parse
import urllib.request
from pathlib import Path

import pandas as pd

_LONG_POLL_TIMEOUT_S = 30
_BACKOFF_S = 5
_TELEGRAM_BASE = "https://api.telegram.org"


class TelegramCommandListener:
    """Background poller that responds to /commands. Construct once in live_loop main()."""

    def __init__(
        self,
        *,
        token: str,
        chat_id: str,
        log_path: Path,
        kill_switch_path: Path,
        initial_cash: float = 10_000.0,
        loop_started_at_ms: int | None = None,
    ) -> None:
        self.token = token
        self.chat_id = str(chat_id)
        self.log_path = log_path
        self.kill_switch_path = kill_switch_path
        self.initial_cash = initial_cash
        self.loop_started_at_ms = loop_started_at_ms or int(time.time() * 1000)
        self._offset = 0
        self._running = False
        self._thread: threading.Thread | None = None

    # ---- lifecycle -------------------------------------------------------

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._running = True
        self._thread = threading.Thread(target=self._run, name="tg-cmd", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._running = False

    # ---- core loop -------------------------------------------------------

    def _run(self) -> None:
        while self._running:
            try:
                self._poll_once()
            except Exception:  # noqa: BLE001
                # Network error, parse error, anything — back off and retry. The
                # listener must never crash the loop process.
                time.sleep(_BACKOFF_S)

    def _poll_once(self) -> None:
        url = (
            f"{_TELEGRAM_BASE}/bot{self.token}/getUpdates"
            f"?offset={self._offset}&timeout={_LONG_POLL_TIMEOUT_S}"
        )
        req = urllib.request.Request(url)
        with urllib.request.urlopen(req, timeout=_LONG_POLL_TIMEOUT_S + 5) as resp:
            data = json.loads(resp.read())

        if not data.get("ok"):
            time.sleep(_BACKOFF_S)
            return

        for update in data.get("result", []):
            self._offset = int(update["update_id"]) + 1
            msg = update.get("message") or {}
            chat = msg.get("chat") or {}
            if str(chat.get("id")) != self.chat_id:
                continue  # ignore messages from anyone but the configured operator
            text = (msg.get("text") or "").strip()
            if text.startswith("/"):
                try:
                    reply = self._handle_command(text)
                except Exception as e:  # noqa: BLE001
                    reply = f"Command error: {type(e).__name__}: {e}"
                self._send(reply)

    # ---- command dispatch ------------------------------------------------

    def _handle_command(self, text: str) -> str:
        # Strip @botname suffix if present (e.g. "/status@wolfofvibestreet_bot")
        first = text.split()[0]
        if "@" in first:
            first = first.split("@", 1)[0]
        cmd = first.lower()

        handlers = {
            "/help": self._cmd_help,
            "/start": self._cmd_help,
            "/status": self._cmd_status,
            "/pnl": self._cmd_pnl,
            "/trades": self._cmd_trades,
        }
        fn = handlers.get(cmd)
        if fn is None:
            return f"Unknown command: {cmd}\nTry /help"
        return fn()

    # ---- commands --------------------------------------------------------

    def _cmd_help(self) -> str:
        return (
            "🤖 traderbot commands\n\n"
            "/status   — bot process, kill switch, last signal\n"
            "/pnl      — equity, cash, P&L, win rate\n"
            "/trades   — last 5 round-trip trades\n"
            "/help     — this message"
        )

    def _cmd_status(self) -> str:
        rows = self._read_rows()
        sigs = [r for r in rows if r["event_type"] == "signal"]
        last_sig_min = "—"
        if sigs:
            last_ms = max(int(r["timestamp_ms"]) for r in sigs)
            last_sig_min = f"{int((time.time()*1000 - last_ms) / 60000)} min ago"
        kill_on = self.kill_switch_path.exists()
        uptime_s = int(time.time() - self.loop_started_at_ms / 1000)
        h, m = divmod(uptime_s // 60, 60)
        return (
            f"📊 status\n\n"
            f"loop:         RUNNING ({h}h {m:02d}m)\n"
            f"kill switch:  {'ACTIVE' if kill_on else 'off'}\n"
            f"signals:      {len(sigs)} total · last {last_sig_min}\n"
            f"log rows:     {len(rows):,}"
        )

    def _cmd_pnl(self) -> str:
        from ui.views import day_pnl, equity_curve, summary  # noqa: PLC0415

        rows = self._read_rows()
        s = summary(rows, initial_cash=self.initial_cash)
        eq_df = equity_curve(rows, initial_cash=self.initial_cash)
        equity = float(eq_df.iloc[-1]["equity"]) if not eq_df.empty else self.initial_cash
        cash = float(eq_df.iloc[-1]["cash"]) if not eq_df.empty else self.initial_cash
        delta = equity - self.initial_cash
        pct = (delta / self.initial_cash * 100) if self.initial_cash > 0 else 0
        today = day_pnl(rows, now_ms=int(time.time() * 1000))
        return (
            f"💰 pnl\n\n"
            f"equity:     ${equity:,.2f}  ({pct:+.2f}%)\n"
            f"cash:       ${cash:,.2f}\n"
            f"today:      ${today:+,.2f}\n"
            f"realized:   ${s['realized_pnl']:+,.2f}\n"
            f"trades:     {s['trades']} · {s['win_rate']*100:.0f}% WR · {s['wins']}W/{s['losses']}L"
        )

    def _cmd_trades(self) -> str:
        from ui.views import trades_dataframe  # noqa: PLC0415

        rows = self._read_rows()
        trades = trades_dataframe(rows)
        if trades.empty:
            return "📋 no trades yet"
        lines = ["📋 last 5 trades\n"]
        for _, t in trades.tail(5)[::-1].iterrows():
            ts = pd.to_datetime(int(t["exit_ts"]), unit="ms", utc=True).strftime("%m-%d %H:%M")
            sign = "🟢" if float(t["pnl"]) > 0 else ("🔴" if float(t["pnl"]) < 0 else "⚪")
            lines.append(
                f"{sign} {ts}  {t['symbol']}  "
                f"${float(t['entry_price']):,.0f}→${float(t['exit_price']):,.0f}  "
                f"${float(t['pnl']):+.2f}  ({float(t['return_pct'])*100:+.2f}%)"
            )
        return "\n".join(lines)

    # ---- helpers ---------------------------------------------------------

    def _read_rows(self) -> list[dict]:
        from memory.decision_log import DecisionLog  # noqa: PLC0415

        if not self.log_path.exists():
            return []
        log = DecisionLog(self.log_path)
        rows = log.all()
        log.close()
        return rows

    def _send(self, text: str) -> None:
        url = f"{_TELEGRAM_BASE}/bot{self.token}/sendMessage"
        payload = urllib.parse.urlencode({"chat_id": self.chat_id, "text": text}).encode()
        req = urllib.request.Request(url, data=payload, method="POST")
        try:
            with urllib.request.urlopen(req, timeout=10) as resp:
                resp.read()
        except Exception:  # noqa: BLE001
            pass
