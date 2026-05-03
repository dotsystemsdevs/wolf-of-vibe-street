"""Path-to-live readiness checklist — pure function, no UI deps.

Returns one dict per check: {key, name, status: 'done'|'todo'|'in_progress', detail}.
Reused by the GO LIVE view in `web/main.py`.
"""

from __future__ import annotations

import importlib.util
import json


def _has_module(name: str) -> bool:
    try:
        return importlib.util.find_spec(name) is not None
    except Exception:  # noqa: BLE001
        return False


def go_live_readiness(
    *,
    rows: list[dict],
    loop_running: bool,
    loop_started_at_ms: int | None,
    env: dict[str, str],
    now_ms: int,
) -> list[dict]:
    """11-item path-to-live checklist with auto-detected status."""
    from risk.live_gate import CALIBRATION_TRADE_COUNT, is_live_trading_enabled

    checks: list[dict] = []

    live_flag = is_live_trading_enabled(env)
    checks.append({
        "key": "live_flag",
        "name": "LIVE_TRADING env flag",
        "status": "done" if live_flag else "todo",
        "detail": (
            "Set to 'true' — real broker construction allowed."
            if live_flag
            else "Not set — bot stays paper-only. Set LIVE_TRADING=true in .env "
            "when you're ready to wire a real broker."
        ),
    })

    has_kraken = _has_module("execution.ccxt_kraken")
    checks.append({
        "key": "real_broker",
        "name": "Real broker adapter (Kraken via CCXT)",
        "status": "done" if has_kraken else "todo",
        "detail": (
            "execution/ccxt_kraken.py present."
            if has_kraken
            else "Only PaperBroker exists. Build KrakenBroker implementing Broker Protocol."
        ),
    })

    has_reconcile = _has_module("execution.reconcile")
    last_reconcile = next(
        (r for r in reversed(rows) if r["event_type"] == "reconcile"), None
    )
    if has_reconcile and last_reconcile is not None:
        reconcile_status = "done"
        reconcile_detail = f"Last run on loop start: {last_reconcile['rationale'] or 'no detail'}"
    elif has_reconcile:
        reconcile_status = "done"
        reconcile_detail = (
            "execution/reconcile.py present. Runs on every build_from_env() — "
            "first run will appear in the decision log next loop start."
        )
    else:
        reconcile_status = "todo"
        reconcile_detail = (
            "Pulls open orders + positions from broker on start, halts new orders "
            "on mismatch. Implement after Kraken broker."
        )
    checks.append({
        "key": "reconcile",
        "name": "Reconcile-on-startup (P-11)",
        "status": reconcile_status,
        "detail": reconcile_detail,
    })

    has_human_gate = _has_module("risk.human_gate")
    if has_human_gate:
        from risk.human_gate import DEFAULT_TOKEN_PATH, get_session_state

        gs = get_session_state(DEFAULT_TOKEN_PATH)
        if gs.is_active:
            remaining_h = (gs.seconds_remaining or 0) // 3600
            gate_state_str = f"ACTIVE — {remaining_h}h remaining"
        else:
            gate_state_str = "INACTIVE — live broker refuses to start"
        human_gate_status = "done"
    else:
        human_gate_status = "todo"
        gate_state_str = "not built"
    checks.append({
        "key": "human_gate",
        "name": "Per-session human 'go live' gate",
        "status": human_gate_status,
        "detail": (
            f"Settings → Live session → type LIVE to activate, expires after 24h. "
            f"Current state: {gate_state_str}."
        ),
    })

    fills = [r for r in rows if r["event_type"] == "order_filled"]
    checks.append({
        "key": "calibration",
        "name": f"Calibration mode (first {CALIBRATION_TRADE_COUNT} trades tagged)",
        "status": "done",
        "detail": (
            f"Live broker uses 'live_calibration' mode for first "
            f"{CALIBRATION_TRADE_COUNT} trades, then 'live'. {len(fills)} fills logged."
        ),
    })

    tg_token = env.get("TELEGRAM_BOT_TOKEN", "").strip()
    tg_chat = env.get("TELEGRAM_CHAT_ID", "").strip()
    tg_ok = bool(tg_token and tg_chat)
    checks.append({
        "key": "telegram",
        "name": "Telegram alerts configured",
        "status": "done" if tg_ok else "todo",
        "detail": (
            "Bot can notify on fills, kill-switch events, and crashes."
            if tg_ok
            else "Settings → Telegram alerts. Optional for paper, mandatory before live."
        ),
    })

    has_live_caps = False
    cap_detail = ""
    try:
        from risk.caps import live_calibration_caps

        try:
            initial_cash_for_panel = float(env.get("TRADERBOT_INITIAL_CASH", "100") or "100")
        except (TypeError, ValueError):
            initial_cash_for_panel = 100.0
        cal = live_calibration_caps(initial_cash_usd=initial_cash_for_panel)
        cap_detail = (
            f"Live calibration preset @ ${initial_cash_for_panel:.0f}: "
            f"max ${cal.max_position_notional_usd:.2f}/trade · "
            f"daily-loss kill ${cal.max_daily_loss_usd:.2f} · "
            f"DD halt {cal.max_daily_drawdown_pct * 100:.1f}% · "
            f"{cal.max_concurrent_positions} concurrent."
        )
        has_live_caps = True
    except Exception:  # noqa: BLE001
        has_live_caps = False
    checks.append({
        "key": "live_caps",
        "name": "Hardened risk caps for live",
        "status": "done" if has_live_caps else "todo",
        "detail": (
            cap_detail
            if has_live_caps
            else "Existing caps are paper-tuned. Live needs absolute-dollar position cap, "
            "tighter daily-DD kill, and a first-trade-of-day delay."
        ),
    })

    try:
        from tools.system_check import check_readiness as _mac_check

        mac_report = _mac_check()
        if not mac_report.is_macos or not mac_report.pmset_available:
            mac_status = "todo"
            mac_detail = mac_report.summary
        elif mac_report.is_clean:
            mac_status = "done"
            settings_str = ", ".join(f"{c.name}={c.actual}" for c in mac_report.checks)
            mac_detail = f"All 5 settings correct: {settings_str}."
        else:
            mac_status = "todo"
            failing = ", ".join(
                f"{c.name}={c.actual}→{c.expected}"
                for c in mac_report.checks if not c.passed
            )
            mac_detail = f"{failing}. Run: {mac_report.remediation}"
    except Exception as e:  # noqa: BLE001
        mac_status = "todo"
        mac_detail = f"check failed: {type(e).__name__}: {e}"
    checks.append({
        "key": "mac_mini",
        "name": "Mac Mini 24/7 prep (pmset settings)",
        "status": mac_status,
        "detail": mac_detail,
    })

    try:
        soak_target_h = int(env.get("TRADERBOT_SOAK_TARGET_HOURS", "12") or "12")
    except (TypeError, ValueError):
        soak_target_h = 12
    soak_target_s = soak_target_h * 3600
    if loop_started_at_ms and loop_running:
        soak_elapsed_s = max(0, (now_ms - loop_started_at_ms) // 1000)
        soak_pct = min(100, soak_elapsed_s * 100 // soak_target_s)
        soak_h = soak_elapsed_s // 3600
        if soak_elapsed_s >= soak_target_s:
            soak_status = "done"
            soak_detail = f"{soak_target_h}h soak complete — {soak_h}h elapsed."
        else:
            soak_status = "in_progress"
            soak_detail = (
                f"{soak_h}h / {soak_target_h}h elapsed ({soak_pct}%). "
                f"Override: TRADERBOT_SOAK_TARGET_HOURS in .env."
            )
    else:
        soak_status = "todo"
        soak_detail = "Loop not running — start it from Settings."
    checks.append({
        "key": "soak",
        "name": f"{soak_target_h}h continuous paper soak",
        "status": soak_status,
        "detail": soak_detail,
    })

    checks.append({
        "key": "decision_log",
        "name": "Decision log audit trail (append-only SQLite)",
        "status": "done",
        "detail": f"{len(rows):,} rows logged. UPDATE/DELETE blocked by triggers.",
    })

    checks.append({
        "key": "coid",
        "name": "Idempotent client_order_id",
        "status": "done",
        "detail": (
            "make_client_order_id(strategy_id, signal_id) gives every order a "
            "deterministic ID — retries can't double-fill."
        ),
    })

    return checks


def calibration_fill_count(rows: list[dict]) -> int:
    """Count fills tagged mode='live_calibration' in metadata_json (S-55 promotion gate)."""
    count = 0
    for r in rows:
        if r["event_type"] != "order_filled":
            continue
        raw = r.get("metadata_json")
        if not raw:
            continue
        try:
            meta = json.loads(raw)
        except (TypeError, ValueError):
            continue
        if isinstance(meta, dict) and meta.get("mode") == "live_calibration":
            count += 1
    return count
