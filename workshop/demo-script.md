# Minute-by-minute demo script (60 minutes)

Checkpoints in **bold** are where you must be on time. If you are behind, use the "cut" notes.

| Time | Do | Say (short version) | Screen |
|---|---|---|---|
| 0:00 | Title slide | "Four AI agents, one investment question, and a human with the final say." | Slides 1-2 |
| 0:02 | Problem slide | Single prompts mix research, maths and judgment. Committees separate duties. | Slide 3 |
| 0:04 | Show finished result (screenshot) | "This is where we will be at minute 45." | Slide 4 |
| **0:05** | Open `agents/first_agent.py` | Model + prompt + tools = agent. | Editor |
| 0:07 | `python main.py first-agent` | Watch it call two tools. The 65.47% came from Python. | Terminal |
| 0:10 | Open `agents/model_factory.py` | Timeouts, adaptive retries, temperature 0. Why each matters live. | Editor |
| 0:12 | Open `tools/financial_calculations.py` | Formula in every result; tests compare with the 10-K. | Editor |
| 0:14 | Q: "Where did the number come from?" | | |
| **0:15** | Architecture slide | Supervisor + three specialists; agents as tools. | Slide 6 |
| 0:17 | `agents/prompts.py` | Risk Analyst is a challenger, not a cheerleader. | Editor |
| 0:20 | `agents/supervisor.py` `_run_specialist` | Model chooses; Python limits: scope, call count, time budget. | Editor |
| 0:23 | `python main.py analyze --ticker NVDA` | Read the timeline: parallel delegation, tool calls, follow-up, policy check. | Terminal |
| 0:27 | Point at tokens and cost line | Real Bedrock-reported tokens; cost only because pricing is configured. | Terminal |
| 0:29 | Q: "What if the supervisor did the maths?" | | |
| **0:30** | `committee/context.py` + `run_downside_scenarios` | Shared context: tools read the budget themselves. | Editor |
| 0:33 | `committee/schemas.py` | Structured outputs; the live bug story (null list, loop, Limits + watchdog). | Editor |
| 0:36 | PSX CLI run (SYS OGDC LUCK, PKR, 30% target) | Same agents, local market, goal check vs T-bill. | Terminal |
| 0:39 | `committee/report.py` `finalize_report` | Policy gate runs after the model. | Editor |
| **0:40** | Streamlit: Committee page | NVDA, $100k, moderate, 5y, untrusted docs ON → Analyze. | Browser |
| 0:41 | While it runs | Narrate cards: Waiting → Running → Completed. Show Section C. | Browser |
| 0:43 | Section D | Banner, bull/bear, disagreements, missing evidence, security notes. | Browser |
| 0:46 | Section E | Approve with your name; show `trade_executed: false`. | Browser |
| 0:47 | Ask page | Roman Urdu example → step 2 (rules, no AI) → goal chart → Ask committee. | Browser |
| **0:50** | Security Lab | Controls table: deterministic vs probabilistic. | Browser |
| 0:51 | Poisoned document | Raw vs received; *Ask the guardrail* → intervened. | Browser |
| 0:53 | AWS console | Guardrail test panel, CloudWatch AWS/Bedrock metrics, IAM policy. | Console |
| 0:54 | Say it | "Guardrails help; they are not a guarantee. The real control is that no tool can read credentials or trade." | |
| **0:55** | Production slide | AgentCore Runtime, Gateway, Memory, Observability, identity. | Slide 13 |
| 0:57 | Q&A | Use the questions in the instructor guide. | Slide 14 |
| 1:00 | Close | Repo link and participant guide. | Slide 15 |

## Cuts if you are late

- Behind at 0:15: skip `model_factory.py` (0:10) and the question at 0:14.
- Behind at 0:30: skip the CLI run at 0:23; the Streamlit demo shows the same timeline.
- Behind at 0:40: skip the PSX CLI run (0:36); show the PSX case on the Ask page instead.
- Behind at 0:50: skip the Ask page committee run; show only the goal check (instant, no model).
- If a live run fails: switch the sidebar to **Mock**, say "this is the labeled rehearsal mode", run again, then show `workshop/assets/screenshots/` from the verified live run.
