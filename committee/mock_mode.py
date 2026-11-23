"""Deterministic MOCK mode for rehearsals and offline demos.

What is real here: every number (the same Python tools run on the same dataset),
the security filters, the policy gate and the report post-processing.
What is simulated: the agents' decisions and wording, which follow fixed rules.
No Bedrock call is made, every event is marked simulated=True, and no token
counts or costs are reported.
"""

from __future__ import annotations

import time
from datetime import datetime, timezone
from typing import Any, Callable

from committee.config import Settings
from committee.context import SharedContext
from committee.report import finalize_report, synthesize_report
from committee.schemas import Evidence, InvestmentRequest, SpecialistFinding
from observability.events import FINANCIAL, RESEARCH, RISK, SUPERVISOR, EventRecorder
from tools.company_data import company_profile, third_party_documents
from tools.financial_calculations import compare, profit_margins, revenue_growth, valuation_ratios
from tools.planning_tools import assess_return_target
from tools.risk_tools import company_risks, concentration_check, currency_exposure, downside_scenarios, investment_policy_check


class _MockRunner:
    def __init__(self, ctx: SharedContext, delay_s: float) -> None:
        self.ctx = ctx
        self.rec: EventRecorder = ctx.recorder  # type: ignore[assignment]
        self.delay = delay_s

    def pause(self, factor: float = 1.0) -> None:
        if self.delay:
            time.sleep(self.delay * factor)

    def call(self, agent: str, name: str, fn: Callable[..., Any], **kwargs: Any) -> Any:
        self.rec.tool_started(agent, name, kwargs, tool_use_id=f"{agent}-{name}-{kwargs}")
        self.pause(0.5)
        try:
            out = fn(**kwargs)
            self.rec.tool_finished(agent, name, status="success", output=out, tool_use_id=f"{agent}-{name}-{kwargs}")
            return out
        except Exception as exc:  # noqa: BLE001
            self.rec.tool_finished(agent, name, status="error", output=str(exc), tool_use_id=f"{agent}-{name}-{kwargs}")
            return None

    def specialist(self, agent: str, task: str, build: Callable[[], SpecialistFinding]) -> None:
        self.rec.delegation(agent, task)
        self.rec.agent_started(agent, task)
        t0 = time.monotonic()
        finding = build()
        self.rec.emit("structured_output", agent, f"{agent} submitted structured SpecialistFinding", tool_name="SpecialistFinding", status="ok")
        self.ctx.add_finding(agent, finding.model_dump())
        self.rec.agent_completed(agent, duration_ms=(time.monotonic() - t0) * 1000, tokens=None,
                                 summary=f"stance={finding.stance}, confidence={finding.confidence}: {finding.summary}")

    # -- specialists -------------------------------------------------------
    def research(self) -> SpecialistFinding:
        req, points, notes, missing = self.ctx.request, [], [], []
        growths = []
        for tk in req.tickers:
            p = self.call(RESEARCH, "get_company_profile", company_profile, ticker=tk)
            g = revenue_growth(tk)
            growths.append(g["latest_yoy_growth_pct"] or 0)
            if p:
                points.append(Evidence(claim=f"{p['name']}: {p['business_description'][:180]}", source=f"get_company_profile / {p['description_source']['source_id']}"))
                if p["competitive_advantages"]:
                    points.append(Evidence(claim=p["competitive_advantages"][0]["text"][:220], source="get_company_profile"))
                missing += p["data_gaps"][:1]
            if req.include_untrusted_documents:
                docs = self.call(RESEARCH, "get_third_party_documents", lambda ticker: third_party_documents(ticker, sanitize=self.ctx.sanitize_documents), ticker=tk) or []
                for d in docs:
                    if d["deterministic_flags"]:
                        self.ctx.add_security_event({"agent": RESEARCH, "message": f"Untrusted document '{d['doc_id']}' contains instruction-like text",
                                                     "doc_id": d["doc_id"], "deterministic_flags": d["deterministic_flags"],
                                                     "removed_lines": d["removed_lines"], "sanitized": d["sanitized"],
                                                     "guardrail": {"evaluated": False, "reason": "Mock mode"}})
                        notes.append(f"Ignored instructions embedded in '{d['title']}' ({', '.join(d['deterministic_flags'])}). Treated as unreliable opinion.")
        stance = "positive" if min(growths) > 10 else "neutral"
        return SpecialistFinding(stance=stance, summary=f"[MOCK] Business profiles reviewed for {', '.join(req.tickers)}.",
                                 key_points=points[:6], concerns=["Competitive positions are summarized, not independently verified."],
                                 missing_information=missing, security_notes=notes, confidence="medium")

    def financial(self) -> SpecialistFinding:
        req, points, missing, positives, negatives = self.ctx.request, [], [], 0, 0
        for tk in req.tickers:
            g = self.call(FINANCIAL, "calculate_revenue_growth", revenue_growth, ticker=tk)
            m = self.call(FINANCIAL, "calculate_profit_margin", profit_margins, ticker=tk)
            v = self.call(FINANCIAL, "calculate_valuation_ratios", lambda ticker: valuation_ratios(ticker, req.price_inputs.get(ticker)), ticker=tk)
            growth, net = g["latest_yoy_growth_pct"], m["latest"]["net_margin_pct"]
            points.append(Evidence(claim=f"{tk} revenue growth {growth}% in latest fiscal year; CAGR {g['cagr_pct']}% ({g['cagr_period']}).", source=f"calculate_revenue_growth / {g['source_id']}"))
            points.append(Evidence(claim=f"{tk} net margin {net}%; operating margin {m['latest']['operating_margin_pct']}%; FCF margin {m['latest']['fcf_margin_pct']}%.", source="calculate_profit_margin"))
            if v and v.get("status") == "ok":
                points.append(Evidence(claim=f"{tk} P/E on latest FY EPS: {v['pe_on_latest_fy_eps']} (price as of {v['price_as_of']}).", source="calculate_valuation_ratios"))
            else:
                missing.append(f"{tk}: valuation not assessed (no dated share price).")
            missing += [f"{tk}: {k} unavailable" for k in m["unavailable_metrics"]]
            if (growth or 0) > 15 and (net or 0) > 15:
                positives += 1
            if (growth or 0) < 0 or (net or 0) < 5:
                negatives += 1
        if len(req.tickers) > 1:
            self.call(FINANCIAL, "compare_companies", lambda tickers: compare(tickers, req.price_inputs), tickers=req.tickers)
        stance = "positive" if positives and not negatives else ("negative" if negatives and not positives else "neutral")
        return SpecialistFinding(stance=stance, summary="[MOCK] Growth, margins and valuation computed by Python tools.",
                                 key_points=points[:6], concerns=[], missing_information=missing, confidence="high" if not missing else "medium")

    def risk(self) -> SpecialistFinding:
        req, points, concerns, missing, flags = self.ctx.request, [], [], [], 0
        conc = self.call(RISK, "check_concentration", concentration_check, allocation_pct=20, risk_tolerance=req.risk_tolerance)
        if conc and not conc["within_limit"]:
            flags += 1
            concerns.append(conc["message"])
        for tk in req.tickers:
            r = self.call(RISK, "get_company_risks", company_risks, ticker=tk)
            d = self.call(RISK, "run_downside_scenarios", downside_scenarios, ticker=tk, budget=req.budget, investor_currency=req.currency, allocation_pct=20)
            cx = self.call(RISK, "assess_currency_risk", currency_exposure, ticker=tk, investor_currency=req.currency, investor_country=req.investor_country)
            if r:
                cats = r["risks_by_category"]
                for cat in ("regulatory", "concentration", "supply_chain", "currency", "commodity"):
                    if cat in cats:
                        concerns.append(f"{tk} {cat} risk: {cats[cat][0]['text'][:200]}")
                if {"regulatory", "concentration"} & cats.keys():
                    flags += 1
                missing += r["data_gaps"][:1]
            if d:
                worst = d["scenarios"][-1]
                points.append(Evidence(claim=f"{tk}: a {worst['price_move_pct']}% price fall on a 20% position loses {worst['loss']:,.0f} {req.currency} ({worst['loss_pct_of_total_budget']}% of budget).", source="run_downside_scenarios"))
            if cx and cx["currency_mismatch"]:
                flags += 1
                points.append(Evidence(claim=f"{tk}: currency mismatch ({cx['asset_currency']} asset, {cx['investor_currency']} investor).", source="assess_currency_risk"))
            if valuation_ratios(tk, req.price_inputs.get(tk)).get("status") != "ok":
                flags += 1
                concerns.append(f"{tk}: valuation unknown, so the price may already reflect the growth.")
        stance = "negative" if flags >= 2 else "neutral"
        return SpecialistFinding(stance=stance, summary="[MOCK] Independent challenge: downside, concentration, currency and data gaps.",
                                 key_points=points[:6], concerns=concerns[:6], missing_information=missing, confidence="medium")


def run_mock(request: InvestmentRequest, *, settings: Settings, recorder: EventRecorder | None, sanitize_documents: bool, delay_s: float):
    from committee.orchestrator import CommitteeRun

    ctx = SharedContext(request=request, sanitize_documents=sanitize_documents)
    if recorder is not None:
        ctx.run_id = recorder.run_id
        recorder.simulated = True
    ctx.recorder = recorder or EventRecorder(ctx.run_id, simulated=True, log_dir=settings.log_dir)
    rec = ctx.recorder
    runner = _MockRunner(ctx, delay_s)
    t0 = time.monotonic()
    started_at = datetime.now(timezone.utc).isoformat(timespec="seconds")

    rec.emit("run_started", SUPERVISOR, "MOCK MODE: scripted run, no Bedrock calls", status="running")
    rec.agent_started(SUPERVISOR, request.question)
    runner.pause()
    runner.specialist(RESEARCH, "Analyze business models, competitive position and third-party sources.", runner.research)
    runner.specialist(FINANCIAL, "Compute growth, margins, cash flow and valuation.", runner.financial)
    runner.specialist(RISK, "Challenge the thesis: downside, concentration, currency, missing data.", runner.risk)

    report = synthesize_report(ctx, reason="[MOCK MODE] Rule-based synthesis, no model was called.")
    if request.target_return_pct is not None:
        goal = runner.call(SUPERVISOR, "assess_investment_goal", assess_return_target, budget=request.budget, currency=request.currency,
                           target_return_pct=request.target_return_pct, horizon_years=request.horizon_years)
        if goal:
            report.goal_assessment = (f"Target {goal['target_return_pct']}% a year vs {goal['risk_free_benchmark']['name']} "
                                      f"{goal['risk_free_benchmark']['rate_pct']}%: {goal['assessment']}")
    runner.call(SUPERVISOR, "check_investment_policy", investment_policy_check, recommendation=report.recommendation,
                allocation_pct=report.proposed_allocation_pct, confidence=report.confidence, risk_tolerance=request.risk_tolerance)
    rec.emit("structured_output", SUPERVISOR, "Supervisor submitted structured CommitteeReport", tool_name="CommitteeReport", status="ok")
    final = finalize_report(report, ctx, mode="mock", produced_by="mock_script")
    rec.agent_completed(SUPERVISOR, duration_ms=(time.monotonic() - t0) * 1000, tokens=None,
                        summary=f"{final.report.recommendation} {final.report.selected_ticker or ''}")
    rec.emit("run_completed", SUPERVISOR, f"MOCK run completed: {final.report.recommendation} {final.report.selected_ticker or ''}".strip(), status="ok")
    events, statuses = rec.snapshot()
    return CommitteeRun(
        run_id=ctx.run_id, mode="mock", request=request, final=final, findings=ctx.specialist_findings, events=events,
        agent_statuses=statuses, usage=[], total_tokens=None, total_cost_usd=None, pricing_configured=settings.pricing_configured,
        security_events=ctx.security_events, started_at=started_at, duration_s=round(time.monotonic() - t0, 2),
    )
