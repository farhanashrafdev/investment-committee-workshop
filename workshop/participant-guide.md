# Participant guide

You will run an AI investment committee on your machine. Four agents (one supervisor, three specialists) call Amazon Nova models on Amazon Bedrock and use Python tools on a dated dataset of US and Pakistan Stock Exchange companies.

> Educational only. The app cannot trade and its output is not financial advice.

## 1. Set up (10 minutes)

You need Python 3.11+, Git, and either AWS credentials with Bedrock access (us-east-1) or nothing at all (mock mode).

**macOS / Linux**

```bash
git clone https://github.com/farhanashrafdev/investment-committee-workshop.git
cd investment-committee-workshop
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
```

**Windows PowerShell**

```powershell
git clone https://github.com/farhanashrafdev/investment-committee-workshop.git
cd investment-committee-workshop
py -3.12 -m venv .venv; .\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
Copy-Item .env.example .env
```

If PowerShell blocks activation: `Set-ExecutionPolicy -Scope CurrentUser RemoteSigned`.

**AWS credentials** (skip for mock mode): `aws login` (or `aws configure sso`), then `python main.py preflight`. All lines should say PASS.

## 2. Run your first agent

```bash
python main.py first-agent
python main.py first-agent --question "Compare Microsoft's latest operating margin with the year before."
```

Look at the footer: which tools were called, and how many tokens Bedrock reported.

## 3. Run the committee

```bash
python main.py analyze --ticker NVDA --budget 100000          # live
python main.py analyze --ticker NVDA --mock                   # no AWS needed
```

Read the timeline: delegations, tool calls (with milliseconds), structured outputs, the policy check.

## 4. Use the web app

```bash
streamlit run app.py
```

- **Investment Committee**: pick companies, budget, risk and horizon. Watch the four agent cards. Approve or reject at the end.
- **Ask the Committee**: type a goal such as `Mere paas 10 lac rupay hain, 25% chahiye 3 saal mein`. Check the parsed values, read the goal check, then ask the committee.
- **Security Lab**: see how a poisoned document is handled.

## 5. Experiments

1. **Change the risk tolerance** from moderate to conservative for NVDA. Does the recommendation or allocation change? Look for "Deterministic adjustments".
2. **Supply a price.** In Section A, open *dated share prices*, enter a price you looked up for MSFT with its date and source. Valuation should now appear instead of "missing evidence".
3. **Compare markets.** Analyze `MSFT`, `SYS` and `LUCK` together in PKR. Note how USD/PKR conversion and currency risk show up.
4. **Unrealistic goal.** Ask: `I have 2 lakh and want 100% return in 1 year`. What does the goal check say?
5. **Attack it.** In the Security Lab, write your own injection and see which layer catches it.
6. **Break a tool.** In `tools/financial_calculations.py`, make `revenue_growth` raise an error for one ticker, run again, and see how the failure shows up in the timeline and in "missing evidence". Undo afterwards.
7. **Change the policy.** Edit `data/investment_policy.json` (for example `moderate: 5`) and rerun. The cap is applied in Python after the model answers.
8. **Read the tests.** `python -m pytest -q`, then open `tests/test_financial_calculations.py`.

## 6. Questions to think about

- Which steps are decided by a model, and which by code?
- What would you need before letting this system act without a human?
- Where would you add your own data sources safely?
