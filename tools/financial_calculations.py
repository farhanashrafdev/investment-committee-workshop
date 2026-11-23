"""Deterministic financial calculations.

The model never does arithmetic in this workshop. Agents call these tools, and
the formulas below produce every growth rate, margin and ratio in the report.
Each result includes the formula so participants can check it by hand.
"""

from __future__ import annotations

from typing import Any

import pandas as pd
from strands import ToolContext, tool

from committee.context import get_shared_context
from committee.schemas import PriceInput
from security.input_validation import validate_ticker, validate_tickers
from tools.dataset import Company, load_dataset


def _frame(company: Company) -> pd.DataFrame:
    df = pd.DataFrame([f.model_dump() for f in company.financials]).set_index("fiscal_year")
    return df.astype({c: "float64" for c in ["revenue", "operating_income", "net_income", "operating_cash_flow", "capital_expenditures", "diluted_eps"]})


def _pct(numerator: float | None, denominator: float | None) -> float | None:
    if numerator is None or denominator in (None, 0) or pd.isna(numerator) or pd.isna(denominator):
        return None
    return float(numerator) / float(denominator) * 100.0


def _clean(v: Any) -> float | None:
    return None if v is None or pd.isna(v) else round(float(v), 2)


# ---------------------------------------------------------------------------
# Pure functions
# ---------------------------------------------------------------------------

def revenue_growth(ticker: str) -> dict[str, Any]:
    c = load_dataset().company(validate_ticker(ticker))
    df = _frame(c)
    growth = df["revenue"].pct_change() * 100.0
    years = [
        {"fiscal_year": fy, "revenue": _clean(df.at[fy, "revenue"]), "yoy_growth_pct": _clean(growth.at[fy])}
        for fy in df.index
    ]
    first, last = df["revenue"].iloc[0], df["revenue"].iloc[-1]
    periods = len(df) - 1
    cagr = ((last / first) ** (1 / periods) - 1) * 100.0 if first and first > 0 and periods > 0 else None
    return {
        "ticker": c.ticker,
        "currency": c.currency,
        "years": years,
        "latest_yoy_growth_pct": _clean(growth.iloc[-1]),
        "cagr_pct": _clean(cagr),
        "cagr_period": f"{df.index[0]} to {df.index[-1]} ({periods} years)",
        "formula": "yoy_growth = (revenue_t / revenue_t-1 - 1) * 100; CAGR = ((last / first) ** (1 / years) - 1) * 100",
        "source_id": c.latest.source_id,
    }


def profit_margins(ticker: str) -> dict[str, Any]:
    c = load_dataset().company(validate_ticker(ticker))
    df = _frame(c)
    rows = []
    for fy, r in df.iterrows():
        fcf = None
        if not pd.isna(r["operating_cash_flow"]) and not pd.isna(r["capital_expenditures"]):
            fcf = r["operating_cash_flow"] - r["capital_expenditures"]
        rows.append({
            "fiscal_year": fy,
            "operating_margin_pct": _clean(_pct(r["operating_income"], r["revenue"])),
            "net_margin_pct": _clean(_pct(r["net_income"], r["revenue"])),
            "free_cash_flow": _clean(fcf),
            "fcf_margin_pct": _clean(_pct(fcf, r["revenue"])),
            "capex_to_revenue_pct": _clean(_pct(r["capital_expenditures"], r["revenue"])),
            "cash_conversion_pct": _clean(_pct(r["operating_cash_flow"], r["net_income"])),
        })
    latest = rows[-1]
    unavailable = [k for k, v in latest.items() if v is None and k != "fiscal_year"]
    return {
        "ticker": c.ticker,
        "currency": c.currency,
        "years": rows,
        "latest": latest,
        "unavailable_metrics": unavailable,
        "formula": "operating_margin = operating_income / revenue; net_margin = net_income / revenue; "
                   "free_cash_flow = operating_cash_flow - capital_expenditures; fcf_margin = FCF / revenue; "
                   "cash_conversion = operating_cash_flow / net_income (all x 100 for %)",
        "source_id": c.latest.source_id,
    }


def valuation_ratios(ticker: str, price_input: PriceInput | None = None) -> dict[str, Any]:
    """Market cap and P/E need a price. PSX prices come from the dataset snapshot;
    US prices must be supplied by the user (with date and source)."""
    c = load_dataset().company(validate_ticker(ticker))
    latest = c.latest
    price, price_as_of, price_source, origin = None, None, None, None
    if price_input is not None:
        price, price_as_of, price_source, origin = price_input.price, price_input.as_of, price_input.source, "user_supplied"
    elif c.market_snapshot is not None:
        snap = c.market_snapshot
        price, price_as_of, price_source, origin = snap.price, snap.as_of, snap.source_id, "dataset_snapshot"

    if price is None:
        return {
            "ticker": c.ticker,
            "status": "insufficient_data",
            "reason": "No share price available. The dataset has no live US prices; supply a dated price to calculate market cap and P/E.",
            "missing": ["share_price"],
        }

    shares = c.shares_outstanding.value if c.shares_outstanding else None
    market_cap = price * shares if shares else None
    eps = latest.diluted_eps
    pe = price / eps if eps and eps > 0 else None
    fcf = None
    if latest.operating_cash_flow is not None and latest.capital_expenditures is not None:
        fcf = latest.operating_cash_flow - latest.capital_expenditures
    result = {
        "ticker": c.ticker,
        "status": "ok",
        "currency": c.currency,
        "price": round(price, 4),
        "price_as_of": price_as_of,
        "price_origin": origin,
        "price_source": price_source,
        "shares_outstanding": shares,
        "market_cap": _clean(market_cap),
        "pe_on_latest_fy_eps": _clean(pe),
        "latest_fy": latest.fiscal_year,
        "latest_fy_diluted_eps": eps,
        "earnings_yield_pct": _clean(_pct(eps, price)) if eps else None,
        "fcf_yield_pct": _clean(_pct(fcf, market_cap)) if fcf is not None and market_cap else None,
        "formula": "market_cap = price * shares_outstanding; P/E = price / latest fiscal-year diluted EPS; "
                   "earnings_yield = EPS / price; fcf_yield = FCF / market_cap",
        "caveat": "P/E uses the latest fiscal-year EPS, not trailing-twelve-month EPS.",
    }
    if c.market_snapshot is not None and c.market_snapshot.pe_ttm is not None:
        result["exchange_reported_pe_ttm"] = c.market_snapshot.pe_ttm
        result["exchange_pe_note"] = c.market_snapshot.notes
    return result


def compare(tickers: list[str], price_inputs: dict[str, PriceInput] | None = None) -> dict[str, Any]:
    ds = load_dataset()
    fx = ds.market_context.fx
    rows = []
    for tk in validate_tickers(tickers):
        c = ds.company(tk)
        g, m = revenue_growth(tk), profit_margins(tk)
        v = valuation_ratios(tk, (price_inputs or {}).get(tk))
        to_usd = 1.0 if c.currency == "USD" else 1.0 / fx.rate
        rows.append({
            "ticker": tk,
            "name": c.name,
            "market": c.market,
            "latest_fy": c.latest.fiscal_year,
            "revenue_native": c.latest.revenue,
            "currency": c.currency,
            "revenue_usd": _clean(c.latest.revenue * to_usd) if c.latest.revenue else None,
            "net_income_usd": _clean(c.latest.net_income * to_usd) if c.latest.net_income else None,
            "latest_yoy_growth_pct": g["latest_yoy_growth_pct"],
            "cagr_pct": g["cagr_pct"],
            "operating_margin_pct": m["latest"]["operating_margin_pct"],
            "net_margin_pct": m["latest"]["net_margin_pct"],
            "fcf_margin_pct": m["latest"]["fcf_margin_pct"],
            "pe": v.get("pe_on_latest_fy_eps"),
        })
    df = pd.DataFrame(rows)
    leaders = {}
    for col in ["latest_yoy_growth_pct", "net_margin_pct", "operating_margin_pct", "fcf_margin_pct"]:
        s = df[col].dropna()
        if len(s) > 1:
            leaders[col] = df.loc[s.idxmax(), "ticker"]
    return {
        "companies": rows,
        "leaders": leaders,
        "fx_used": {"pair": fx.pair, "rate": fx.rate, "as_of": fx.as_of, "source_id": fx.source_id},
        "caveats": [
            "Fiscal years end in different months, so periods do not line up exactly.",
            "PKR figures are converted to USD at a single recent SBP rate for size comparison only; ratios are currency-neutral.",
        ],
    }


# ---------------------------------------------------------------------------
# Strands tools
# ---------------------------------------------------------------------------

@tool
def calculate_revenue_growth(ticker: str) -> dict[str, Any]:
    """Calculate year-over-year revenue growth and compound annual growth rate (CAGR) with pandas.

    Args:
        ticker: Ticker from the workshop dataset.
    """
    return revenue_growth(ticker)


@tool
def calculate_profit_margin(ticker: str) -> dict[str, Any]:
    """Calculate operating margin, net margin, free cash flow, FCF margin, capex intensity and cash conversion per year.

    Metrics that need data the dataset does not have are returned as null and listed in unavailable_metrics.

    Args:
        ticker: Ticker from the workshop dataset.
    """
    return profit_margins(ticker)


@tool(context=True)
def calculate_valuation_ratios(ticker: str, tool_context: ToolContext) -> dict[str, Any]:
    """Calculate market capitalization, P/E, earnings yield and FCF yield when a dated share price is available.

    Returns status 'insufficient_data' when no price is available. Never estimate a price yourself.

    Args:
        ticker: Ticker from the workshop dataset.
    """
    ctx = get_shared_context(tool_context.invocation_state)
    tk = validate_ticker(ticker)
    return valuation_ratios(tk, ctx.request.price_inputs.get(tk))


@tool(context=True)
def compare_companies(tickers: list[str], tool_context: ToolContext) -> dict[str, Any]:
    """Compare growth, margins and valuation across up to three companies, converting size to USD for comparison.

    Args:
        tickers: One to three tickers from the workshop dataset.
    """
    ctx = get_shared_context(tool_context.invocation_state)
    return compare(tickers, ctx.request.price_inputs)
