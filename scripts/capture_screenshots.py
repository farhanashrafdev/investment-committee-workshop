"""Drive the running Streamlit app with a headless browser and save screenshots.

Used to verify the UI end to end (including a LIVE Bedrock run) and to produce
images for the README and slide deck.

    streamlit run app.py            # in one terminal
    python scripts/capture_screenshots.py [--mock]
Requires: pip install playwright && python -m playwright install chromium
"""

from __future__ import annotations

import argparse
import time
from pathlib import Path

from playwright.sync_api import Page, sync_playwright

OUT = Path(__file__).resolve().parent.parent / "workshop" / "assets" / "screenshots"
URL = "http://localhost:8501"


def wait_idle(page: Page, timeout_s: int = 240) -> None:
    """Wait until Streamlit stops running the script (the status widget disappears)."""
    deadline = time.time() + timeout_s
    time.sleep(1.5)
    while time.time() < deadline:
        if page.locator('[data-testid="stStatusWidget"]').count() == 0:
            time.sleep(1.0)
            if page.locator('[data-testid="stStatusWidget"]').count() == 0:
                return
        time.sleep(1.0)
    raise TimeoutError("Streamlit app did not become idle")


def shot(page: Page, name: str, at_text: str | None = None) -> None:
    """Screenshot the viewport. Streamlit scrolls inside its own container, so
    scroll the target heading into view instead of using full_page."""
    OUT.mkdir(parents=True, exist_ok=True)
    if at_text:
        page.get_by_text(at_text, exact=False).first.evaluate("e => e.scrollIntoView({block: 'start'})")
        time.sleep(0.8)
    page.screenshot(path=str(OUT / f"{name}.png"))
    print("saved", name)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--mock", action="store_true")
    args = ap.parse_args()
    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(viewport={"width": 1500, "height": 1000})

        page.goto(URL)
        wait_idle(page)
        if args.mock:
            page.get_by_text("Mock (rehearsal, no AWS)").click()
            wait_idle(page)
        shot(page, "01-committee-request")
        page.get_by_text("Attach untrusted third-party documents").click()
        time.sleep(0.5)
        page.get_by_role("button", name="Analyze with the committee").click()
        time.sleep(7)
        shot(page, "02-agents-running", "B · Agent execution")
        wait_idle(page, 300)
        shot(page, "03-agents-completed", "B · Agent execution")
        shot(page, "04-financial-analysis", "C · Financial analysis")
        shot(page, "05-committee-report", "D · Investment committee report")
        shot(page, "06-report-details", "Specialist stances and disagreements")
        page.get_by_label("Reviewer name").fill("Workshop Instructor")
        page.get_by_role("button", name="Approve recommendation").click()
        wait_idle(page)
        shot(page, "07-approval-recorded", "E · Human approval")

        page.goto(f"{URL}/ask")
        wait_idle(page)
        page.get_by_text("I have 5 lakh rupees and want 30% return in 2 years. Pakistani stocks, low risk.", exact=True).first.click()
        wait_idle(page)
        page.get_by_role("button", name="1 · Understand my question").click()
        wait_idle(page)
        shot(page, "08-ask-goal-check", "2 · Check what we understood")
        page.get_by_role("button", name="3 · Ask the AI committee").click()
        wait_idle(page, 300)
        shot(page, "09-ask-answer", "Committee answer")

        page.goto(f"{URL}/security")
        wait_idle(page)
        shot(page, "10-security-controls")
        shot(page, "11-security-document", "1 · The poisoned document")
        if page.get_by_role("button", name="Ask the guardrail").count():
            page.get_by_role("button", name="Ask the guardrail").click()
            wait_idle(page)
        shot(page, "12-security-detection", "2 · Two layers of detection")

        page.goto(f"{URL}/architecture")
        wait_idle(page)
        shot(page, "13-architecture")
        browser.close()


if __name__ == "__main__":
    main()
