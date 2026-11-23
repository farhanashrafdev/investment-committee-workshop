# Dry-run checklist

Do this once 2 days before and again 1 hour before. Tick every line.

## Environment
- [ ] `python --version` is 3.11+; `pip install -r requirements.txt` completes
- [ ] `python -m pytest -q` passes (offline suite)
- [ ] `aws login` done with a **non-root** identity that has `iam/bedrock-workshop-policy.json`
- [ ] `python main.py preflight` shows PASS for credentials, both models and the guardrail
- [ ] `.env` has `GUARDRAIL_ID` and `MODEL_PRICING_JSON`
- [ ] `RUN_LIVE_TESTS=1 python -m pytest -m live -q` passes

## Demo path
- [ ] `python main.py first-agent` answers with tool calls (under 10 s)
- [ ] `python main.py analyze --ticker NVDA --untrusted-docs` completes with a supervisor-written report (under 60 s)
- [ ] Streamlit committee page: live run completes; four cards end in Completed; approval records a decision
- [ ] Ask page: Roman Urdu example parses to 10 lac / 25% / 3 years; committee answers
- [ ] Security Lab: *Ask the guardrail* returns `GUARDRAIL_INTERVENED`
- [ ] Mock mode run works with Wi-Fi turned off
- [ ] Saved a rehearsal run with `--json rehearsal-run.json`
- [ ] AWS console tabs open: Bedrock guardrail, CloudWatch AWS/Bedrock metrics, IAM policy

## Presentation
- [ ] Terminal font 18pt+, browser zoom 125%, notifications off
- [ ] Slides open on `workshop/slides/investment-committee-workshop.pptx`
- [ ] Repo URL on the last slide is reachable by participants (make the repo public or invite them)

## Known live-demo risks and mitigations

| Risk | Likelihood | Mitigation |
|---|---|---|
| Credentials expire during the session | Medium (`aws login` sessions are temporary) | Sign in again right before; keep a terminal ready |
| Bedrock throttling with many participants on one account | Medium | Participants use their own accounts or mock mode; adaptive retries |
| Model returns invalid structured output | Low after fixes | Lenient validators, `Limits(structured_output_attempts=3)`, deterministic fallback report |
| Run takes too long | Low (18-25 s measured) | `RUN_BUDGET_S` watchdog; narrate Section C while waiting |
| Different recommendation than rehearsed | Medium | Say it explicitly: wording and stance vary; numbers and policy limits do not |
| Venue network blocks AWS | Low-medium | Mock mode and screenshots |
| Guardrail missing in a participant's account | High for participants | Deterministic layer still works; guardrail is optional |
| Least-privilege policy missing a permission | Unknown: not yet exercised with a real role | Test it in this dry run with the `ic-workshop` user |
| Root credentials used for the demo | Present today | Switch to the IAM user before the workshop |

## Status of this repository (2026-11-23)

Verified by running it:
- Offline test suite (88 tests including headless Streamlit pages)
- Live end-to-end runs on Bedrock (CLI, pytest live test, and the Streamlit UI driven by a headless browser)
- Guardrail creation and `ApplyGuardrail` intervention on the poisoned document
- IAM policies validated by Access Analyzer (zero findings)

Not yet verified:
- Running under the least-privilege IAM policy (all runs used the account root credentials)
- macOS and Linux setup commands (written from standard Python practice; tested only on Windows Server)
- OpenTelemetry export to a real collector (configuration path only)
