"""
Seed Hindsight with the AP history (Person A). Run ONCE per bank.

Usage:
  python seed_memory.py --dry-run    Print every memory in plain English. No API calls, no credits.
  python seed_memory.py              Actually retain into Hindsight (asks for confirmation first).

Needs (only for the real run):
  $env:HINDSIGHT_API_URL="https://api.hindsight.vectorize.io"
  $env:HINDSIGHT_API_KEY="your-key"

Safety features:
  - Asks before spending credits.
  - Remembers progress in seed_progress.json, so if it crashes halfway,
    rerunning skips what was already stored instead of paying twice.
"""

import json
import os
import sys
from datetime import datetime
from pathlib import Path

DATA = Path("data")
BANK_ID = "ap-demo-v1"
PROGRESS = Path("seed_progress.json")

MISSION = (
    "You are the institutional memory of an accounts payable team. Prioritize past exception "
    "resolutions, reviewer rationale, recurring vendor patterns, AP policies, and contract changes. "
    "A newer contract change or policy always overrides older approval patterns. Always cite the "
    "specific past invoices or events behind a recommendation. With no relevant history, recommend escalation."
)


def load(name):
    return json.loads((DATA / name).read_text(encoding="utf-8"))


def describe_exception(res, inv, pos):
    etype = res["exception_type"]
    po = pos.get(inv["po_reference"])
    if etype == "freight_not_on_po":
        amt = sum(l["amount"] for l in inv["lines"] if l["sku"] == "FRT-SEP")
        return f"freight line of ${amt:,.2f} not on {inv['po_reference']}"
    if etype == "tax_mismatch":
        return f"invoice tax ${inv['tax']:,.2f} differs from PO tax ${po['tax']:,.2f}"
    if etype == "po_reference_not_found":
        return f"PO reference {inv['po_reference']} does not match any purchase order"
    if etype == "duplicate_invoice":
        return f"invoice number {inv['invoice_number']} had already been submitted earlier"
    if etype == "price_variance":
        return f"unit prices higher than on {inv['po_reference']} (PO total ${po['total']:,.2f})"
    if etype == "terms_mismatch":
        return f"invoice payment terms '{inv['payment_terms']}' differ from PO terms '{po['payment_terms']}'"
    return etype


def build_memories():
    vendors = {v["vendor_id"]: v["name"] for v in load("vendors.json")}
    pos = {p["po_number"]: p for p in load("purchase_orders.json")}
    invoices = {(i["invoice_number"], i["invoice_date"]): i for i in load("invoices_history.json")}
    memories = []

    for ev in load("events.json"):
        tag = f"vendor:{ev['vendor_id']}" if ev["vendor_id"] else "policy"
        memories.append({
            "doc_id": f"event_{ev['type']}_{ev['date']}",
            "content": ev["text"],
            "context": "AP policy or vendor contract change",
            "timestamp": datetime.fromisoformat(ev["date"]),
            "tags": [tag, f"event:{ev['type']}"],
        })

    for res in load("resolutions.json"):
        inv = invoices[(res["invoice_number"], res["invoice_date"])]
        header = (f"Invoice {inv['invoice_number']} from {vendors[inv['vendor_id']]}, dated "
                  f"{inv['invoice_date']}, total ${inv['total']:,.2f}, PO reference {inv['po_reference']}.")
        if res["exception_type"]:
            body = f" Exception: {describe_exception(res, inv, pos)}."
        else:
            body = " No exceptions."
        verdict = f" Reviewer {res['reviewer']} {res['decision']} it on {res['resolved_date']}. Note: {res['note']}"
        memories.append({
            "doc_id": f"res_{inv['invoice_number']}_{inv['invoice_date']}",
            "content": header + body + verdict,
            "context": "AP exception resolution",
            "timestamp": datetime.fromisoformat(res["resolved_date"]),
            "tags": [f"vendor:{inv['vendor_id']}",
                     f"exception:{res['exception_type'] or 'none'}",
                     f"decision:{res['decision']}"],
        })

    memories.sort(key=lambda m: m["timestamp"])
    return memories


def dry_run(memories):
    for m in sorted(memories, key=lambda m: (m["tags"][0], m["timestamp"])):
        print(f"[{m['timestamp'].date()}] {m['tags'][0]:<18} {m['content']}\n")
    print(f"Total memories: {len(memories)}  (dry run, nothing was sent)")


def real_run(memories):
    from hindsight_client import Hindsight

    done = set(json.loads(PROGRESS.read_text())) if PROGRESS.exists() else set()
    todo = [m for m in memories if m["doc_id"] not in done]
    print(f"Bank: {BANK_ID}. Already stored: {len(done)}. To store now: {len(todo)}.")
    if not todo:
        print("Nothing to do.")
        return
    if input("This spends Hindsight credits. Continue? (y/n) ").strip().lower() != "y":
        print("Cancelled.")
        return

    with Hindsight(base_url=os.environ["HINDSIGHT_API_URL"],
                   api_key=os.environ["HINDSIGHT_API_KEY"], timeout=120.0) as client:
        if not done:
            try:
                client.create_bank(bank_id=BANK_ID, name="AP Exception Agent Demo", mission=MISSION,
                                   disposition={"skepticism": 4, "literalism": 4, "empathy": 2})
                print("Bank created.")
            except Exception as e:
                print(f"Bank create skipped (may already exist): {e}")

        for n, m in enumerate(todo, 1):
            client.retain(bank_id=BANK_ID, content=m["content"], context=m["context"],
                          timestamp=m["timestamp"], tags=m["tags"], document_id=m["doc_id"],
                          retain_async=False)
            done.add(m["doc_id"])
            PROGRESS.write_text(json.dumps(sorted(done)))
            print(f"  [{n}/{len(todo)}] {m['doc_id']}")

    print("Seeding complete. Wait a few minutes for Hindsight to consolidate observations before testing.")


if __name__ == "__main__":
    mems = build_memories()
    if "--dry-run" in sys.argv:
        dry_run(mems)
    else:
        real_run(mems)
