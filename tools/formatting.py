"""Number formatting helpers shared by tools, reports and the UI."""

from __future__ import annotations


def round_or_none(value: float | None, digits: int = 2) -> float | None:
    return None if value is None else round(float(value), digits)


def fmt_money(value: float | None, currency: str) -> str:
    if value is None:
        return "n/a"
    sign = "-" if value < 0 else ""
    v = abs(value)
    prefix = "$" if currency == "USD" else "PKR "
    for div, suffix in ((1e12, "T"), (1e9, "B"), (1e6, "M"), (1e3, "K")):
        if v >= div:
            return f"{sign}{prefix}{v / div:,.2f}{suffix}"
    return f"{sign}{prefix}{v:,.2f}"


def fmt_pkr_crore(value: float | None) -> str:
    """Pakistani convention: 1 crore = 10,000,000; 1 lakh = 100,000."""
    if value is None:
        return "n/a"
    if abs(value) >= 1e7:
        return f"PKR {value / 1e7:,.2f} crore"
    if abs(value) >= 1e5:
        return f"PKR {value / 1e5:,.2f} lakh"
    return f"PKR {value:,.0f}"


def fmt_pct(value: float | None, digits: int = 1) -> str:
    return "n/a" if value is None else f"{value:.{digits}f}%"
