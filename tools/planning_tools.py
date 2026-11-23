"""Goal tools for "I have X and want Y% return" questions.

parse_goal_question() is a deterministic parser (regular expressions, no model),
so the amount and target the committee works with are exactly what the user
typed, and the user confirms them in the UI before any agent runs.
"""

from __future__ import annotations

import math
import re
from typing import Any

from strands import ToolContext, tool

from committee.context import get_shared_context
from security.input_validation import InputValidationError
from tools.dataset import load_dataset, load_policy

_MULT = {"k": 1e3, "thousand": 1e3, "lakh": 1e5, "lac": 1e5, "lakhs": 1e5, "crore": 1e7, "crores": 1e7, "cr": 1e7,
         "m": 1e6, "mn": 1e6, "million": 1e6, "b": 1e9, "bn": 1e9, "billion": 1e9, "arab": 1e9}
_PKR_WORDS = r"(?:\bpkr|\brs\.?|\brupees?|\brupay|\brupaye)"
_USD_WORDS = r"(?:\busd|\bus\$|\$|\bdollars?)"
_NUM = r"(\d{1,3}(?:,\d{2,3})+(?:\.\d+)?|\d+(?:\.\d+)?)"
_UNIT = r"(k|thousand|lakhs?|lac|crores?|cr|million|mn|m|billion|bn|b|arab)?\b"


def _to_number(num: str, unit: str | None) -> float:
    return float(num.replace(",", "")) * _MULT.get((unit or "").lower(), 1.0)


def parse_goal_question(text: str) -> dict[str, Any]:
    t = text.lower()
    out: dict[str, Any] = {"amount": None, "currency": None, "target_return_pct": None, "horizon_years": None,
                           "risk_tolerance": None, "markets": [], "notes": []}

    m = (re.search(rf"{_PKR_WORDS}\s*{_NUM}\s*{_UNIT}", t)
         or re.search(rf"{_NUM}\s*{_UNIT}\s*{_PKR_WORDS}", t))
    if m:
        out["amount"], out["currency"] = _to_number(m.group(1), m.group(2)), "PKR"
    else:
        m = (re.search(rf"{_USD_WORDS}\s*{_NUM}\s*{_UNIT}", t)
             or re.search(rf"{_NUM}\s*{_UNIT}\s*{_USD_WORDS}", t))
        if m:
            out["amount"], out["currency"] = _to_number(m.group(1), m.group(2)), "USD"
        else:
            m = re.search(rf"{_NUM}\s*(lakhs?|lac|crores?|cr|arab)\b", t)
            if m:
                out["amount"], out["currency"] = _to_number(m.group(1), m.group(2)), "PKR"
                out["notes"].append("Currency inferred as PKR from lakh/crore wording.")

    years_m = re.search(r"(\d+(?:\.\d+)?)\s*(years?|yrs?|saal)\b", t)
    months_m = re.search(r"(\d+)\s*months?\b", t)
    if years_m:
        out["horizon_years"] = max(1, round(float(years_m.group(1))))
    elif months_m:
        out["horizon_years"] = max(1, math.ceil(int(months_m.group(1)) / 12))

    pct = re.search(r"(\d+(?:\.\d+)?)\s*(%|percent|per cent|fees?ad)", t)
    if pct:
        out["target_return_pct"] = float(pct.group(1))
        if re.search(r"(monthly|per month|a month)", t):
            out["notes"].append("A monthly target was stated; it is treated as an annual figure only after you confirm it.")
    elif re.search(r"\b(double|2x|twice)\b", t):
        yrs = out["horizon_years"] or 5
        out["target_return_pct"] = round((2 ** (1 / yrs) - 1) * 100, 2)
        out["notes"].append(f"'Double' converted to {out['target_return_pct']}% a year over {yrs} years.")
    elif re.search(r"\b(triple|3x)\b", t):
        yrs = out["horizon_years"] or 5
        out["target_return_pct"] = round((3 ** (1 / yrs) - 1) * 100, 2)
        out["notes"].append(f"'Triple' converted to {out['target_return_pct']}% a year over {yrs} years.")

    if re.search(r"\b(low risk|safe|conservative|no risk|secure)\b", t):
        out["risk_tolerance"] = "conservative"
    elif re.search(r"\b(high risk|aggressive|risky|maximum return)\b", t):
        out["risk_tolerance"] = "aggressive"
    elif re.search(r"\b(medium risk|moderate|balanced)\b", t):
        out["risk_tolerance"] = "moderate"

    if re.search(r"\b(pakistan|psx|local|kse|karachi)\b", t):
        out["markets"].append("PK")
    if re.search(r"\b(usa|u\.s\.|american|international|global|abroad|nasdaq|foreign|us (?:stocks?|markets?|tech|companies|shares))\b", t):
        out["markets"].append("US")
    return out


def risk_free_benchmark(currency: str) -> dict[str, Any]:
    ds = load_dataset()
    key = "PK_tbill_12m_cutoff_pct" if currency == "PKR" else "US_treasury_1y_pct"
    rate = ds.market_context.rates[key]
    label = "Pakistan 12-month T-bill cut-off yield" if currency == "PKR" else "US 1-year Treasury par yield"
    return {"name": label, "rate_pct": rate.value, "as_of": rate.as_of, "source_id": rate.source_id}


def assess_return_target(budget: float, currency: str, target_return_pct: float, horizon_years: int) -> dict[str, Any]:
    if budget <= 0:
        raise InputValidationError("budget must be positive.")
    if not 0 <= target_return_pct <= 100:
        raise InputValidationError("target_return_pct must be between 0 and 100.")
    if not 1 <= horizon_years <= 30:
        raise InputValidationError("horizon_years must be between 1 and 30.")
    policy = load_policy()
    rf = risk_free_benchmark(currency)
    excess = target_return_pct - rf["rate_pct"]
    bands = policy.target_return_bands_pp_over_risk_free
    if excess <= bands["low_risk_max"]:
        band, text = "low_risk_possible", "The target is at or below the current risk-free benchmark, so it may be reachable without taking stock-market risk."
    elif excess <= bands["equity_risk_max"]:
        band, text = "requires_equity_risk", "The target is above the risk-free benchmark, so it requires taking investment risk, with a real chance of losses."
    elif excess <= bands["speculative_above"]:
        band, text = "high_risk", "The target is well above the risk-free benchmark and needs a concentrated or high-risk approach. Losses can be large."
    else:
        band, text = "speculative", "The target is far above the risk-free benchmark. Treat promises of returns like this as a warning sign."
    target_value = budget * (1 + target_return_pct / 100) ** horizon_years
    rf_value = budget * (1 + rf["rate_pct"] / 100) ** horizon_years
    return {
        "budget": budget,
        "currency": currency,
        "target_return_pct": target_return_pct,
        "horizon_years": horizon_years,
        "target_ending_value": round(target_value, 2),
        "risk_free_benchmark": rf,
        "risk_free_ending_value": round(rf_value, 2),
        "excess_return_needed_pp": round(excess, 2),
        "band": band,
        "assessment": text,
        "caveats": [
            "Nominal returns: inflation is not included.",
            "Taxes and fees are not included.",
            "The benchmark rate changes over time; this is the value on the dataset date.",
            "Band thresholds are the committee's workshop policy, not market facts.",
        ],
        "formula": "ending_value = budget * (1 + rate) ** years",
    }


@tool(context=True)
def assess_investment_goal(tool_context: ToolContext) -> dict[str, Any]:
    """Compare the investor's target return with the current risk-free benchmark (PK T-bill or US Treasury)
    and calculate the ending value the target implies. Reads the budget, currency, target and horizon from the request.
    """
    r = get_shared_context(tool_context.invocation_state).request
    if r.target_return_pct is None:
        return {"status": "no_target", "message": "The investor did not state a target return."}
    return assess_return_target(r.budget, r.currency, r.target_return_pct, r.horizon_years)
