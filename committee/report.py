"""Deterministic post-processing and rendering of the committee report.

``finalize_report`` runs after the supervisor answers. It re-applies the
investment policy, checks scope, redacts secrets and records what changed, so
the final recommendation never depends on the model alone.

``generate_investment_report`` renders the final report as Markdown.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Literal

from pydantic import BaseModel

from committee.context import SharedContext
from committee.schemas import CommitteeReport
from observability.events import FINANCIAL, RESEARCH, RISK
from security.output_checks import redact_secrets
from tools.dataset import load_dataset, load_policy
from tools.financial_calculations import compare
from tools.formatting import fmt_money, fmt_pkr_crore
from tools.risk_tools import investment_policy_check

DISCLAIMER = (
    "Educational analysis produced by an AI investment-committee demo using a dated historical dataset. "
    "It is not personalized financial advice, not an offer or solicitation, and no trade has been or will be executed "
    "by this application. Consult a licensed financial adviser (for example one regulated by the SECP in Pakistan or "
    "by the SEC/FINRA in the United States) before investing."
)


class FinalReport(BaseModel):
    run_id: str
    mode: Literal["live", "mock"]
    produced_by: Literal["supervisor_agent", "deterministic_fallback", "mock_script"]
    report: CommitteeReport
    original_recommendation: str
    original_allocation_pct: float
    adjustments: list[str]
    policy_check: dict[str, Any]
    allocation_amount: float
    currency: str
    specialist_stances: dict[str, str]
    stance_disagreement: bool
    redactions: list[str]
    disclaimer: str = DISCLAIMER
    dataset_version: str
    models: dict[str, str] | None = None
    generated_at: str


def latest_stances(ctx: SharedContext) -> dict[str, str]:
    return {agent: findings[-1].get("stance", "unknown") for agent, findings in ctx.specialist_findings.items() if findings}


def _redact_model(report: CommitteeReport) -> tuple[CommitteeReport, list[str]]:
    found: list[str] = []
    data = report.model_dump()
    for key, value in data.items():
        if isinstance(value, str):
            data[key], f = redact_secrets(value)
            found += f
        elif isinstance(value, list):
            new = []
            for item in value:
                if isinstance(item, str):
                    item, f = redact_secrets(item)
                    found += f
                new.append(item)
            data[key] = new
    return CommitteeReport.model_validate(data), sorted(set(found))


def finalize_report(
    report: CommitteeReport,
    ctx: SharedContext,
    *,
    mode: Literal["live", "mock"],
    produced_by: Literal["supervisor_agent", "deterministic_fallback", "mock_script"],
    models: dict[str, str] | None = None,
) -> FinalReport:
    req = ctx.request
    adjustments: list[str] = []
    original_rec, original_alloc = report.recommendation, report.proposed_allocation_pct
    data = report.model_dump()

    # 1. Scope: the recommendation must refer to a ticker the user asked about.
    if data["selected_ticker"] is not None:
        data["selected_ticker"] = str(data["selected_ticker"]).upper().strip()
    if data["selected_ticker"] not in req.tickers:
        if len(req.tickers) == 1:
            if data["selected_ticker"] is not None:
                adjustments.append(f"selected_ticker '{data['selected_ticker']}' replaced with request ticker {req.tickers[0]}.")
            data["selected_ticker"] = req.tickers[0]
        elif data["recommendation"] == "INVEST":
            adjustments.append(f"selected_ticker '{data['selected_ticker']}' is outside the request; downgraded to HOLD.")
            data.update(recommendation="HOLD", proposed_allocation_pct=0.0, selected_ticker=None)
        else:
            data["selected_ticker"] = None

    # 2. Investment policy (deterministic, same rules the supervisor tool exposes).
    policy = investment_policy_check(data["recommendation"], data["proposed_allocation_pct"], data["confidence"], req.risk_tolerance)
    if not policy["passed"]:
        adjustments += policy["violations"]
    data["recommendation"] = policy["adjusted_recommendation"]
    data["proposed_allocation_pct"] = policy["adjusted_allocation_pct"]

    # 3. Security observations the deterministic filters recorded are always shown,
    #    even if the model forgot to mention them.
    observed = list(data.get("security_observations") or [])
    for ev in ctx.security_events:
        line = (f"Deterministic filter flagged untrusted document '{ev.get('doc_id')}' "
                f"({', '.join(ev.get('deterministic_flags', [])) or 'guardrail'}); its instructions were not followed.")
        if line not in observed:
            observed.append(line)
    data["security_observations"] = observed

    clean, redactions = _redact_model(CommitteeReport.model_validate(data))
    if redactions:
        adjustments.append(f"Redacted secret-like text from the report: {', '.join(redactions)}.")

    stances = latest_stances(ctx)
    distinct = {s for s in stances.values() if s in {"positive", "neutral", "negative"}}
    allocation_amount = round(req.budget * clean.proposed_allocation_pct / 100.0, 2)

    if ctx.recorder:
        ctx.recorder.policy(
            "Deterministic policy check applied to the final recommendation",
            {"original": {"recommendation": original_rec, "allocation_pct": original_alloc},
             "final": {"recommendation": clean.recommendation, "allocation_pct": clean.proposed_allocation_pct},
             "adjustments": adjustments},
            status="ok" if not adjustments else "warning",
        )

    return FinalReport(
        run_id=ctx.run_id,
        mode=mode,
        produced_by=produced_by,
        report=clean,
        original_recommendation=original_rec,
        original_allocation_pct=original_alloc,
        adjustments=adjustments,
        policy_check=policy,
        allocation_amount=allocation_amount,
        currency=req.currency,
        specialist_stances=stances,
        stance_disagreement=len(distinct) > 1,
        redactions=redactions,
        dataset_version=load_dataset().dataset_version,
        models=models,
        generated_at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
    )


def synthesize_report(ctx: SharedContext, *, reason: str) -> CommitteeReport:
    """Rule-based committee report built from specialist findings and tool numbers.

    Used by mock mode, and as a clearly labeled fallback if the live supervisor
    fails after specialists have already produced findings.
    """
    req = ctx.request
    policy = load_policy()
    stances = latest_stances(ctx)
    comp = compare(req.tickers, req.price_inputs)
    ranked = sorted(
        comp["companies"],
        key=lambda r: (r["net_margin_pct"] or 0) + min(r["latest_yoy_growth_pct"] or 0, 50),
        reverse=True,
    )
    best = ranked[0]
    fin, risk = stances.get(FINANCIAL, "neutral"), stances.get(RISK, "neutral")
    negatives = sum(1 for s in stances.values() if s == "negative")
    limit = policy.max_single_position_pct[req.risk_tolerance]
    if fin == "positive" and risk != "negative":
        rec, alloc, conf = "INVEST", round(limit / 2), "medium"
    elif negatives >= 2:
        rec, alloc, conf = "REJECT", 0, "medium"
    else:
        rec, alloc, conf = "HOLD", 0, "medium" if fin == "positive" else "low"

    def points(agent: str, key: str) -> list[str]:
        out: list[str] = []
        for f in ctx.specialist_findings.get(agent, [])[-1:]:
            for item in f.get(key, []):
                out.append(item["claim"] if isinstance(item, dict) else str(item))
        return out

    disagreements = []
    if fin == "positive" and risk == "negative":
        disagreements.append("Financial Analyst is positive on fundamentals; Risk Analyst is negative on downside and concentration. "
                             "Resolved conservatively: no allocation until the risks below are addressed.")
    missing = sorted({m for fs in ctx.specialist_findings.values() for f in fs for m in f.get("missing_information", [])})
    return CommitteeReport(
        recommendation=rec,  # type: ignore[arg-type]
        selected_ticker=best["ticker"] if rec != "REJECT" else None,
        proposed_allocation_pct=alloc,
        executive_summary=(f"{reason} Based on specialist stances (research: {stances.get(RESEARCH, 'n/a')}, financial: {fin}, "
                           f"risk: {risk}), the rule-based committee recommends {rec} for {best['ticker']}."),
        bull_case=points(RESEARCH, "key_points")[:3] + points(FINANCIAL, "key_points")[:3],
        bear_case=points(RISK, "key_points")[:4] + points(RISK, "concerns")[:2],
        financial_assessment=" ".join(p for p in points(FINANCIAL, "key_points")[:4]) or "No financial finding available.",
        key_risks=points(RISK, "concerns")[:5] or points(RISK, "key_points")[:5],
        disagreements=disagreements,
        missing_evidence=missing[:8],
        confidence=conf,  # type: ignore[arg-type]
        confidence_explanation="Rule-based synthesis: confidence is medium when specialists broadly agree and low when they conflict.",
        goal_assessment=None,
        conditions_to_revisit=["A dated current share price is supplied for valuation", "Next annual results are available"],
        security_observations=[n for fs in ctx.specialist_findings.values() for f in fs for n in f.get("security_notes", [])],
    )


def _money(value: float, currency: str) -> str:
    s = fmt_money(value, currency)
    return f"{s} ({fmt_pkr_crore(value)})" if currency == "PKR" and value >= 1e5 else s


def generate_investment_report(final: FinalReport) -> str:
    """Render the final committee report as Markdown (used for display and download)."""
    r = final.report
    mode_line = "LIVE: generated by Amazon Bedrock agents" if final.mode == "live" else "MOCK MODE: scripted demo, no Bedrock calls"
    if final.produced_by == "deterministic_fallback":
        mode_line += " (supervisor failed; report assembled by deterministic fallback from specialist findings)"

    def bullets(items: list[str]) -> str:
        return "\n".join(f"- {i}" for i in items) if items else "- None recorded"

    lines = [
        "# Investment Committee Report",
        f"**Run:** `{final.run_id}`  |  **Mode:** {mode_line}  |  **Dataset:** {final.dataset_version}",
        "",
        f"## Recommendation: {r.recommendation}" + (f" {r.selected_ticker}" if r.selected_ticker else ""),
        f"- Proposed allocation: **{r.proposed_allocation_pct:.0f}%** of budget = **{_money(final.allocation_amount, final.currency)}**",
        f"- Confidence: **{r.confidence}**. {r.confidence_explanation}",
        "- Status: **awaiting human approval**. No trade is executed by this system.",
    ]
    if final.adjustments:
        lines += ["", "### Deterministic adjustments", bullets(final.adjustments)]
    lines += [
        "", "## Executive summary", r.executive_summary,
        "", "## Bull case", bullets(r.bull_case),
        "", "## Bear case", bullets(r.bear_case),
        "", "## Financial assessment", r.financial_assessment,
        "", "## Key risks", bullets(r.key_risks),
        "", "## Specialist disagreements", bullets(r.disagreements),
        "", "## Missing evidence", bullets(r.missing_evidence),
    ]
    if r.goal_assessment:
        lines += ["", "## Goal assessment", r.goal_assessment]
    lines += [
        "", "## Conditions to revisit", bullets(r.conditions_to_revisit),
        "", "## Security observations", bullets(r.security_observations),
        "", "---", f"_{final.disclaimer}_",
    ]
    return "\n".join(lines)
