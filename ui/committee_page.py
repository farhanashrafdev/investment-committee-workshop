"""Page 1: the Investment Committee dashboard (sections A to E)."""

from __future__ import annotations

import streamlit as st

from committee.config import get_settings
from security.guardrails import build_checker
from security.input_validation import InputValidationError, build_request
from tools.dataset import load_dataset
from ui.common import companies_table, hero, is_running, poll_run, sidebar, start_run
from ui.report_view import approval_section, execution_section, financial_section, live_progress, report_section

SLOT = "committee"
DEFAULT_Q = ("Our investment committee has {budget} available. Evaluate {names} using the provided financial dataset. "
             "Should we invest, hold, or reject the opportunity?")


def render() -> None:
    mode = sidebar()
    hero("🏛️ The Investment Committee",
         "Four AI agents on Amazon Bedrock (Strands Agents) research, calculate, challenge and recommend. A human makes the final call.")
    ds = load_dataset()
    table = companies_table()

    # -- Section A: investment request ---------------------------------------
    st.subheader("A · Investment request")
    options = table["ticker"].tolist()
    labels = {r.ticker: f"{r.ticker} · {r.name} ({r.market})" for r in table.itertuples()}
    tickers = st.multiselect("Companies (up to 3): international and Pakistan Stock Exchange", options, default=["NVDA"],
                             format_func=lambda t: labels[t], max_selections=3, key="tickers_committee")
    with st.form("request_form"):
        c3, c4, c5, c6, c7 = st.columns(5)
        currency = c3.radio("Budget currency", ["USD", "PKR"], horizontal=True)
        budget = c4.number_input("Investment budget", min_value=1.0, max_value=1e12, value=100_000.0, step=1_000.0, format="%.0f")
        risk = c5.selectbox("Risk tolerance", ["conservative", "moderate", "aggressive"], index=1)
        horizon = c6.slider("Horizon (years)", 1, 15, 5)
        country = c7.selectbox("Investor based in", ["PK", "US", "OTHER"], format_func={"PK": "Pakistan", "US": "United States", "OTHER": "Other"}.get)
        question = st.text_area("Committee question (leave empty to use the standard question)", max_chars=1500,
                                placeholder=DEFAULT_Q.format(budget="<budget>", names=", ".join(tickers) or "<companies>"))
        with st.expander("Optional: dated share prices for US companies (the dataset has no live US prices)"):
            st.caption("Without a price the committee reports valuation as missing evidence. Enter a price you looked up, with its date and source.")
            prices = {}
            for tk in [t for t in tickers if ds.company(t).market == "US"]:
                p1, p2, p3 = st.columns(3)
                price = p1.number_input(f"{tk} price (USD)", min_value=0.0, value=0.0, key=f"px_{tk}")
                as_of = p2.text_input(f"{tk} price date", key=f"pxd_{tk}", placeholder="2026-11-20")
                src = p3.text_input(f"{tk} price source", key=f"pxs_{tk}", placeholder="Nasdaq official close")
                if price > 0:
                    prices[tk] = {"price": price, "as_of": as_of, "source": src}
        untrusted = st.checkbox("Attach untrusted third-party documents (security demo: they contain prompt injection)", value=False)
        submitted = st.form_submit_button("🚀 Analyze with the committee", type="primary", disabled=is_running(SLOT))

    if submitted:
        question = question.strip() or DEFAULT_Q.format(budget=f"{budget:,.0f} {currency}", names=", ".join(tickers))
        try:
            req, scan = build_request(tickers=tickers, budget=budget, currency=currency, risk_tolerance=risk, horizon_years=horizon,
                                      question=question, investor_country=country, price_inputs=prices, include_untrusted_documents=untrusted)
        except InputValidationError as exc:
            st.error(f"Input rejected by validation: {exc}")
            return
        if scan.suspicious:
            checker = build_checker(get_settings())
            verdict = checker.check(question) if checker else None
            st.warning(f"Deterministic scan flagged the question: {', '.join(scan.flags)}."
                       + (f" Guardrail verdict: {verdict.action}." if verdict else ""))
            if verdict and verdict.intervened:
                st.error("Blocked: Amazon Bedrock Guardrails classified the question as a prompt attack.")
                return
        st.session_state.pop(f"run_{SLOT}", None)
        st.session_state[f"req_{SLOT}"] = req
        start_run(SLOT, req, mode)

    # -- Section B: agent execution ------------------------------------------
    st.subheader("B · Agent execution")
    area = st.container()
    if is_running(SLOT):
        poll_run(SLOT, live_progress(area))
        st.rerun()
    run = st.session_state.get(f"run_{SLOT}")
    if run is None:
        area.caption("Agents are Waiting. Submit a request to watch the supervisor delegate and the specialists call tools.")
    else:
        with area:
            execution_section(run)

    # -- Section C: financial analysis ---------------------------------------
    st.subheader("C · Financial analysis")
    st.caption("Computed by deterministic Python tools from the sourced dataset. These numbers do not come from the language model.")
    financial_section(run.request.tickers if run else tickers)

    # -- Section D / E -------------------------------------------------------
    if run is not None:
        st.subheader("D · Investment committee report")
        report_section(run)
        st.subheader("E · Human approval")
        revision = approval_section(run, SLOT)
        if revision:
            req = run.request.model_copy(update={"revision_notes": [*run.request.revision_notes, revision][-3:]})
            st.session_state.pop(f"run_{SLOT}", None)
            start_run(SLOT, req, run.mode)
            st.rerun()
