"""Backtest compare tab — extracted from the main dashboard for layout clarity."""

from __future__ import annotations

import streamlit as st  # noqa: E402  — app context

from backtest.compare import (  # noqa: E402
    DEFAULT_SYMBOLS,
    STRATEGIES,
    make_figure,
    run_comparison,
    strategy_by_label,
)


def render_compare_tab() -> None:
    """Multi-symbol backtest: same strategies as the live `STRATEGIES` registry."""
    st.caption(
        "Offline only — no live orders. Same strategy list as the loop; uses local Parquet cache when possible."
    )

    c1, c2, c3, c4 = st.columns([3, 1, 1, 2])
    symbols_str = c1.text_input(
        "Symbols (comma-separated)",
        ", ".join(DEFAULT_SYMBOLS),
        key="compare_symbols",
    )
    days = c2.number_input("Days", min_value=7, max_value=180, value=30, step=1, key="compare_days")
    timeframe = c3.selectbox("Timeframe", ["1h", "4h", "1d"], index=0, key="compare_tf")
    strategy_label = c4.selectbox(
        "Strategy",
        [e.label for e in STRATEGIES.values()],
        index=0,
        key="compare_strategy",
    )

    if st.button("Run comparison", type="primary", key="compare_run_btn"):
        symbols = [s.strip() for s in symbols_str.split(",") if s.strip()]
        if not symbols:
            st.error("Need at least one symbol.")
            return
        entry = strategy_by_label(strategy_label)
        with st.spinner(f"Backfilling + backtesting {len(symbols)} symbols on {strategy_label}..."):
            try:
                results = run_comparison(
                    symbols,
                    days=int(days),
                    timeframe=timeframe,
                    strategy_fn=entry.fn,
                )
            except Exception as e:
                st.error(f"Comparison failed: {type(e).__name__}: {e}")
                return
        st.session_state["compare_results"] = results
        st.session_state["compare_results_strategy"] = strategy_label

    results = st.session_state.get("compare_results")
    if results is None:
        st.info("Set parameters and click **Run comparison**.")
        return

    st.plotly_chart(make_figure(results), config={"displayModeBar": False}, width="stretch")

    shown_strategy = st.session_state.get("compare_results_strategy", "Baseline EMA-cross")
    st.markdown(
        f'<div class="section-title">Per-symbol breakdown · {shown_strategy}</div>',
        unsafe_allow_html=True,
    )
    rows_html: list[str] = []
    for r in results:
        m = r.result.metrics
        strat_pct = m["total_return_pct"] * 100
        diff = strat_pct - r.buy_hold_return_pct
        diff_cls = "pos" if diff > 0 else ("neg" if diff < 0 else "muted")
        strat_cls = "pos" if strat_pct > 0 else ("neg" if strat_pct < 0 else "muted")
        exp = float(m.get("expectancy", 0.0))
        exp_cls = "pos" if exp > 0 else ("neg" if exp < 0 else "muted")
        pf = float(m.get("profit_factor", 0.0))
        pf_str = "∞" if pf == float("inf") else f"{pf:.2f}"
        pf_cls = "pos" if pf > 1.5 else ("neg" if pf < 1.0 else "muted")
        rows_html.append(
            "<tr>"
            f"<td><strong>{r.symbol}</strong></td>"
            f'<td class="num">{r.bars}</td>'
            f'<td class="num">{int(m["num_trades"])}</td>'
            f'<td class="num">{m["win_rate"] * 100:.1f}%</td>'
            f'<td class="num {exp_cls}">${exp:+.2f}</td>'
            f'<td class="num {pf_cls}">{pf_str}</td>'
            f'<td class="num {strat_cls}"><strong>{strat_pct:+.2f}%</strong></td>'
            f'<td class="num muted">{r.buy_hold_return_pct:+.2f}%</td>'
            f'<td class="num {diff_cls}">{diff:+.2f}pp</td>'
            f'<td class="num muted">{m["sharpe"]:+.2f}</td>'
            f'<td class="num muted">{m["max_drawdown"] * 100:.2f}%</td>'
            "</tr>"
        )
    st.markdown(
        '<table class="t">'
        "<thead><tr>"
        "<th>Symbol</th><th>Bars</th><th>Trades</th><th>WR</th>"
        "<th>Exp/trade</th><th>PF</th>"
        "<th>Strategy</th><th>B&amp;H</th><th>Diff</th>"
        "<th>Sharpe</th><th>MaxDD</th>"
        "</tr></thead><tbody>" + "".join(rows_html) + "</tbody></table>",
        unsafe_allow_html=True,
    )
