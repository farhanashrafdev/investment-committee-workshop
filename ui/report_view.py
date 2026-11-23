"""Rendering for agent execution, financial analysis, the committee report and human approval."""

from __future__ import annotations

import json

import altair as alt
import pandas as pd
import streamlit as st

from committee.approval import record_decision
from committee.orchestrator import CommitteeRun
from committee.report import generate_investment_report
from observability.events import EventRecorder
from tools.dataset import load_dataset
from tools.formatting import fmt_money, fmt_pct, fmt_pkr_crore
from ui.common import REC_STYLE, agent_cards, metrics_for, mode_badge, timeline_frame


# ---------------------------------------------------------------------------
# Section B: agent execution
# ---------------------------------------------------------------------------

def live_progress(area):
    """Returns a render(recorder, mode) callback that draws into fixed placeholders."""
    header = area.empty()
    cards = area.container()
    cards_slot = cards.empty()
    table = area.empty()

    def render(recorder: EventRecorder, mode: str) -> None:
        events, statuses = recorder.snapshot()
        header.markdown(f"{mode_badge(mode)} &nbsp; run `{recorder.run_id}` · {len(events)} events", unsafe_allow_html=True)
        with cards_slot.container():
            agent_cards(st, statuses, events)
        if events:
            table.dataframe(timeline_frame(events).tail(14), hide_index=True, width="stretch")

    return render


def execution_section(run: CommitteeRun) -> None:
    st.markdown(f"{mode_badge(run.mode)} &nbsp; run `{run.run_id}` · {run.duration_s}s · {len(run.events)} events",
                unsafe_allow_html=True)
    agent_cards(st, run.agent_statuses, run.events)
    if run.mode == "mock":
        st.warning("MOCK MODE: agent decisions and wording are scripted. Numbers come from the real Python tools. No Bedrock call was made.")
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Tool calls", sum(1 for e in run.events if e.kind == "tool_call"))
    c2.metric("Delegations", sum(1 for e in run.events if e.kind == "delegation"))
    c3.metric("Tokens (Bedrock-reported)", f"{run.total_tokens:,}" if run.total_tokens else "n/a")
    c4.metric("Estimated cost", f"${run.total_cost_usd:.4f}" if run.total_cost_usd is not None else "not configured" if run.mode == "live" else "n/a")

    st.markdown("**Delegation and tool timeline**")
    st.dataframe(timeline_frame(run.events), hide_index=True, width="stretch", height=360,
                 column_config={"detail": st.column_config.TextColumn(width="large")})
    tool_events = [e for e in run.events if e.kind in ("tool_call", "tool_result", "security", "policy")]
    if tool_events:
        with st.expander("Inspect a tool call (inputs and outputs)"):
            labels = [f"#{e.seq} {e.agent} · {e.tool_name or e.kind} · {e.status or ''}" for e in tool_events]
            choice = st.selectbox("Event", range(len(tool_events)), format_func=lambda i: labels[i])
            e = tool_events[choice]
            st.json({"agent": e.agent, "tool": e.tool_name, "status": e.status, "duration_ms": e.duration_ms,
                     "input": e.tool_input, "output_preview": e.output_preview, "simulated": e.simulated})
    if run.usage:
        st.markdown("**Model usage per agent invocation**")
        st.dataframe(pd.DataFrame([{"agent": u["agent"], "model": u["model_id"], "input tokens": u["tokens"].get("inputTokens"),
                                    "output tokens": u["tokens"].get("outputTokens"), "model latency ms": u["tokens"].get("latencyMs"),
                                    "cost USD": u["cost_usd"]} for u in run.usage]), hide_index=True, width="stretch")
    st.caption(f"Full event log: logs/run-{run.run_id}.jsonl. Model reasoning is not displayed; only tool calls, results and final outputs.")


# ---------------------------------------------------------------------------
# Section C: financial analysis (deterministic, available before any agent runs)
# ---------------------------------------------------------------------------

def financial_section(tickers: list[str]) -> None:
    if not tickers:
        st.info("Select at least one company.")
        return
    ds = load_dataset()
    m = metrics_for(tuple(tickers))
    cols = st.columns(len(tickers))
    for col, tk in zip(cols, tickers):
        c = ds.company(tk)
        g, mg = m["growth"][tk], m["margins"][tk]["latest"]
        with col.container(border=True):
            st.markdown(f"**{c.name}** ({tk} · {c.exchange})")
            st.metric(f"Revenue {c.latest.fiscal_year}", fmt_money(c.latest.revenue, c.currency), f"{fmt_pct(g['latest_yoy_growth_pct'])} YoY")
            if c.currency == "PKR":
                st.caption(fmt_pkr_crore(c.latest.revenue))
            a, b = st.columns(2)
            a.metric("Net margin", fmt_pct(mg["net_margin_pct"]))
            b.metric("Op. margin", fmt_pct(mg["operating_margin_pct"]))
            a.metric("FCF", fmt_money(mg["free_cash_flow"], c.currency))
            b.metric("Revenue CAGR", fmt_pct(g["cagr_pct"]))

    fx = ds.market_context.fx.rate
    rows = []
    for tk in tickers:
        c = ds.company(tk)
        to_usd = 1.0 if c.currency == "USD" else 1 / fx
        for f in c.financials:
            rows.append({"ticker": tk, "fiscal year": f.fiscal_year, "revenue (USD bn)": round(f.revenue * to_usd / 1e9, 2),
                         "net income (USD bn)": round((f.net_income or 0) * to_usd / 1e9, 2)})
    df = pd.DataFrame(rows)
    left, right = st.columns(2)
    with left:
        st.markdown("**Revenue by fiscal year (USD bn)**")
        st.altair_chart(alt.Chart(df).mark_bar().encode(
            x=alt.X("fiscal year:N", title=None), y=alt.Y("revenue (USD bn):Q"), color="ticker:N", xOffset="ticker:N",
            tooltip=list(df.columns)), width="stretch")
    with right:
        st.markdown("**Latest-year margins (%)**")
        mrows = []
        for tk in tickers:
            mg = m["margins"][tk]["latest"]
            for k, label in (("operating_margin_pct", "operating"), ("net_margin_pct", "net"), ("fcf_margin_pct", "FCF")):
                if mg[k] is not None:
                    mrows.append({"ticker": tk, "margin": label, "pct": mg[k]})
        mdf = pd.DataFrame(mrows)
        if not mdf.empty:
            st.altair_chart(alt.Chart(mdf).mark_bar().encode(
                x=alt.X("margin:N", title=None), y=alt.Y("pct:Q", title="%"), color="ticker:N", xOffset="ticker:N",
                tooltip=["ticker", "margin", "pct"]), width="stretch")
    if m["compare"] and len(tickers) > 1:
        comp = pd.DataFrame(m["compare"]["companies"])[["ticker", "latest_fy", "currency", "revenue_usd", "latest_yoy_growth_pct", "cagr_pct",
                                                        "operating_margin_pct", "net_margin_pct", "fcf_margin_pct", "pe"]]
        st.markdown("**Comparison (computed by `compare_companies`)**")
        st.dataframe(comp, hide_index=True, width="stretch")
        st.caption(" · ".join(m["compare"]["caveats"]) + f" USD/PKR {fx} (SBP, {ds.market_context.fx.as_of}).")
    with st.expander("Sources for these figures"):
        for tk in tickers:
            c = ds.company(tk)
            src = ds.sources[c.latest.source_id]
            st.markdown(f"- **{tk}**: [{src.title}]({src.url}) · retrieved {src.retrieved}")
            for gap in c.data_gaps:
                st.caption(f"  {tk} data gap: {gap}")


# ---------------------------------------------------------------------------
# Section D: committee report
# ---------------------------------------------------------------------------

def report_section(run: CommitteeRun) -> None:
    final = run.final
    if final is None:
        st.error(f"No report was produced. {run.error or ''}")
        return
    r = final.report
    fg, bg = REC_STYLE[r.recommendation]
    amount = fmt_money(final.allocation_amount, final.currency)
    if final.currency == "PKR" and final.allocation_amount:
        amount += f" ({fmt_pkr_crore(final.allocation_amount)})"
    st.markdown(
        f'<div class="rec-banner" style="background:{bg};border:2px solid {fg}">'
        f'<div class="rec" style="color:{fg}">{r.recommendation} {r.selected_ticker or ""}</div>'
        f'<div>Proposed allocation <b>{r.proposed_allocation_pct:.0f}%</b> of budget = <b>{amount}</b> · confidence <b>{r.confidence}</b> · '
        f'status <b>awaiting human approval</b></div></div>', unsafe_allow_html=True)
    if final.produced_by == "deterministic_fallback":
        st.warning("The live supervisor failed. This report was assembled by deterministic rules from the specialists' live findings.")
    if final.adjustments:
        st.info("**Deterministic adjustments applied after the model answered:**\n\n" + "\n".join(f"- {a}" for a in final.adjustments))

    st.markdown("#### Executive summary")
    st.write(r.executive_summary)
    a, b = st.columns(2)
    with a.container(border=True):
        st.markdown("#### 🟢 Bull case")
        for x in r.bull_case:
            st.markdown(f"- {x}")
    with b.container(border=True):
        st.markdown("#### 🔴 Bear case")
        for x in r.bear_case:
            st.markdown(f"- {x}")
    st.markdown("#### Financial assessment")
    st.write(r.financial_assessment)
    a, b = st.columns(2)
    with a:
        st.markdown("#### Key risks")
        for x in r.key_risks:
            st.markdown(f"- {x}")
        st.markdown("#### Missing evidence")
        for x in r.missing_evidence or ["None recorded"]:
            st.markdown(f"- {x}")
    with b:
        st.markdown("#### Specialist stances and disagreements")
        if final.specialist_stances:
            st.dataframe(pd.DataFrame([{"specialist": k, "stance": v} for k, v in final.specialist_stances.items()]),
                         hide_index=True, width="stretch")
        if final.stance_disagreement:
            st.caption("Deterministic check: specialists' stances differ.")
        for x in r.disagreements or ["None recorded"]:
            st.markdown(f"- {x}")
    st.markdown("#### Confidence and uncertainty")
    st.write(f"**{r.confidence}**: {r.confidence_explanation}")
    if r.goal_assessment:
        st.markdown("#### Goal assessment")
        st.write(r.goal_assessment)
    if r.conditions_to_revisit:
        st.markdown("#### Conditions to revisit")
        for x in r.conditions_to_revisit:
            st.markdown(f"- {x}")
    if r.security_observations:
        st.markdown("#### 🛡 Security observations")
        for x in r.security_observations:
            st.markdown(f"- {x}")
    with st.expander("Specialist findings (structured outputs returned to the supervisor)"):
        for agent, findings in run.findings.items():
            for i, f in enumerate(findings, 1):
                st.markdown(f"**{agent}** (call {i}) · stance **{f['stance']}** · confidence {f['confidence']}")
                st.json(f, expanded=False)
    st.download_button("Download report (Markdown)", generate_investment_report(final), file_name=f"committee-report-{run.run_id}.md")
    st.caption(final.disclaimer)


# ---------------------------------------------------------------------------
# Section E: human approval
# ---------------------------------------------------------------------------

def approval_section(run: CommitteeRun, slot: str) -> str | None:
    """Returns a revision note when the reviewer requests a revision."""
    final = run.final
    if final is None:
        return None
    decisions = st.session_state.setdefault(f"decisions_{slot}", {})
    done = decisions.get(run.run_id)
    st.markdown("Approval **only records your decision** in `audit/decisions.jsonl`. "
                "There is no brokerage connection and no trade function in this application.")
    if done:
        st.success(f"Decision recorded: **{done['decision']}** by {done['reviewer']} at {done['decided_at']}. trade_executed = false")
        st.code(json.dumps(done, indent=2), language="json")
        return None
    reviewer = st.text_input("Reviewer name", key=f"reviewer_{slot}_{run.run_id}", max_chars=80)
    comment = st.text_area("Comment or revision request", key=f"comment_{slot}_{run.run_id}", max_chars=2000,
                           placeholder="e.g. Re-check the valuation using a current price, and stress-test a 10% allocation.")
    c1, c2, c3 = st.columns(3)
    clicked = None
    if c1.button("✅ Approve recommendation", key=f"ap_{slot}_{run.run_id}", type="primary"):
        clicked = "APPROVED"
    if c2.button("❌ Reject recommendation", key=f"rj_{slot}_{run.run_id}"):
        clicked = "REJECTED"
    if c3.button("🔁 Request revision", key=f"rv_{slot}_{run.run_id}"):
        clicked = "REVISION_REQUESTED"
    if clicked:
        if not reviewer.strip():
            st.error("Enter the reviewer's name first.")
            return None
        if clicked == "REVISION_REQUESTED" and not comment.strip():
            st.error("Describe what should be revised.")
            return None
        rec = record_decision(final, clicked, reviewer, comment)
        decisions[run.run_id] = rec.model_dump()
        if clicked == "REVISION_REQUESTED":
            return comment.strip()
        st.rerun()
    return None
