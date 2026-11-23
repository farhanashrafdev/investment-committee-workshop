"""Page 2: "Ask the Committee", a simple public-facing page for goal-based questions.

The user writes something like "I have 5 lakh rupees and want 30% in 2 years".
A deterministic parser extracts the numbers, the user confirms them, a
deterministic goal check compares the target with the risk-free rate, and then
the agent committee reviews candidate companies. The answer is educational,
not personalized advice.
"""

from __future__ import annotations

import altair as alt
import pandas as pd
import streamlit as st

from committee.config import get_settings
from security.guardrails import build_checker
from security.input_validation import InputValidationError, build_request, validate_question
from tools.formatting import fmt_money, fmt_pkr_crore
from tools.planning_tools import assess_return_target, parse_goal_question
from ui.common import REC_STYLE, hero, is_running, mode_badge, poll_run, sidebar, start_run
from ui.report_view import live_progress, report_section

SLOT = "ask"
EXAMPLES = [
    "I have 5 lakh rupees and want 30% return in 2 years. Pakistani stocks, low risk.",
    "Mere paas 10 lac rupay hain, 25% chahiye 3 saal mein, local market.",
    "I have $10,000 and want to double it in 5 years with US tech stocks.",
    "PKR 2 crore for 5 years, moderate risk, mix of Pakistan and international stocks.",
]
UNIVERSE = {"PK": ["SYS", "OGDC", "LUCK"], "US": ["NVDA", "MSFT", "AMZN"], "BOTH": ["NVDA", "SYS", "OGDC"]}
BAND_STYLE = {
    "low_risk_possible": ("green", "Realistic with low-risk options"),
    "requires_equity_risk": ("orange", "Needs stock-market risk"),
    "high_risk": ("red", "High risk"),
    "speculative": ("red", "Speculative: treat as a warning sign"),
}


def _money(v: float, ccy: str) -> str:
    return f"{fmt_money(v, ccy)} ({fmt_pkr_crore(v)})" if ccy == "PKR" else fmt_money(v, ccy)


def _short(v: float, ccy: str) -> str:
    """Compact value for metric cards (lakh/crore for PKR, as Pakistani readers expect)."""
    return fmt_pkr_crore(v) if ccy == "PKR" else fmt_money(v, ccy)


def _goal_card(goal: dict) -> None:
    color, label = BAND_STYLE[goal["band"]]
    rf = goal["risk_free_benchmark"]
    st.markdown(f"#### Is your target realistic? :{color}[**{label}**]")
    st.write(goal["assessment"])
    c1, c2, c3 = st.columns(3)
    c1.metric(f"Your target after {goal['horizon_years']} yrs", _short(goal["target_ending_value"], goal["currency"]), f"{goal['target_return_pct']}% / yr",
              help=_money(goal["target_ending_value"], goal["currency"]))
    c2.metric("Risk-free path", _short(goal["risk_free_ending_value"], goal["currency"]), f"{rf['rate_pct']}% / yr",
              help=_money(goal["risk_free_ending_value"], goal["currency"]))
    c3.metric("Extra return you are asking for", f"{goal['excess_return_needed_pp']:+.2f} pp", help="Percentage points above the risk-free benchmark")
    years = list(range(goal["horizon_years"] + 1))
    df = pd.DataFrame(
        [{"year": y, "path": "your target", "value": goal["budget"] * (1 + goal["target_return_pct"] / 100) ** y} for y in years]
        + [{"year": y, "path": f"risk-free ({rf['name']})", "value": goal["budget"] * (1 + rf["rate_pct"] / 100) ** y} for y in years]
    )
    st.altair_chart(alt.Chart(df).mark_line(point=True).encode(x="year:O", y=alt.Y("value:Q", title=goal["currency"]), color="path:N"),
                    width="stretch")
    st.caption(f"Benchmark: {rf['name']} {rf['rate_pct']}% as of {rf['as_of']} (source: {rf['source_id']}). " + " ".join(goal["caveats"]))


def _plain_answer(run) -> None:
    final = run.final
    r = final.report
    fg, bg = REC_STYLE[r.recommendation]
    simple = {
        "INVEST": f"The committee would consider putting about {r.proposed_allocation_pct:.0f}% of your money "
                  f"({_money(final.allocation_amount, final.currency)}) into {r.selected_ticker}, and keeping the rest diversified.",
        "HOLD": "The committee would not put money into these stocks right now. It would wait for more evidence.",
        "REJECT": "The committee would not invest in these stocks for this goal.",
    }[r.recommendation]
    st.markdown(f'<div class="rec-banner" style="background:{bg};border:2px solid {fg}"><div class="rec" style="color:{fg}">'
                f'Short answer: {r.recommendation} {r.selected_ticker or ""}</div><div>{simple}</div></div>', unsafe_allow_html=True)
    if r.goal_assessment:
        st.markdown("**About your target:** " + r.goal_assessment)
    st.markdown("**What the committee found:** " + r.executive_summary)
    st.markdown("**Biggest risks:**")
    for x in r.key_risks[:4]:
        st.markdown(f"- {x}")
    st.markdown("**Before you invest:**")
    for x in [
        "Keep an emergency fund in a safe place before buying any stock.",
        "Do not put all your money into one company or one market.",
        "Compare any expected return with the risk-free rate shown above.",
        "Ignore 'guaranteed' tips from social media or WhatsApp groups (see the Security Lab).",
        "Talk to a licensed financial adviser (SECP-regulated in Pakistan) about your full situation, taxes and any Shariah requirements.",
    ]:
        st.markdown(f"- {x}")
    st.caption(final.disclaimer)


def render() -> None:
    mode = sidebar()
    hero("💬 Ask the Committee", "Tell us how much you have, the return you hope for and your time frame, in English or Roman Urdu.")
    st.markdown("**Try an example:**")
    pick = st.pills("examples", EXAMPLES, label_visibility="collapsed", key="ask_example")
    text = st.text_area("Your question", value=pick or "", max_chars=600, height=90,
                        placeholder="e.g. I have 5 lakh rupees and want 30% return in 2 years. What should I do?")

    if st.button("1 · Understand my question", type="primary"):
        try:
            clean, scan = validate_question(text)
        except InputValidationError as exc:
            st.error(str(exc))
            return
        checker = build_checker(get_settings())
        verdict = checker.check(clean) if checker else None
        if scan.suspicious or (verdict and verdict.intervened):
            st.error("This question looks like an attempt to change the assistant's instructions, so it was not processed. "
                     f"(deterministic flags: {', '.join(scan.flags) or 'none'}; guardrail: {verdict.action if verdict else 'not configured'})")
            return
        st.session_state["ask_parsed"] = {"text": clean, **parse_goal_question(clean)}
        st.session_state.pop(f"run_{SLOT}", None)

    parsed = st.session_state.get("ask_parsed")
    if not parsed:
        st.info("Nothing is sent to an AI model until you confirm the details in step 2.")
        return

    st.markdown("#### 2 · Check what we understood")
    st.caption("These values were extracted with fixed rules (no AI). Correct anything that is wrong.")
    for n in parsed["notes"]:
        st.caption(f"ℹ {n}")
    c1, c2, c3, c4, c5 = st.columns(5)
    currency = c1.selectbox("Currency", ["PKR", "USD"], index=0 if (parsed["currency"] or "PKR") == "PKR" else 1)
    amount = c2.number_input("Amount", min_value=1.0, value=float(parsed["amount"] or (500_000 if currency == "PKR" else 10_000)), step=1000.0, format="%.0f")
    target = c3.number_input("Target return % per year", min_value=0.0, max_value=100.0, value=float(parsed["target_return_pct"] or 15.0), step=0.5)
    horizon = c4.number_input("Years", min_value=1, max_value=30, value=int(parsed["horizon_years"] or 3))
    risk = c5.selectbox("Risk comfort", ["conservative", "moderate", "aggressive"],
                        index=["conservative", "moderate", "aggressive"].index(parsed["risk_tolerance"] or "moderate"))
    markets = parsed["markets"] or (["PK"] if currency == "PKR" else ["US"])
    key = "BOTH" if len(set(markets)) > 1 else markets[0]
    tickers = st.multiselect("Companies the committee will review (max 3)", ["NVDA", "MSFT", "AMZN", "SYS", "OGDC", "LUCK"],
                             default=UNIVERSE[key], max_selections=3)

    goal = assess_return_target(amount, currency, target, int(horizon))
    with st.container(border=True):
        _goal_card(goal)

    if st.button("3 · Ask the AI committee", type="primary", disabled=is_running(SLOT) or not tickers):
        try:
            req, _ = build_request(tickers=tickers, budget=amount, currency=currency, risk_tolerance=risk, horizon_years=int(horizon),
                                   question=parsed["text"], target_return_pct=target, investor_country="PK" if currency == "PKR" else "OTHER")
        except InputValidationError as exc:
            st.error(str(exc))
            return
        start_run(SLOT, req, mode)

    area = st.container()
    if is_running(SLOT):
        poll_run(SLOT, live_progress(area))
        st.rerun()
    run = st.session_state.get(f"run_{SLOT}")
    if run and run.final:
        st.markdown(f"#### Committee answer &nbsp; {mode_badge(run.mode)}", unsafe_allow_html=True)
        _plain_answer(run)
        with st.expander("See the full committee report and the agents' work"):
            report_section(run)
    elif run:
        st.error(run.error or "The committee could not produce an answer.")
