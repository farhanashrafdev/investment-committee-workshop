"""Command-line entry point for the workshop.

    python main.py preflight              # credentials, model access, guardrail
    python main.py first-agent            # segment 2: a single Strands agent with tools
    python main.py analyze --ticker NVDA --budget 100000            # live Bedrock run
    python main.py analyze --ticker NVDA --budget 100000 --mock     # deterministic mock run
    python main.py security-demo          # untrusted document + guardrail verdict
    python main.py create-guardrail       # creates the workshop guardrail in your account
"""

from __future__ import annotations

import argparse
import json
import sys

from committee.config import get_settings


def _print_checks(checks) -> bool:
    ok = True
    for c in checks:
        mark = "PASS" if c.ok else "FAIL"
        ok &= c.ok
        print(f"[{mark}] {c.name}: {c.detail}")
        if c.hint:
            print(f"       -> {c.hint}")
    return ok


def cmd_preflight(args: argparse.Namespace) -> int:
    from committee.preflight import run_preflight

    s = get_settings()
    print(f"Region: {s.aws_region} | supervisor: {s.supervisor_model_id} | specialists: {s.specialist_model_id}")
    return 0 if _print_checks(run_preflight(s, invoke_models=not args.no_invoke)) else 1


def cmd_first_agent(args: argparse.Namespace) -> int:
    from agents.first_agent import run_first_agent

    run_first_agent(get_settings(), args.question) if args.question else run_first_agent(get_settings())
    return 0


def _parse_prices(items: list[str]) -> dict[str, dict]:
    """--price NVDA=150.25@2026-11-20@Nasdaq-close"""
    out = {}
    for item in items or []:
        tk, rest = item.split("=", 1)
        price, as_of, source = (rest.split("@") + ["", ""])[:3]
        out[tk.upper()] = {"price": float(price), "as_of": as_of, "source": source.replace("-", " ")}
    return out


def cmd_analyze(args: argparse.Namespace) -> int:
    from committee.orchestrator import run_committee
    from committee.report import generate_investment_report
    from observability.events import EventRecorder
    from security.input_validation import InputValidationError, build_request

    question = args.question or (
        f"Our investment committee has {args.budget:,.0f} {args.currency} available. Evaluate {', '.join(args.ticker)} "
        "using the provided financial dataset. Should we invest, hold, or reject the opportunity?"
    )
    try:
        req, scan = build_request(
            tickers=args.ticker, budget=args.budget, currency=args.currency, risk_tolerance=args.risk,
            horizon_years=args.horizon, question=question, target_return_pct=args.target_return,
            investor_country=args.country, price_inputs=_parse_prices(args.price), include_untrusted_documents=args.untrusted_docs,
        )
    except InputValidationError as exc:
        print(f"Input rejected: {exc}")
        return 2
    if scan.suspicious:
        print(f"WARNING: question flagged by deterministic scan: {scan.flags}")

    s = get_settings()
    mode = "mock" if args.mock else "live"
    print(("MOCK MODE (no Bedrock calls)" if args.mock else f"LIVE MODE on Amazon Bedrock ({s.aws_region})") + "\n")
    import uuid

    rec = EventRecorder(uuid.uuid4().hex[:12], simulated=args.mock, log_dir=s.log_dir)
    run = run_committee(req, mode=mode, settings=s, recorder=rec, mock_delay_s=0)
    for e in run.events:
        dur = f" ({e.duration_ms:.0f} ms)" if e.duration_ms else ""
        tok = f" tokens={e.tokens.get('totalTokens')}" if e.tokens else ""
        print(f"{e.elapsed_s:7.2f}s  {e.kind:<17} {e.agent:<32} {e.message}{dur}{tok}")
    print()
    if run.final:
        print(generate_investment_report(run.final))
    if run.error:
        print(f"\nERROR: {run.error}")
    print(f"\nDuration {run.duration_s}s | tokens: {run.total_tokens if run.total_tokens is not None else 'n/a (mock)'} | "
          f"cost: {('$' + str(run.total_cost_usd)) if run.total_cost_usd is not None else 'pricing not configured'}")
    if args.json:
        with open(args.json, "w", encoding="utf-8") as fh:
            fh.write(run.model_dump_json(indent=2))
        print(f"Run written to {args.json}")
    return 0 if run.final else 1


def cmd_security_demo(args: argparse.Namespace) -> int:
    from security.guardrails import build_checker
    from tools.company_data import third_party_documents

    s = get_settings()
    checker = build_checker(s)
    for doc in third_party_documents(args.ticker):
        print(f"== {doc['title']} ({doc['doc_id']}, synthetic={doc['synthetic']})")
        print("\nDETERMINISTIC LAYER (regex scan + quarantine, same result every time):")
        print(f"  flags: {doc['deterministic_flags']}")
        for line in doc["removed_lines"]:
            print(f"  removed: {line}")
        print("\nPROBABILISTIC LAYER (Amazon Bedrock Guardrails, ApplyGuardrail):")
        if checker:
            v = checker.check(doc["raw_text"], source="INPUT")
            print(f"  action: {v.action}")
            for r in v.reasons:
                print(f"  reason: {r}")
            if v.error:
                print(f"  error: {v.error}")
        else:
            print("  not configured. Run: python main.py create-guardrail, then set GUARDRAIL_ID in .env")
        print("\nWhat the agent receives:\n")
        print(doc["content"])
    return 0


def cmd_create_guardrail(args: argparse.Namespace) -> int:
    from security.guardrails import create_workshop_guardrail

    s = get_settings()
    out = create_workshop_guardrail(s.aws_region)
    print(json.dumps(out, indent=2))
    print(f"\nAdd to .env:\nGUARDRAIL_ID={out['guardrail_id']}\nGUARDRAIL_VERSION=DRAFT")
    return 0


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Investment Committee workshop CLI")
    sub = p.add_subparsers(dest="cmd", required=True)

    pf = sub.add_parser("preflight", help="Check credentials, Bedrock model access and guardrail")
    pf.add_argument("--no-invoke", action="store_true", help="Skip the tiny test invocation of each model")
    pf.set_defaults(func=cmd_preflight)

    fa = sub.add_parser("first-agent", help="Segment 2 demo: a single agent with two tools")
    fa.add_argument("--question", default=None)
    fa.set_defaults(func=cmd_first_agent)

    an = sub.add_parser("analyze", help="Run the full committee")
    an.add_argument("--ticker", nargs="+", default=["NVDA"])
    an.add_argument("--budget", type=float, default=100_000)
    an.add_argument("--currency", choices=["USD", "PKR"], default="USD")
    an.add_argument("--risk", choices=["conservative", "moderate", "aggressive"], default="moderate")
    an.add_argument("--horizon", type=int, default=5)
    an.add_argument("--country", choices=["PK", "US", "OTHER"], default="PK")
    an.add_argument("--target-return", type=float, default=None)
    an.add_argument("--question", default=None)
    an.add_argument("--price", action="append", help="Dated price for a US ticker: NVDA=150.25@2026-11-20@Nasdaq-close")
    an.add_argument("--untrusted-docs", action="store_true", help="Attach the synthetic poisoned documents (security demo)")
    an.add_argument("--mock", action="store_true", help="Deterministic mock mode (no Bedrock calls)")
    an.add_argument("--json", default=None, help="Write the full run to this JSON file")
    an.set_defaults(func=cmd_analyze)

    sd = sub.add_parser("security-demo", help="Show how an untrusted document is handled")
    sd.add_argument("--ticker", default="NVDA")
    sd.set_defaults(func=cmd_security_demo)

    cg = sub.add_parser("create-guardrail", help="Create the workshop Bedrock guardrail (one-time)")
    cg.set_defaults(func=cmd_create_guardrail)

    args = p.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
