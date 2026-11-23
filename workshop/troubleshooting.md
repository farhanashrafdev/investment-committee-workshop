# Troubleshooting and backup demo

Run `python main.py preflight` first: most problems show up there with a hint.

| Symptom | Cause | Fix |
|---|---|---|
| `Unable to locate credentials` | Not signed in | `aws login` (or `aws configure sso`), then retry |
| `Missing Dependency ... botocore[crt]` | `aws login` credentials need the CRT extra | `pip install "botocore[crt]"` |
| `ExpiredToken` / session expired mid-demo | Temporary credentials expired | `aws login` again; restart Streamlit |
| `AccessDeniedException ... not available for this account` | Model not enabled for the account | Enable it in the Bedrock console (us-east-1), or pick another Amazon model ID in `.env` |
| `AccessDeniedException` with your IAM user | Policy missing a statement | Attach `iam/bedrock-workshop-policy.json` with your account and guardrail IDs |
| `ValidationException: The provided model identifier is invalid` | Wrong Region for the profile | `AWS_REGION=us-east-1` in `.env` |
| `ThrottlingException` | Too many requests (shared account, many participants) | Wait 30-60 s; adaptive retries already back off. Run one analysis at a time. Switch to Mock for the demo. |
| Run stops with "time budget" | Watchdog fired after `RUN_BUDGET_S` | Retry; if it repeats, raise `RUN_BUDGET_S` or `BEDROCK_READ_TIMEOUT_S` |
| Supervisor failed, report says "deterministic fallback" | Supervisor model error after specialists finished | The report is still built from the live findings and labeled. Retry for a supervisor-written report. |
| A specialist card shows **Failed** | That agent's model call or structured output failed | The supervisor continues with the others; the gap appears in missing evidence. Retry. |
| Guardrail shows `NOT_CONFIGURED` | `GUARDRAIL_ID` empty | `python main.py create-guardrail`, copy ID to `.env`, restart |
| Guardrail `ERROR` | Wrong ID / Region / permission | Check `GUARDRAIL_ID`, Region, `bedrock:ApplyGuardrail` permission |
| Streamlit "Page not found" popup | Old bookmarked URL | Open http://localhost:8501 |
| Port 8501 in use | Another Streamlit running | `streamlit run app.py --server.port 8502` |
| PowerShell won't activate venv | Execution policy | `Set-ExecutionPolicy -Scope CurrentUser RemoteSigned` |
| `python` opens the Microsoft Store (Windows) | Store alias, Python not installed | `winget install -e --id Python.Python.3.12`, open a new terminal |
| Charts empty | No company selected | Select at least one ticker |
| Different wording each run | Models are not deterministic, even at temperature 0 | Expected. Numbers come from tools and do not change. |

## Backup demo plan (in order)

1. **Live, retry once.** Most failures are transient.
2. **Mock mode** (sidebar → *Mock (rehearsal, no AWS)*). Same UI, tools, data, security filters, policy gate and report code. Every screen says MOCK MODE and no tokens are shown. Say so out loud.
3. **CLI mock**: `python main.py analyze --ticker NVDA --mock --untrusted-docs`.
4. **Screenshots from a verified live run**: `workshop/assets/screenshots/` (captured with `scripts/capture_screenshots.py` against Bedrock on 2026-11-23).
5. **Saved live run JSON**: create one in rehearsal with `python main.py analyze --ticker NVDA --untrusted-docs --json rehearsal-run.json` and open it if everything else fails.

## Reset between runs

- Streamlit: refresh the browser tab (new session) or use the sidebar menu → *Clear cache*.
- Logs: `logs/run-*.jsonl` (safe to delete). Decisions: `audit/decisions.jsonl` (keep for the audit demo or delete).
