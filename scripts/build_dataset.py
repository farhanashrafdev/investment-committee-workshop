"""Rebuild the workshop dataset from public sources.

The workshop itself never calls these sources at runtime: it reads the JSON files
this script writes into ``data/``. Run this script only when you want to refresh
the snapshot (and then review the diff before committing).

Sources
- US companies: SEC EDGAR XBRL "companyfacts" API, values filtered to the
  accession number of each company's latest 10-K, plus verbatim excerpts from
  that 10-K document.
- Pakistan (PSX) companies: PSX Data Portal company pages (dps.psx.com.pk).
- Market context: State Bank of Pakistan homepage indicators and the
  U.S. Treasury daily par yield curve CSV.

Usage:
    python scripts/build_dataset.py
Set SEC_USER_AGENT to "Your Name your.email@example.com" (SEC fair-access policy).
"""

from __future__ import annotations

import csv
import html
import io
import json
import os
import re
import sys
import time
import urllib.request
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / "data"
TODAY = datetime.now(timezone.utc).date().isoformat()
SEC_UA = os.environ.get("SEC_USER_AGENT", "InvestmentCommitteeWorkshop dataset-build admin@example.com")
BROWSER_UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) InvestmentCommitteeWorkshop/1.0"


def fetch(url: str, ua: str, retries: int = 3) -> str:
    last: Exception | None = None
    for attempt in range(retries):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": ua, "Accept-Encoding": "identity"})
            with urllib.request.urlopen(req, timeout=60) as resp:
                body = resp.read()
            try:
                return body.decode("utf-8")
            except UnicodeDecodeError:
                # Some EDGAR documents are Windows-1252 encoded.
                return body.decode("cp1252", "replace")
        except Exception as exc:  # noqa: BLE001 - surface after retries
            last = exc
            time.sleep(2 ** attempt)
    raise RuntimeError(f"Failed to fetch {url}: {last}")


def html_to_text(raw: str) -> str:
    raw = re.sub(r"<script.*?</script>|<style.*?</style>", " ", raw, flags=re.S)
    text = html.unescape(re.sub(r"<[^>]+>", " ", raw))
    text = text.replace("\u00a0", " ")
    return re.sub(r"\s+", " ", text).strip()


def num(s: str) -> float:
    s = s.strip().replace(",", "")
    neg = s.startswith("(") and s.endswith(")")
    s = s.strip("()")
    v = float(s)
    return -v if neg else v


# ---------------------------------------------------------------------------
# US companies (SEC EDGAR)
# ---------------------------------------------------------------------------

US_COMPANIES: list[dict[str, Any]] = [
    {
        "ticker": "NVDA",
        "cik": "0001045810",
        "name": "NVIDIA Corporation",
        "exchange": "NASDAQ",
        "sector": "Semiconductors / Accelerated computing",
        "fiscal_year_end": "Last Sunday of January",
        "revenue_tags": ["Revenues"],
        "capex_tags": ["PaymentsToAcquireProductiveAssets", "PaymentsToAcquirePropertyPlantAndEquipment"],
        "description_pattern": r"NVIDIA pioneered accelerated computing[^.]{0,250}\.",
        "advantage_excerpts": [
            r"With our introduction of CUDA in 2006[^.]{0,300}\.",
        ],
        "risk_excerpts": [
            ("regulatory", r"Over the past three years, we have been subject to a series of shifting and expanding export control restrictions[^.]{0,250}\."),
            ("concentration", r"Three direct customers accounted for[^.]{0,200}\."),
            ("supply_chain", r"We utilize foundries, such as Taiwan Semiconductor Manufacturing Company Limited[^.]{0,200}\."),
        ],
        "advantage_summaries": [
            "Full-stack platform: GPUs, networking, systems and the CUDA software ecosystem that developers build on (workshop interpretation of the 10-K business section).",
            "A large developer ecosystem around CUDA can raise switching costs for customers (workshop interpretation, not a company statement).",
        ],
        "risk_summaries": [
            ("market", "Revenue depends heavily on data-center and AI infrastructure spending cycles; a slowdown in customer capex would hit growth (workshop interpretation)."),
            ("valuation", "Market expectations may already price in continued growth; valuation cannot be judged without a current share price (workshop interpretation)."),
        ],
    },
    {
        "ticker": "MSFT",
        "cik": "0000789019",
        "name": "Microsoft Corporation",
        "exchange": "NASDAQ",
        "sector": "Software / Cloud computing",
        "fiscal_year_end": "June 30",
        "revenue_tags": ["RevenueFromContractWithCustomerExcludingAssessedTax", "Revenues"],
        "capex_tags": ["PaymentsToAcquirePropertyPlantAndEquipment"],
        "description_pattern": r"Our mission is to empower every person and every organization on the planet to achieve more\.",
        "advantage_excerpts": [
            r"we have a long-term strategic partnership with OpenAI\.",
        ],
        "risk_excerpts": [
            ("competition", r"Our competitors are also scaling their infrastructure[^.]{0,200}\."),
            ("cybersecurity", r"[^.]{0,200}cyberattacks, and during elections or periods of intense diplomatic or armed conflict\."),
        ],
        "advantage_summaries": [
            "Diversified revenue across cloud infrastructure, productivity software, platforms and gaming (workshop interpretation of the 10-K segment structure).",
            "Large installed enterprise customer base for cross-selling cloud and AI services (workshop interpretation).",
        ],
        "risk_summaries": [
            ("financial", "Capital expenditure for AI datacenters has grown quickly; returns on that spending are not yet visible in this dataset (see calculated capex intensity)."),
            ("regulatory", "Size and market position attract regulatory and competition scrutiny in several jurisdictions (workshop interpretation)."),
        ],
    },
    {
        "ticker": "AMZN",
        "cik": "0001018724",
        "name": "Amazon.com, Inc.",
        "exchange": "NASDAQ",
        "sector": "E-commerce / Cloud computing",
        "fiscal_year_end": "December 31",
        "revenue_tags": ["RevenueFromContractWithCustomerExcludingAssessedTax", "Revenues"],
        "capex_tags": ["PaymentsToAcquireProductiveAssets", "PaymentsToAcquirePropertyPlantAndEquipment"],
        "description_pattern": r"We seek to be Earth.s most customer-centric company\.",
        "advantage_excerpts": [
            r"through AWS, which offers a broad set of on-demand technology services[^.]{0,250}\.",
        ],
        "risk_excerpts": [
            ("competition", r"The worldwide marketplace in which we compete is evolving rapidly and intensely competitive[^.]{0,200}\."),
            ("regulatory", r"[^.]{0,250}in violation of federal and state antitrust[^.]{0,120}\."),
        ],
        "advantage_summaries": [
            "Scale in logistics and fulfillment supports fast delivery and cost advantages in retail (workshop interpretation).",
            "AWS gives exposure to cloud infrastructure demand alongside the retail business (workshop interpretation).",
        ],
        "risk_summaries": [
            ("financial", "Very high capital expenditure relative to operating cash flow reduces free cash flow (see calculated FCF)."),
            ("margin", "Retail operations run on thin margins, so cost inflation or price competition can quickly affect profit (workshop interpretation)."),
        ],
    },
]

DURATION_DAYS = (340, 380)


def latest_10k(cik: str) -> dict[str, str]:
    sub = json.loads(fetch(f"https://data.sec.gov/submissions/CIK{cik}.json", SEC_UA))
    recent = sub["filings"]["recent"]
    for i, form in enumerate(recent["form"]):
        if form == "10-K":
            return {
                "accn": recent["accessionNumber"][i],
                "filed": recent["filingDate"][i],
                "period": recent["reportDate"][i],
                "doc": recent["primaryDocument"][i],
            }
    raise RuntimeError(f"No 10-K found for CIK {cik}")


def annual_values(facts: dict[str, Any], tags: list[str], accn: str, unit: str = "USD") -> dict[tuple[str, str], float]:
    for tag in tags:
        node = facts.get(tag)
        if not node or unit not in node["units"]:
            continue
        out: dict[tuple[str, str], float] = {}
        for row in node["units"][unit]:
            if row.get("accn") != accn or not row.get("start"):
                continue
            days = (date.fromisoformat(row["end"]) - date.fromisoformat(row["start"])).days
            if DURATION_DAYS[0] <= days <= DURATION_DAYS[1]:
                out[(row["start"], row["end"])] = float(row["val"])
        if out:
            return out
    return {}


def build_us_company(spec: dict[str, Any], sources: dict[str, dict[str, Any]]) -> dict[str, Any]:
    tk = spec["ticker"]
    filing = latest_10k(spec["cik"])
    facts = json.loads(fetch(f"https://data.sec.gov/api/xbrl/companyfacts/CIK{spec['cik']}.json", SEC_UA))
    gaap, dei = facts["facts"]["us-gaap"], facts["facts"]["dei"]
    accn = filing["accn"]
    accn_nodash = accn.replace("-", "")
    cik_int = str(int(spec["cik"]))
    doc_url = f"https://www.sec.gov/Archives/edgar/data/{cik_int}/{accn_nodash}/{filing['doc']}"

    src_xbrl = f"sec-xbrl-{tk.lower()}"
    src_doc = f"sec-10k-{tk.lower()}"
    sources[src_xbrl] = {
        "id": src_xbrl,
        "publisher": "U.S. Securities and Exchange Commission (EDGAR XBRL API)",
        "title": f"{spec['name']} company facts, values from 10-K accession {accn}",
        "url": f"https://data.sec.gov/api/xbrl/companyfacts/CIK{spec['cik']}.json",
        "filing_url": doc_url,
        "reporting_period_end": filing["period"],
        "filed": filing["filed"],
        "currency": "USD",
        "retrieved": TODAY,
        "notes": "Annual (fiscal-year) values as reported in the latest 10-K. Free cash flow is computed by the workshop as operating cash flow minus purchases of property and equipment.",
    }
    sources[src_doc] = {
        "id": src_doc,
        "publisher": "U.S. Securities and Exchange Commission (EDGAR)",
        "title": f"{spec['name']} Form 10-K for fiscal period ending {filing['period']}",
        "url": doc_url,
        "reporting_period_end": filing["period"],
        "filed": filing["filed"],
        "retrieved": TODAY,
        "notes": "Verbatim excerpts extracted programmatically from the 10-K text.",
    }

    revenue = annual_values(gaap, spec["revenue_tags"], accn)
    op_inc = annual_values(gaap, ["OperatingIncomeLoss"], accn)
    net_inc = annual_values(gaap, ["NetIncomeLoss"], accn)
    ocf = annual_values(gaap, ["NetCashProvidedByUsedInOperatingActivities"], accn)
    capex = annual_values(gaap, spec["capex_tags"], accn)
    eps = annual_values(gaap, ["EarningsPerShareDiluted"], accn, unit="USD/shares")

    years = []
    for (start, end) in sorted(revenue):
        fy_label = f"FY{date.fromisoformat(end).year}"
        years.append({
            "fiscal_year": fy_label,
            "period_start": start,
            "period_end": end,
            "revenue": revenue.get((start, end)),
            "operating_income": op_inc.get((start, end)),
            "net_income": net_inc.get((start, end)),
            "operating_cash_flow": ocf.get((start, end)),
            "capital_expenditures": capex.get((start, end)),
            "diluted_eps": eps.get((start, end)),
            "source_id": src_xbrl,
        })

    shares_rows = [r for r in dei.get("EntityCommonStockSharesOutstanding", {}).get("units", {}).get("shares", []) if r.get("accn") == accn]
    shares = None
    if shares_rows:
        total = sum(float(r["val"]) for r in shares_rows if r["end"] == shares_rows[0]["end"])
        shares = {"value": total, "as_of": shares_rows[0]["end"], "source_id": src_xbrl,
                  "notes": "Shares outstanding from the 10-K cover page (sum of share classes if more than one)."}

    text = html_to_text(fetch(doc_url, SEC_UA))

    def excerpt(pattern: str) -> str | None:
        m = re.search(pattern, text)
        return m.group(0).strip() if m else None

    desc = excerpt(spec["description_pattern"])
    if not desc:
        raise RuntimeError(f"{tk}: description excerpt not found")

    advantages = [{"text": s, "kind": "workshop_summary", "source_id": src_doc} for s in spec["advantage_summaries"]]
    for pat in spec["advantage_excerpts"]:
        ex = excerpt(pat)
        if ex:
            advantages.append({"text": ex, "kind": "filing_excerpt", "source_id": src_doc})
        else:
            print(f"WARNING {tk}: advantage excerpt not found: {pat}", file=sys.stderr)

    risks = []
    for cat, pat in spec["risk_excerpts"]:
        ex = excerpt(pat)
        if ex:
            risks.append({"category": cat, "text": ex, "kind": "filing_excerpt", "source_id": src_doc})
        else:
            print(f"WARNING {tk}: risk excerpt not found: {pat}", file=sys.stderr)
    for cat, s in spec["risk_summaries"]:
        risks.append({"category": cat, "text": s, "kind": "workshop_summary", "source_id": src_doc})

    return {
        "ticker": tk,
        "name": spec["name"],
        "market": "US",
        "exchange": spec["exchange"],
        "currency": "USD",
        "sector": spec["sector"],
        "fiscal_year_end": spec["fiscal_year_end"],
        "business_description": {"text": desc, "kind": "filing_excerpt", "source_id": src_doc},
        "financials": years,
        "shares_outstanding": shares,
        "market_snapshot": None,
        "competitive_advantages": advantages,
        "risks": risks,
        "data_gaps": [
            "No market share price in the dataset: market capitalization and P/E need a price you supply (with its date and source).",
            "Annual figures only; the most recent quarters are not included.",
            "Fiscal years differ between companies (NVIDIA ends in January, Microsoft in June, Amazon in December).",
        ],
    }


# ---------------------------------------------------------------------------
# Pakistan Stock Exchange companies (PSX Data Portal)
# ---------------------------------------------------------------------------

PSX_COMPANIES: list[dict[str, Any]] = [
    {
        "ticker": "SYS",
        "advantage_summaries": [
            "Software development and business process outsourcing services can grow with client demand without heavy fixed assets (workshop interpretation).",
        ],
        "risk_summaries": [
            ("currency", "Check how much revenue is earned in foreign currency in the latest annual report: PKR/USD moves affect reported results (not quantified in this dataset)."),
            ("talent", "IT services depend on retaining skilled staff; wage pressure can compress margins (workshop interpretation)."),
        ],
    },
    {
        "ticker": "OGDC",
        "advantage_summaries": [
            "Upstream oil and gas producer with a long operating history in Pakistan (company profile on PSX Data Portal).",
        ],
        "risk_summaries": [
            ("commodity", "Earnings depend on oil and gas prices and production volumes (workshop interpretation)."),
            ("receivables", "Verify receivables and collection risk in the annual report: this dataset does not include balance-sheet or cash-flow data."),
        ],
    },
    {
        "ticker": "LUCK",
        "advantage_summaries": [
            "Established cement manufacturer; scale can help with costs in a commodity-like industry (workshop interpretation).",
        ],
        "risk_summaries": [
            ("cyclical", "Cement demand follows construction and infrastructure cycles, which are sensitive to interest rates (workshop interpretation)."),
            ("energy_costs", "Energy is a major input cost for cement production (workshop interpretation)."),
        ],
    },
]

PSX_COMMON_GAPS = [
    "Operating income, operating cash flow and capital expenditure are not available from the PSX Data Portal summary: operating margin and free cash flow cannot be calculated.",
    "PSX Data Portal figures are standardized by Capital Stake and may differ from the issuer's annual report; P/E is based on unconsolidated financials.",
    "Historical EPS is not adjusted for bonus shares or splits, so EPS growth across years is not comparable.",
]


def build_psx_company(spec: dict[str, Any], sources: dict[str, dict[str, Any]]) -> dict[str, Any]:
    tk = spec["ticker"]
    url = f"https://dps.psx.com.pk/company/{tk}"
    raw = fetch(url, BROWSER_UA)
    text = html_to_text(raw)

    def rx(pattern: str, src: str = text, flags: int = 0) -> re.Match[str]:
        m = re.search(pattern, src, flags)
        if not m:
            raise RuntimeError(f"{tk}: pattern not found: {pattern}")
        return m

    name = html.unescape(rx(r'class="quote__name">([^<]+)<', raw).group(1)).strip()
    sector = html.unescape(rx(r'class="quote__sector"><span>([^<]+)<', raw).group(1)).strip()
    price = num(rx(r'class="quote__close">Rs\.([\d,\.]+)<', raw).group(1))
    as_of = rx(r'class="quote__date">\^?\s*As of ([^<]+)<', raw).group(1).strip()
    pe = num(rx(r"P/E Ratio \(TTM\) \*\* ([\d,\.\(\)]+)").group(1))
    mcap_k = num(rx(r"Market Cap \(000' ?s \) ([\d,\.]+)").group(1))
    shares = num(rx(r"Equity Profile Market Cap \(000' ?s \) [\d,\.]+ Shares ([\d,]+)").group(1))
    free_float_pct = num(rx(r"Free Float [\d,]+ Free Float ([\d\.]+)%").group(1))
    fye = rx(r"Fiscal Year End (\w+)").group(1)
    desc = rx(r"BUSINESS DESCRIPTION (.+?) KEY PEOPLE").group(1).strip()

    fin = rx(r"Financials All numbers in thousands \(000's\) except EPS Annual Quarterly "
             r"(\d{4}) (\d{4}) (\d{4}) (\d{4}) Sales ([\d,\.\(\)]+) ([\d,\.\(\)]+) ([\d,\.\(\)]+) ([\d,\.\(\)]+) "
             r"Profit after Taxation ([\d,\.\(\)]+) ([\d,\.\(\)]+) ([\d,\.\(\)]+) ([\d,\.\(\)]+) "
             r"EPS ([\d,\.\(\)]+) ([\d,\.\(\)]+) ([\d,\.\(\)]+) ([\d,\.\(\)]+)")
    g = fin.groups()
    yrs, sales, pat, eps = g[0:4], g[4:8], g[8:12], g[12:16]

    src = f"psx-dps-{tk.lower()}"
    sources[src] = {
        "id": src,
        "publisher": "Pakistan Stock Exchange Data Portal (data powered by Capital Stake)",
        "title": f"{name} ({tk}) company page",
        "url": url,
        "market_data_as_of": as_of,
        "currency": "PKR",
        "retrieved": TODAY,
        "notes": "Financial figures are shown in PKR thousands on the portal and converted to PKR here. The portal says data may be standardized and differ from the issuer's annual report.",
    }

    years = []
    for y, s, p, e in sorted(zip(yrs, sales, pat, eps)):
        years.append({
            "fiscal_year": f"FY{y}",
            "period_start": None,
            "period_end": None,
            "revenue": num(s) * 1000,
            "operating_income": None,
            "net_income": num(p) * 1000,
            "operating_cash_flow": None,
            "capital_expenditures": None,
            "diluted_eps": num(e),
            "source_id": src,
        })

    advantages = [{"text": s, "kind": "workshop_summary", "source_id": src} for s in spec["advantage_summaries"]]
    risks = [{"category": c, "text": s, "kind": "workshop_summary", "source_id": src} for c, s in spec["risk_summaries"]]
    risks.append({"category": "liquidity", "text": f"Free float is {free_float_pct:.0f}% of shares according to the PSX Data Portal; lower free float can mean lower trading liquidity.", "kind": "data_point", "source_id": src})

    return {
        "ticker": tk,
        "name": name,
        "market": "PK",
        "exchange": "PSX",
        "currency": "PKR",
        "sector": sector.title(),
        "fiscal_year_end": fye,
        "business_description": {"text": desc, "kind": "exchange_profile", "source_id": src},
        "financials": years,
        "shares_outstanding": {"value": shares, "as_of": as_of, "source_id": src, "notes": "Shares from PSX Data Portal equity profile."},
        "market_snapshot": {
            "price": price,
            "currency": "PKR",
            "as_of": as_of,
            "market_cap": mcap_k * 1000,
            "pe_ttm": pe,
            "free_float_pct": free_float_pct,
            "source_id": src,
            "notes": "Snapshot captured when the dataset was built. P/E (TTM) as published by the portal, based on unconsolidated financials.",
        },
        "competitive_advantages": advantages,
        "risks": risks,
        "data_gaps": list(PSX_COMMON_GAPS),
    }


# ---------------------------------------------------------------------------
# Market context
# ---------------------------------------------------------------------------

def build_market_context(sources: dict[str, dict[str, Any]]) -> dict[str, Any]:
    sbp_url = "https://www.sbp.org.pk/ecodata/rates/m2m/M2M-Current.asp"
    t = html_to_text(fetch(sbp_url, BROWSER_UA))
    policy = num(re.search(r"SBP Policy Rate ([\d\.]+)% p\.a\.", t).group(1))
    m2m = re.search(r"USD/ ?PKR Rates As on ([\w\- ]+?) M2M Revaluation Rate ([\d\.]+)", t)
    mtb12 = re.search(r"Cut-off Rates in the Latest Auctions MTBs Tenor Cut-off Yield .*?12-M ([\d\.]+)% \(as on ([^)]+)\)", t)
    sources["sbp-indicators"] = {
        "id": "sbp-indicators",
        "publisher": "State Bank of Pakistan",
        "title": "SBP key indicators (policy rate, USD/PKR M2M revaluation rate, T-bill cut-off yields)",
        "url": sbp_url,
        "retrieved": TODAY,
        "notes": "Values as displayed on the SBP website on the retrieval date.",
    }

    year = datetime.now(timezone.utc).year
    ust_url = ("https://home.treasury.gov/resource-center/data-chart-center/interest-rates/daily-treasury-rates.csv/"
               f"{year}/all?type=daily_treasury_yield_curve&field_tdr_date_value={year}&page&_format=csv")
    rows = list(csv.DictReader(io.StringIO(fetch(ust_url, SEC_UA))))
    latest = rows[0]
    sources["ust-yield-curve"] = {
        "id": "ust-yield-curve",
        "publisher": "U.S. Department of the Treasury",
        "title": "Daily Treasury Par Yield Curve Rates",
        "url": ust_url,
        "retrieved": TODAY,
        "notes": f"Latest row in the CSV on the retrieval date ({latest['Date']}).",
    }

    return {
        "fx": {
            "pair": "USD/PKR",
            "rate": num(m2m.group(2)),
            "description": "SBP M2M revaluation rate (PKR per 1 USD)",
            "as_of": m2m.group(1).strip(),
            "source_id": "sbp-indicators",
        },
        "rates": {
            "PK_policy_rate_pct": {"value": policy, "as_of": TODAY, "source_id": "sbp-indicators"},
            "PK_tbill_12m_cutoff_pct": {"value": num(mtb12.group(1)), "as_of": mtb12.group(2).strip(), "source_id": "sbp-indicators"},
            "US_treasury_1y_pct": {"value": num(latest["1 Yr"]), "as_of": latest["Date"], "source_id": "ust-yield-curve"},
            "US_treasury_10y_pct": {"value": num(latest["10 Yr"]), "as_of": latest["Date"], "source_id": "ust-yield-curve"},
        },
    }


def main() -> None:
    sources: dict[str, dict[str, Any]] = {}
    companies = []
    for spec in US_COMPANIES:
        print(f"SEC {spec['ticker']} ...")
        companies.append(build_us_company(spec, sources))
        time.sleep(0.5)
    for spec in PSX_COMPANIES:
        print(f"PSX {spec['ticker']} ...")
        companies.append(build_psx_company(spec, sources))
    print("Market context ...")
    market = build_market_context(sources)

    dataset = {
        "dataset_version": TODAY,
        "built_at_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "disclaimer": "Educational workshop dataset. Figures come from the cited public sources as of the retrieval date; qualitative items marked 'workshop_summary' are interpretations written for the workshop, not company statements.",
        "companies": companies,
        "market_context": market,
    }
    DATA_DIR.mkdir(exist_ok=True)
    (DATA_DIR / "companies.json").write_text(json.dumps(dataset, indent=2, ensure_ascii=False), encoding="utf-8")
    (DATA_DIR / "sources.json").write_text(json.dumps({"retrieved": TODAY, "sources": list(sources.values())}, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"Wrote {len(companies)} companies and {len(sources)} sources to {DATA_DIR}")


if __name__ == "__main__":
    main()
