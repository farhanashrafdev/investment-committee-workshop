"""Shared Streamlit helpers: styling, sidebar, cached data, and the background run controller."""

from __future__ import annotations

import threading
import time
import uuid
from typing import Any, Callable

import pandas as pd
import streamlit as st

from committee.config import get_settings
from committee.orchestrator import CommitteeRun, run_committee
from committee.preflight import run_preflight
from committee.schemas import InvestmentRequest
from observability.events import AGENT_NAMES, EventRecorder
from tools.dataset import load_dataset
from tools.financial_calculations import compare, profit_margins, revenue_growth

STATUS_STYLE = {
    "Waiting": ("#6b7280", "#f3f4f6", "Waiting"),
    "Running": ("#1d4ed8", "#dbeafe", "Running"),
    "Completed": ("#047857", "#d1fae5", "Completed"),
    "Failed": ("#b91c1c", "#fee2e2", "Failed"),
}
AGENT_ICONS = {
    "Investment Committee Supervisor": "🧑‍⚖️",
    "Market Research Analyst": "🔎",
    "Financial Analyst": "📊",
    "Risk Analyst": "🛡️",
}
REC_STYLE = {"INVEST": ("#047857", "#d1fae5"), "HOLD": ("#92400e", "#fef3c7"), "REJECT": ("#b91c1c", "#fee2e2")}

CSS = """
<style>
.block-container {padding-top: 1.6rem;}
.hero {background: linear-gradient(120deg,#0f172a 0%,#1e3a8a 55%,#0f766e 100%); color:#fff; padding:1.2rem 1.5rem;
       border-radius:14px; margin-bottom:1rem;}
.hero h1 {color:#fff; font-size:1.7rem; margin:0;}
.hero p {color:#e2e8f0; margin:.3rem 0 0 0;}
.agent-card {border-radius:12px; padding:.8rem 1rem; border:1px solid #e5e7eb; min-height:118px;}
.agent-card .name {font-weight:700; font-size:.98rem;}
.agent-card .status {display:inline-block; padding:.1rem .55rem; border-radius:999px; font-size:.8rem; font-weight:600; margin-top:.35rem;}
.agent-card .meta {color:#4b5563; font-size:.8rem; margin-top:.35rem;}
.rec-banner {border-radius:14px; padding:1rem 1.3rem; margin:.4rem 0 1rem 0;}
.rec-banner .rec {font-size:1.6rem; font-weight:800;}
.mode-live {background:#065f46; color:#fff; padding:.25rem .7rem; border-radius:6px; font-weight:700;}
.mode-mock {background:#b45309; color:#fff; padding:.25rem .7rem; border-radius:6px; font-weight:700;}
.small-note {color:#6b7280; font-size:.85rem;}
</style>
"""


def apply_style() -> None:
    st.markdown(CSS, unsafe_allow_html=True)


def hero(title: str, subtitle: str) -> None:
    st.markdown(f'<div class="hero"><h1>{title}</h1><p>{subtitle}</p></div>', unsafe_allow_html=True)


def mode_badge(mode: str) -> str:
    if mode == "live":
        return '<span class="mode-live">LIVE · Amazon Bedrock</span>'
    return '<span class="mode-mock">MOCK MODE · scripted, no Bedrock calls</span>'


# ---------------------------------------------------------------------------
# Cached, non-user-specific data
# ---------------------------------------------------------------------------

@st.cache_data(show_spinner=False)
def companies_table() -> pd.DataFrame:
    ds = load_dataset()
    return pd.DataFrame([
        {"ticker": c.ticker, "name": c.name, "market": "Pakistan (PSX)" if c.market == "PK" else "United States",
         "exchange": c.exchange, "sector": c.sector, "currency": c.currency}
        for c in ds.companies
    ])


@st.cache_data(show_spinner=False)
def metrics_for(tickers: tuple[str, ...]) -> dict[str, Any]:
    """Deterministic financial metrics; safe to cache because they depend only on the dataset."""
    return {
        "growth": {t: revenue_growth(t) for t in tickers},
        "margins": {t: profit_margins(t) for t in tickers},
        "compare": compare(list(tickers)) if tickers else None,
    }


@st.cache_data(ttl=300, show_spinner="Running preflight checks...")
def cached_preflight(invoke_models: bool) -> list[dict[str, Any]]:
    return [c.to_dict() for c in run_preflight(get_settings(), invoke_models=invoke_models)]


# ---------------------------------------------------------------------------
# Sidebar
# ---------------------------------------------------------------------------

def sidebar() -> str:
    s = get_settings()
    ds = load_dataset()
    with st.sidebar:
        st.markdown("### ⚙️ Run settings")
        mode_label = st.radio("Execution mode", ["Live (Amazon Bedrock)", "Mock (rehearsal, no AWS)"], key="mode_choice",
                              help="Mock mode uses the same Python tools and data but scripted agent behaviour. It is always labeled.")
        mode = "live" if mode_label.startswith("Live") else "mock"
        st.caption(f"Region **{s.aws_region}**")
        st.caption(f"Supervisor: `{s.supervisor_model_id}`")
        st.caption(f"Specialists: `{s.specialist_model_id}`")
        st.caption(f"Guardrail: {'`' + s.guardrail_id + '`' if s.guardrail_id else 'not configured'}")
        st.caption(f"Cost estimate: {'configured' if s.pricing_configured else 'pricing not configured'}")
        st.caption(f"Dataset: {ds.dataset_version} (historical snapshot)")
        with st.expander("Preflight checks"):
            invoke = st.checkbox("Include a tiny test call to each model", value=True)
            if st.button("Run preflight", key="run_preflight"):
                cached_preflight.clear()
                st.session_state["preflight"] = cached_preflight(invoke)
            for c in st.session_state.get("preflight", []):
                st.markdown(f"{'✅' if c['ok'] else '❌'} **{c['name']}**: {c['detail']}")
                if c["hint"]:
                    st.caption(c["hint"])
        st.divider()
        st.caption("Educational demo. Not financial advice. No trades are ever executed.")
    return mode


# ---------------------------------------------------------------------------
# Background run controller (one active run per browser session)
# ---------------------------------------------------------------------------

def start_run(slot: str, request: InvestmentRequest, mode: str, sanitize: bool = True) -> None:
    s = get_settings()
    recorder = EventRecorder(uuid.uuid4().hex[:12], simulated=(mode == "mock"), log_dir=s.log_dir)
    holder: dict[str, Any] = {}

    def target() -> None:
        try:
            holder["run"] = run_committee(request, mode=mode, settings=s, recorder=recorder, sanitize_documents=sanitize)  # type: ignore[arg-type]
        except Exception as exc:  # noqa: BLE001
            holder["error"] = f"{type(exc).__name__}: {exc}"

    th = threading.Thread(target=target, daemon=True, name=f"committee-{recorder.run_id}")
    th.start()
    st.session_state[f"active_{slot}"] = {"thread": th, "recorder": recorder, "holder": holder, "mode": mode}


def poll_run(slot: str, render: Callable[[EventRecorder, str], None]) -> CommitteeRun | None:
    """Re-render live progress until the background run finishes; returns the result once."""
    active = st.session_state.get(f"active_{slot}")
    if not active:
        return None
    th: threading.Thread = active["thread"]
    while th.is_alive():
        render(active["recorder"], active["mode"])
        time.sleep(0.7)
    render(active["recorder"], active["mode"])
    del st.session_state[f"active_{slot}"]
    if "error" in active["holder"]:
        st.error(active["holder"]["error"])
        return None
    run: CommitteeRun = active["holder"]["run"]
    st.session_state[f"run_{slot}"] = run
    return run


def is_running(slot: str) -> bool:
    return f"active_{slot}" in st.session_state


def agent_cards(container, statuses: dict[str, str], events: list) -> None:
    cols = container.columns(4)
    for col, name in zip(cols, AGENT_NAMES):
        status = statuses.get(name, "Waiting")
        fg, bg, label = STATUS_STYLE[status]
        tools = sum(1 for e in events if e.agent == name and e.kind == "tool_call")
        tokens = sum((e.tokens or {}).get("totalTokens", 0) for e in events if e.agent == name and e.kind == "agent_completed")
        meta = f"{tools} tool calls" + (f" · {tokens:,} tokens" if tokens else "")
        col.markdown(
            f'<div class="agent-card" style="background:{bg}22;border-color:{fg}55">'
            f'<div class="name">{AGENT_ICONS[name]} {name}</div>'
            f'<div class="status" style="color:{fg};background:{bg}">{label}</div>'
            f'<div class="meta">{meta}</div></div>',
            unsafe_allow_html=True,
        )


KIND_LABEL = {
    "run_started": "▶ run", "run_completed": "■ done", "run_failed": "✖ failed", "delegation": "→ delegate",
    "agent_started": "● start", "agent_completed": "✔ complete", "agent_failed": "✖ failed", "tool_call": "⚙ tool call",
    "tool_result": "↩ result", "structured_output": "📄 structured", "security": "🛡 security", "policy": "⚖ policy",
    "usage": "Σ usage", "info": "ℹ info",
}


def timeline_frame(events: list) -> pd.DataFrame:
    df = pd.DataFrame([
        {
            "t (s)": e.elapsed_s,
            "event": KIND_LABEL.get(e.kind, e.kind),
            "agent": e.agent,
            "tool": e.tool_name or "",
            "status": e.status or "",
            "ms": f"{e.duration_ms:,.0f}" if e.duration_ms is not None else "",
            "tokens": f"{(e.tokens or {}).get('totalTokens'):,}" if (e.tokens or {}).get("totalTokens") else "",
            "detail": e.tool_input or e.output_preview or e.message,
        }
        for e in events
    ])
    return df
