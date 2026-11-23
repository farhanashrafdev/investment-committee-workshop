"""Risk tools: company risks, stress scenarios, currency exposure and policy checks.

Scenario numbers are illustrative stress tests computed in Python. They are not
forecasts and nothing here predicts prices.
"""

from __future__ import annotations

from typing import Any

from strands import ToolContext, tool

from committee.context import get_shared_context
from security.input_validation import InputValidationError, validate_ticker
from tools.dataset import load_dataset, load_policy

CONFIDENCE_ORDER = {"low": 0, "medium": 1, "high": 2}


# ---------------------------------------------------------------------------
# Pure functions
# ---------------------------------------------------------------------------

def company_risks(ticker: str) -> dict[str, Any]:
    c = load_dataset().company(validate_ticker(ticker))
    by_cat: dict[str, list[dict[str, str]]] = {}
    for r in c.risks:
        by_cat.setdefault(r.category, []).append({"text": r.text, "kind": r.kind, "source_id": r.source_id})
    return {
        "ticker": c.ticker,
        "risks_by_category": by_cat,
        "data_gaps": c.data_gaps,
        "note": "filing_excerpt = verbatim from the company's filing; workshop_summary = workshop interpretation; data_point = value from the cited source.",
    }


def _check_allocation(allocation_pct: float) -> float:
    if allocation_pct is None or not 0 <= float(allocation_pct) <= 100:
        raise InputValidationError("allocation_pct must be between 0 and 100.")
    return float(allocation_pct)


def downside_scenarios(ticker: str, budget: float, investor_currency: str, allocation_pct: float) -> dict[str, Any]:
    ds, policy = load_dataset(), load_policy()
    c = ds.company(validate_ticker(ticker))
    alloc = _check_allocation(allocation_pct)
    position = budget * alloc / 100.0
    fx_mismatch = c.currency != investor_currency
    rows = []
    for shock in policy.stress_scenarios_pct:
        after = position * (1 + shock / 100.0)
        row: dict[str, Any] = {
            "price_move_pct": shock,
            "position_value_after": round(after, 2),
            "loss": round(position - after, 2),
            "loss_pct_of_total_budget": round((position - after) / budget * 100.0, 2),
        }
        if fx_mismatch:
            adverse = after * (1 - policy.fx_shock_pct / 100.0)
            row["with_adverse_fx_move"] = {
                "fx_move_pct": -policy.fx_shock_pct,
                "position_value_after": round(adverse, 2),
                "loss": round(position - adverse, 2),
                "loss_pct_of_total_budget": round((position - adverse) / budget * 100.0, 2),
            }
        rows.append(row)
    return {
        "ticker": c.ticker,
        "investor_currency": investor_currency,
        "asset_currency": c.currency,
        "budget": budget,
        "allocation_pct": alloc,
        "position_size": round(position, 2),
        "scenarios": rows,
        "label": "Illustrative stress tests, not forecasts. Shocks come from the committee policy file.",
        "formula": "value_after = position * (1 + price_move); with FX: value_after * (1 + fx_move)",
    }


def concentration_check(allocation_pct: float, risk_tolerance: str) -> dict[str, Any]:
    policy = load_policy()
    alloc = _check_allocation(allocation_pct)
    limit = policy.max_single_position_pct.get(risk_tolerance)
    if limit is None:
        raise InputValidationError(f"Unknown risk tolerance '{risk_tolerance}'.")
    return {
        "allocation_pct": alloc,
        "risk_tolerance": risk_tolerance,
        "max_single_position_pct": limit,
        "within_limit": alloc <= limit,
        "message": (f"{alloc:.0f}% in one stock is within the {limit:.0f}% limit for a {risk_tolerance} profile."
                    if alloc <= limit else
                    f"{alloc:.0f}% in one stock exceeds the {limit:.0f}% single-position limit for a {risk_tolerance} profile."),
        "policy_note": policy.notes,
    }


def currency_exposure(ticker: str, investor_currency: str, investor_country: str) -> dict[str, Any]:
    ds = load_dataset()
    c = ds.company(validate_ticker(ticker))
    fx = ds.market_context.fx
    notes: list[str] = []
    mismatch = c.currency != investor_currency
    if mismatch:
        notes.append(
            f"The asset is priced in {c.currency} and the investor measures returns in {investor_currency}: "
            "the investor's return is the share return combined with the USD/PKR move over the holding period."
        )
    if investor_country == "PK" and c.market == "US":
        notes.append(
            "Pakistani residents investing abroad must follow State Bank of Pakistan foreign exchange regulations. "
            "Confirm the current rules and approved channels with your bank or a licensed adviser. This tool does not state the limits."
        )
    if investor_country != "PK" and c.market == "PK":
        notes.append(
            "Non-resident investment in PSX-listed shares must use the channels allowed under SBP and SECP rules. "
            "Confirm the process with a PSX-licensed broker."
        )
    notes.append("Tax treatment (capital gains, dividends, withholding) is not assessed by this committee.")
    return {
        "ticker": c.ticker,
        "asset_currency": c.currency,
        "investor_currency": investor_currency,
        "currency_mismatch": mismatch,
        "reference_fx": {"pair": fx.pair, "rate": fx.rate, "as_of": fx.as_of, "source_id": fx.source_id, "description": fx.description},
        "notes": notes,
    }


def investment_policy_check(recommendation: str, allocation_pct: float, confidence: str, risk_tolerance: str) -> dict[str, Any]:
    """Deterministic policy gate. The orchestrator re-runs this after the model answers."""
    policy = load_policy()
    alloc = _check_allocation(allocation_pct)
    rec = recommendation.upper().strip()
    if rec not in {"INVEST", "HOLD", "REJECT"}:
        raise InputValidationError("recommendation must be INVEST, HOLD or REJECT.")
    if confidence not in CONFIDENCE_ORDER:
        raise InputValidationError("confidence must be low, medium or high.")
    violations: list[str] = []
    adjusted_rec, adjusted_alloc = rec, alloc
    limit = policy.max_single_position_pct[risk_tolerance]
    if rec != "INVEST" and alloc > 0:
        violations.append(f"{rec} recommendations must have 0% allocation.")
        adjusted_alloc = 0.0
    if rec == "INVEST" and alloc > limit:
        violations.append(f"Allocation {alloc:.0f}% exceeds the {limit:.0f}% single-position limit for {risk_tolerance} investors; capped.")
        adjusted_alloc = float(limit)
    if rec == "INVEST" and alloc == 0:
        violations.append("INVEST recommendation with 0% allocation; treated as HOLD.")
        adjusted_rec = "HOLD"
    if rec == "INVEST" and CONFIDENCE_ORDER[confidence] < CONFIDENCE_ORDER[policy.min_confidence_to_invest]:
        violations.append(f"INVEST requires at least '{policy.min_confidence_to_invest}' confidence; downgraded to HOLD.")
        adjusted_rec, adjusted_alloc = "HOLD", 0.0
    return {
        "input": {"recommendation": rec, "allocation_pct": alloc, "confidence": confidence, "risk_tolerance": risk_tolerance},
        "passed": not violations,
        "violations": violations,
        "adjusted_recommendation": adjusted_rec,
        "adjusted_allocation_pct": adjusted_alloc,
        "max_single_position_pct": limit,
    }


# ---------------------------------------------------------------------------
# Strands tools
# ---------------------------------------------------------------------------

@tool
def get_company_risks(ticker: str) -> dict[str, Any]:
    """Get documented business, regulatory, concentration, market and data-quality risks for a company.

    Args:
        ticker: Ticker from the workshop dataset.
    """
    return company_risks(ticker)


@tool(context=True)
def run_downside_scenarios(ticker: str, allocation_pct: float, tool_context: ToolContext) -> dict[str, Any]:
    """Stress-test a position: loss in the investor's currency if the share price falls 20%, 35% or 50%,
    plus an adverse currency move when the stock and the investor use different currencies.

    The budget and investor currency come from the committee request automatically.

    Args:
        ticker: Ticker from the workshop dataset.
        allocation_pct: Percent of the budget to stress-test (0-100).
    """
    ctx = get_shared_context(tool_context.invocation_state)
    r = ctx.request
    return downside_scenarios(ticker, r.budget, r.currency, allocation_pct)


@tool(context=True)
def check_concentration(allocation_pct: float, tool_context: ToolContext) -> dict[str, Any]:
    """Check a single-stock allocation against the committee's position limit for the investor's risk tolerance.

    Args:
        allocation_pct: Percent of the budget proposed for one stock (0-100).
    """
    ctx = get_shared_context(tool_context.invocation_state)
    return concentration_check(allocation_pct, ctx.request.risk_tolerance)


@tool(context=True)
def assess_currency_risk(ticker: str, tool_context: ToolContext) -> dict[str, Any]:
    """Assess currency mismatch (USD/PKR) and cross-border considerations for this investor and company.

    Args:
        ticker: Ticker from the workshop dataset.
    """
    ctx = get_shared_context(tool_context.invocation_state)
    r = ctx.request
    return currency_exposure(ticker, r.currency, r.investor_country)


@tool(context=True)
def check_investment_policy(recommendation: str, allocation_pct: float, confidence: str, tool_context: ToolContext) -> dict[str, Any]:
    """Check a draft recommendation against the committee investment policy before finalizing it.

    Args:
        recommendation: INVEST, HOLD or REJECT.
        allocation_pct: Percent of the budget proposed (0 unless INVEST).
        confidence: low, medium or high.
    """
    ctx = get_shared_context(tool_context.invocation_state)
    return investment_policy_check(recommendation, allocation_pct, confidence, ctx.request.risk_tolerance)
