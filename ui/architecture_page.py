"""Page 4: architecture, observability and audit trail."""

from __future__ import annotations

import json

import pandas as pd
import streamlit as st

from committee.config import get_settings
from committee.orchestrator import CommitteeRun
from observability.tracing import setup_tracing
from ui.common import hero, sidebar

DOT = """
digraph G {
  rankdir=LR; bgcolor="transparent"; node [shape=box, style="rounded,filled", fontname="Helvetica", fontsize=11];
  user [label="User / Reviewer\\n(Streamlit)", fillcolor="#e0e7ff"];
  val [label="Input validation\\n+ Guardrail check", fillcolor="#fde68a"];
  sup [label="Supervisor agent\\nNova Pro", fillcolor="#bfdbfe"];
  res [label="Market Research\\nNova 2 Lite", fillcolor="#c7d2fe"];
  fin [label="Financial Analyst\\nNova 2 Lite", fillcolor="#c7d2fe"];
  risk [label="Risk Analyst\\nNova 2 Lite", fillcolor="#c7d2fe"];
  tools1 [label="profile / documents\\n(untrusted, quarantined)", fillcolor="#f1f5f9"];
  tools2 [label="pandas calculations\\ngrowth, margins, FCF, P/E", fillcolor="#f1f5f9"];
  tools3 [label="risks, stress tests,\\ncurrency, concentration", fillcolor="#f1f5f9"];
  data [label="Sourced dataset\\nSEC · PSX · SBP · Treasury", shape=cylinder, fillcolor="#dcfce7"];
  gate [label="Policy gate + redaction\\n(deterministic)", fillcolor="#fde68a"];
  hitl [label="Human approval\\n(records decision only)", fillcolor="#fecaca"];
  user -> val -> sup;
  sup -> res [label="agent as tool"]; sup -> fin; sup -> risk;
  res -> tools1; fin -> tools2; risk -> tools3;
  tools1 -> data; tools2 -> data; tools3 -> data;
  sup -> gate -> hitl -> user;
}
"""


def render() -> None:
    sidebar()
    hero("🧭 Architecture & Observability", "How the pieces fit, and how to see what the agents did.")
    st.graphviz_chart(DOT, width="stretch")
    st.markdown("""
- **Agents as tools.** The supervisor's tools are three specialist agents. Each call starts a real Strands agent loop on Bedrock.
- **Shared context.** Budget, currency, prices and policy travel in Strands `invocation_state`; tools read them through `ToolContext`.
- **Structured outputs.** Specialists return `SpecialistFinding`, the supervisor returns `CommitteeReport` (Pydantic, validated by Strands).
- **Deterministic math.** Every figure comes from `tools/financial_calculations.py` (pandas), never from the model.
- **Hooks.** `BeforeToolCallEvent` / `AfterToolCallEvent` feed the execution timeline and `logs/run-<id>.jsonl`.
""")
    s = get_settings()
    st.markdown("#### Tracing")
    st.write(f"OpenTelemetry: **{setup_tracing(s)}**")
    st.caption("Set OTEL_EXPORTER_OTLP_ENDPOINT (for example a local Jaeger at http://localhost:4318) or STRANDS_CONSOLE_TRACING=true, then restart.")

    runs = {k: v for k, v in st.session_state.items() if k.startswith("run_") and isinstance(v, CommitteeRun)}
    st.markdown("#### Runs in this session")
    if runs:
        st.dataframe(pd.DataFrame([{"page": k[4:], "run": r.run_id, "mode": r.mode, "seconds": r.duration_s, "tokens": r.total_tokens,
                                    "cost USD": r.total_cost_usd, "result": r.final.report.recommendation if r.final else "failed"}
                                   for k, r in runs.items()]), hide_index=True, width="stretch")
    else:
        st.caption("No runs yet.")

    st.markdown("#### Audit trail (human decisions)")
    path = s.audit_dir / "decisions.jsonl"
    if path.exists():
        rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
        st.dataframe(pd.DataFrame(rows[-20:]), hide_index=True, width="stretch")
    else:
        st.caption("No decisions recorded yet.")
