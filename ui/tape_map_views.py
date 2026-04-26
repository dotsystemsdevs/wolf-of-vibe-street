"""TAPE = dense decision-log data grid. MAP = one-screen system map (vibe, clarity)."""

from __future__ import annotations

import json

import pandas as pd
import streamlit as st  # noqa: E402  — app context

_DEFAULT_LIMIT = 5000


def _row_to_tape_dict(r: dict) -> dict:
    ts = int(r.get("timestamp_ms", 0))
    utc = pd.to_datetime(ts, unit="ms", utc=True) if ts else None
    meta = r.get("metadata_json")
    mode = ""
    if meta:
        try:
            o = json.loads(meta) if isinstance(meta, str) else {}
            if isinstance(o, dict):
                mode = str(o.get("mode", "") or "")
        except (TypeError, ValueError):
            mode = ""
    rat = r.get("rationale") or ""
    if len(rat) > 120:
        rat = rat[:117] + "…"
    return {
        "id": r.get("id"),
        "t (UTC)": utc.strftime("%Y-%m-%d %H:%M:%S") if utc is not None else "",
        "event": r.get("event_type", ""),
        "symbol": r.get("symbol", ""),
        "side": r.get("side") or "",
        "strategy": r.get("strategy_id", ""),
        "price": r.get("price"),
        "qty": r.get("quantity"),
        "pnl": r.get("pnl"),
        "coid": (r.get("client_order_id") or "")[:16],
        "mode": mode,
        "rationale": rat,
    }


def _event_types_from_rows(rows: list[dict]) -> list[str]:
    s = {str(r.get("event_type", "")) for r in rows if r.get("event_type")}
    return sorted(s)


def render_tape_tab(rows: list[dict], *, log_path_str: str) -> None:
    """Scrollable, filterable, Excel-dense view of the append-only decision log."""
    st.caption(
        f"Same SQLite as `{log_path_str}` — max ~{_DEFAULT_LIMIT:,} rows shown (newest last)."
    )
    if not rows:
        st.info("Ingen data i loggen ännu.")
        return

    types = _event_types_from_rows(rows)
    pick = st.multiselect("Visa händelsetyp", options=types, default=types, key="tape_event_filter")
    if not pick:
        st.warning("Välj minst en händelsetyp.")
        return

    filtered = [r for r in rows if str(r.get("event_type", "")) in set(pick)]
    tail = filtered[-_DEFAULT_LIMIT:]

    df = pd.DataFrame(_row_to_tape_dict(r) for r in tail)
    h = min(720, 120 + int(min(len(df), 100)) * 28)
    st.dataframe(
        df,
        use_container_width=True,
        height=h,
        hide_index=True,
        column_config={
            "id": st.column_config.NumberColumn("id", width="small"),
            "t (UTC)": st.column_config.TextColumn("t (UTC)", width="medium"),
            "event": st.column_config.TextColumn("event", width="small"),
            "symbol": st.column_config.TextColumn("symbol", width="small"),
            "side": st.column_config.TextColumn("side", width="small"),
            "strategy": st.column_config.TextColumn("strategy", width="small"),
            "price": st.column_config.NumberColumn("price", format="%.6f", width="small"),
            "qty": st.column_config.NumberColumn("qty", format="%.6f", width="small"),
            "pnl": st.column_config.NumberColumn("pnl", format="%.2f", width="small"),
            "coid": st.column_config.TextColumn("coid", width="small"),
            "mode": st.column_config.TextColumn("mode", width="small"),
            "rationale": st.column_config.TextColumn("rationale", width="large"),
        },
    )
    n_all, n_f = len(rows), len(tail)
    st.caption(f"Showing {n_f} of {n_all} events (filter: {len(filtered)}).")


def render_map_tab() -> None:
    """One-line pipeline: where data goes (no interactivity)."""
    st.caption("Static only — use DESK and sidebar to operate.")
    st.markdown(
        '<p style="font-size:1.05rem; line-height:1.7; color:#a8a8a8; margin:4px 0 0 0; '
        'max-width:42em;">'
        'Bars on disk <span style="color:#6b6b6b;">→</span> '
        'strategy / optional LLM <span style="color:#6b6b6b;">→</span> risk '
        '<span style="color:#6b6b6b;">→</span> executor <span style="color:#6b6b6b;">→</span> '
        'broker (paper or Kraken) <span style="color:#6b6b6b;">→</span> '
        '<span style="color:#f0f0f0;">SQLite log</span> '
        '<span style="color:#6b6b6b;">→</span> this app &amp; Telegram'
        "</p>",
        unsafe_allow_html=True,
    )
