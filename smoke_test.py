"""
Hindsight smoke test for the AP Exception Agent.

Checks, in order:
  1. Connection and API version
  2. Bank creation with mission + disposition
  3. Retain with backdated timestamps and vendor tags
  4. Recall filtered by vendor tag
  5. Reflect: does it notice the Meridian contract change?

Setup:
  pip install hindsight-client
  export HINDSIGHT_API_URL="https://api.hindsight.vectorize.io"   # or http://localhost:8888
  export HINDSIGHT_API_KEY="your-key"                              # from Hindsight Cloud
  python smoke_test.py
"""

import os
import sys
import time
from datetime import datetime

from hindsight_client import Hindsight

BANK_ID = "ap-smoke-test"
VENDOR_TAG = "vendor:meridian-logistics"

MEMORIES = [
    (datetime(2026, 3, 14),
     "Invoice MER-2291 from Meridian Logistics, total $12,450. Exception: freight line of "
     "$480 not on PO-7731. Reviewer Priya Nair approved: Meridian bills freight separately "
     "per their 2025 contract, this is expected."),
    (datetime(2026, 4, 11),
     "Invoice MER-2340 from Meridian Logistics, total $9,870. Exception: freight line of "
     "$395 not on PO-7802. Reviewer Priya Nair approved, same freight pattern as before."),
    (datetime(2026, 5, 16),
     "Invoice MER-2418 from Meridian Logistics, total $14,120. Exception: freight line of "
     "$510 not on PO-7890. Reviewer Arjun Mehta approved, known Meridian freight billing."),
    (datetime(2026, 8, 1),
     "Meridian Logistics signed a new contract effective 2026-08-01. Freight is now "
     "included in the PO unit price. Separate freight lines are no longer valid."),
]

NEW_INVOICE = (
    "Invoice MER-2603 from Meridian Logistics, dated 2026-09-12, total $11,300. "
    "Exception: freight line of $450 not on PO-8120."
)


def step(msg):
    print(f"\n=== {msg} ===")


def main():
    url = os.environ.get("HINDSIGHT_API_URL", "http://localhost:8888")
    key = os.environ.get("HINDSIGHT_API_KEY")
    client = Hindsight(base_url=url, api_key=key, timeout=120.0)

    step("1. Connection")
    version = client.get_version()
    print(f"Connected to {url}, API version {version.api_version}")

    step("2. Create bank")
    try:
        client.create_bank(
            bank_id=BANK_ID,
            name="AP Smoke Test",
            mission=(
                "You are the institutional memory of an accounts payable team. "
                "Prioritize past exception resolutions, reviewer rationale, recurring "
                "vendor patterns, and contract changes that invalidate old patterns."
            ),
            disposition={"skepticism": 4, "literalism": 4, "empathy": 2},
        )
        print("Bank created")
    except Exception as e:
        print(f"Bank create skipped (may already exist): {e}")

    step("3. Retain backdated memories with tags")
    tags_supported = True
    for ts, text in MEMORIES:
        try:
            client.retain(bank_id=BANK_ID, content=text, context="ap exception resolution",
                          timestamp=ts, tags=[VENDOR_TAG], retain_async=False)
        except TypeError:
            tags_supported = False
            client.retain(bank_id=BANK_ID, content=text, context="ap exception resolution",
                          timestamp=ts, retain_async=False)
        print(f"Retained {ts.date()}")
    print(f"Tags on retain supported by this SDK: {tags_supported}")

    print("Waiting 60s for background consolidation...")
    time.sleep(60)

    step("4. Recall (vendor-filtered)")
    kwargs = {"bank_id": BANK_ID, "query": "Meridian Logistics freight exceptions", "budget": "mid"}
    if tags_supported:
        kwargs.update(tags=[VENDOR_TAG], tags_match="all")
    try:
        results = client.recall(**kwargs)
    except TypeError:
        print("Tag filter not accepted on recall, retrying without it")
        kwargs.pop("tags", None)
        kwargs.pop("tags_match", None)
        results = client.recall(**kwargs)
    for r in results.results:
        print(f"  [{r.type}] {r.text}")

    step("5. Reflect: the pattern-change test")
    answer = client.reflect(
        bank_id=BANK_ID,
        query=(
            f"New exception: {NEW_INVOICE} Based on past resolutions, should this be "
            "auto-approved, recommended for approval, or escalated? Cite the specific "
            "past invoices or events that justify your answer."
        ),
        budget="mid",
    )
    print(answer.text)

    step("VERDICT")
    ok = "contract" in answer.text.lower() or "escalat" in answer.text.lower()
    print("PASS: reflect caught the contract change" if ok
          else "CHECK MANUALLY: reflect may have applied the old freight rule")


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        print(f"\nFAILED: {type(e).__name__}: {e}")
        sys.exit(1)
