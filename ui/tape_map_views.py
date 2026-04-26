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
    rat = (r.get("rationale") or "")
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
        f"**TAPE** — samma rader som i SQLite `{log_path_str}`. Upp till "
        f"{_DEFAULT_LIMIT:,} senaste raderna (nyast sist). Ingen skrivrätt."
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
    st.caption(f"Rader: **{n_f}** i tabellen  (filtrerat från {len(filtered)}  ·  {n_all} totalt i logg)")


def render_map_tab() -> None:
    """Single-screen mental model: data → brain → risk → paper/Kraken → log → du."""
    st.caption("**MAP** — en sida, hela flödet. Ingen interaktion här, bara orientering.")

    st.markdown(
        """
<div class="section-title" style="margin-top:0;">Dataflöde (vad som händer i sekunder)</div>
""",
        unsafe_allow_html=True,
    )

    ascii_map = r"""
  ┌─────────────┐     ┌──────────┐     ┌─────────────────┐     ┌────────────┐
  │ Binance     │     │ Parquet  │     │ EMA/RSI/ATR     │     │ SELL/BUY/  │
  │ OHLCV (REST)├────►│ bars on  ├────►│ strategies/*.py ├────►│ HOLD +     │
  │ (publik)   │     │ disk     │     │ + valfri LLM    │     │ conviction │
  └─────────────┘     └──────────┘     └────────┬────────┘     └──────┬─────┘
                                            │                        │
  ┌─────────────┐     ┌──────────┐         │                        ▼
  │ Risk caps   │     │Executor  │◄────────┘                 ┌──────────────┐
  │ kill / DD  │     │on_bar    │                          │ PaperBroker  │
  │ 25% 5% 30T  ├────►│           ├─────────────────────────►eller Kraken  │
  └─────────────┘     └─────┬────┘                          └──────┬───────┘
                            │                                        │
                            ▼                                        ▼
                     ┌────────────┐                            ┌───────────┐
                     │ decision_  │  append-only            │ Dashboard │
                     │ log SQLite │  (trigger-låst)            │  du läser  │
                     └─────┬──────┘                            └─────┬─────┘
                           │                                         │
                           └────────────────TELEGRAM───────────────────┘
"""
    st.text(ascii_map)

    mermaid = """
```mermaid
flowchart TB
  subgraph in["Data in"]
    B[Binance OHLCV] --> P[(Parquet)]
    P --> F[Features EMA/RSI/ATR]
  end
  subgraph think["Strategi"]
    F --> S[signals]
    S -->|valfri| L[Claude LLM filter]
  end
  think --> R[Risk caps + kill switch]
  R --> X[Executor]
  X --> BK[Broker / paper eller Kraken]
  BK --> D[(decision log SQLite)]
  D --> U[Streamlit + Telegram]
```

Klistra in i <https://mermaid.live> om du vill pilla på diagrammet.
""".strip()
    st.markdown(mermaid)

    st.markdown(
        """
<div class="section-title">Tre lager (minnesregel)</div>
<ul style="color:#a8a8a8; font-size:12px; line-height:1.5; max-width:700px; font-family:Oswald,sans-serif; letter-spacing:0.06em;">
  <li><b style="color:#f0f0f0;">1 · SANNING</b> — append-only <code>decision_log</code> (samma rader som <b>TAPE</b>)</li>
  <li><b style="color:#f0f0f0;">2 · HJÄRNA</b> — strategi + (valfri) LLM-filtrering, <i>före</i> risk</li>
  <li><b style="color:#f0f0f0;">3 · PENGAR</b> — <code>Executor</code> + broker, alltid caps + kill</li>
</ul>
""",
        unsafe_allow_html=True,
    )
