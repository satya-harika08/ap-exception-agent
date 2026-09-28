"""
Generate synthetic accounts payable data for the AP Exception Agent.

Run:     python generate_data.py
Output:  data/*.json  (identical on every run and every laptop, thanks to the fixed seed)

Files produced:
  vendors.json            8 vendors with payment terms
  purchase_orders.json    every PO
  invoices_history.json   past invoices (Mar to mid-Sep 2026), all already resolved
  resolutions.json        what a human reviewer decided for each past invoice, and why
  events.json             contract changes and AP policies (things memory must know)
  live_queue.json         unresolved invoices for the demo (late Sep 2026)

NOTE: live_queue items contain an "_expected" field with the correct answer.
It exists only for testing. The agent's detection code must NEVER read it.
"""

import json
import random
from datetime import date, timedelta
from pathlib import Path

random.seed(42)
OUT = Path("data")
TAX_RATE = 0.08
HISTORY_END = date(2026, 9, 15)
REVIEWERS = ["Priya Nair", "Arjun Mehta", "Kavya Reddy"]
CLEAN_NOTE = "Matched PO exactly, no exceptions."

VENDORS = [
    {"vendor_id": "meridian", "name": "Meridian Logistics", "prefix": "MER", "category": "Freight and logistics", "terms": "Net 30"},
    {"vendor_id": "northwind", "name": "Northwind Office Supply", "prefix": "NW", "category": "Office supplies", "terms": "Net 30"},
    {"vendor_id": "kestrel", "name": "Kestrel Industrial Parts", "prefix": "KIP", "category": "Industrial parts", "terms": "Net 45"},
    {"vendor_id": "blueharbor", "name": "Blue Harbor Packaging", "prefix": "BH", "category": "Packaging", "terms": "Net 30"},
    {"vendor_id": "solace", "name": "Solace Facilities Services", "prefix": "SOL", "category": "Facilities", "terms": "Net 30"},
    {"vendor_id": "ardent", "name": "Ardent IT Solutions", "prefix": "ARD", "category": "IT services", "terms": "Net 30"},
    {"vendor_id": "vireo", "name": "Vireo Catering", "prefix": "VIR", "category": "Catering", "terms": "2/10 Net 30"},
    {"vendor_id": "pinecrest", "name": "Pinecrest Staffing", "prefix": "PIN", "category": "Temporary staffing", "terms": "Net 15"},
]
VENDOR = {v["vendor_id"]: v for v in VENDORS}

# sku, description, min price, max price, min qty, max qty
CATALOG = {
    "meridian": [("LTL-REG", "LTL shipment, regional lane", 850, 1400, 1, 4),
                 ("WH-HNDL", "Warehouse handling fee", 120, 200, 1, 3),
                 ("PAL-STR", "Pallet storage, monthly", 60, 90, 4, 12)],
    "northwind": [("PPR-A4", "Copy paper A4, case of 5 reams", 38, 45, 5, 20),
                  ("TNR-BLK", "Toner cartridge, black", 72, 89, 2, 8),
                  ("ORG-DSK", "Desk organizer", 18, 25, 2, 10)],
    "kestrel": [("BRG-6204", "Bearing assembly 6204", 14, 19, 20, 80),
                ("HYD-H10", "Hydraulic hose 1/2 in, 10 ft", 45, 60, 4, 16),
                ("MTR-CPL", "Motor coupling", 88, 110, 2, 6)],
    "blueharbor": [("BOX-181212", "Corrugated box 18x12x12, bundle of 25", 32, 40, 10, 40),
                   ("WRP-STR", "Stretch wrap roll", 22, 28, 6, 24),
                   ("VFL-10", "Void fill, 10 cu ft bag", 30, 36, 4, 12)],
    "vireo": [("LUN-HEAD", "Working lunch, per head", 14, 18, 20, 60),
              ("COF-SVC", "Coffee service", 95, 120, 1, 4)],
}

EVENTS = [
    {"date": "2026-03-01", "vendor_id": None, "type": "policy",
     "text": "AP policy: tax differences up to $0.05 per invoice may be approved as rounding without further review."},
    {"date": "2026-06-01", "vendor_id": "solace", "type": "contract_amendment",
     "text": "Solace Facilities Services contract amendment effective 2026-06-01: 8% annual price escalation on all services."},
    {"date": "2026-08-01", "vendor_id": "meridian", "type": "contract_change",
     "text": "Meridian Logistics signed a new contract effective 2026-08-01. Freight is now included in the PO unit price. Separate freight lines are no longer valid."},
]

pos, history, resolutions = [], [], []
_po_counter = [7700]
_inv_counter = {}


# ---------- helpers ----------

def money(x):
    return round(x + 1e-9, 2)


def line(sku, desc, qty, price):
    p = money(price)
    return {"sku": sku, "description": desc, "qty": qty, "unit_price": p, "amount": money(qty * p)}


def make_lines(vid):
    items = CATALOG[vid]
    chosen = random.sample(items, random.randint(1, len(items)))
    return [line(s, d, random.randint(qa, qb), random.uniform(pa, pb)) for s, d, pa, pb, qa, qb in chosen]


def finalize(doc, tax_adjust=0.0):
    doc["subtotal"] = money(sum(l["amount"] for l in doc["lines"]))
    doc["tax"] = money(doc["subtotal"] * TAX_RATE + tax_adjust)
    doc["total"] = money(doc["subtotal"] + doc["tax"])
    return doc


def next_po():
    _po_counter[0] += 1
    return f"PO-{_po_counter[0]}"


def next_inv(prefix):
    _inv_counter[prefix] = _inv_counter.get(prefix, 2200) + 1
    return f"{prefix}-{_inv_counter[prefix]}"


def make_pair(vid, inv_date, lines):
    v = VENDOR[vid]
    po = finalize({"po_number": next_po(), "vendor_id": vid,
                   "po_date": str(inv_date - timedelta(days=random.randint(5, 12))),
                   "payment_terms": v["terms"], "lines": [dict(l) for l in lines]})
    inv = finalize({"invoice_number": next_inv(v["prefix"]), "vendor_id": vid,
                    "vendor_name": v["name"], "invoice_date": str(inv_date),
                    "po_reference": po["po_number"], "payment_terms": v["terms"],
                    "lines": [dict(l) for l in lines]})
    return po, inv


def resolve(inv, exception, decision, note):
    d = date.fromisoformat(inv["invoice_date"]) + timedelta(days=random.randint(1, 3))
    return {"invoice_number": inv["invoice_number"], "vendor_id": inv["vendor_id"],
            "invoice_date": inv["invoice_date"], "exception_type": exception,
            "decision": decision, "reviewer": random.choice(REVIEWERS),
            "note": note, "resolved_date": str(d)}


def add(po, inv, res):
    if po:
        pos.append(po)
    history.append(inv)
    resolutions.append(res)


def schedule(days, jitter=2):
    out = []
    for m in range(3, 10):
        for d in days:
            dt = date(2026, m, d) + timedelta(days=random.randint(-jitter, jitter))
            if dt <= HISTORY_END:
                out.append(dt)
    return out


# ---------- vendor personalities ----------

def gen_meridian():
    notes = [
        "Approved. Meridian bills freight separately under their 2025 contract, this is expected.",
        "Approved, same separate freight billing pattern as previous Meridian invoices.",
        "Approved. Known Meridian freight line, consistent with prior approvals.",
        "Approved, freight billed separately as usual for Meridian.",
        "Approved. Recurring Meridian freight charge, amount in normal range.",
    ]
    for i, d in enumerate(schedule([14])):
        po, inv = make_pair("meridian", d, make_lines("meridian"))
        if d < date(2026, 8, 1):
            inv["lines"].append(line("FRT-SEP", "Freight charge", 1, random.uniform(380, 520)))
            finalize(inv)
            add(po, inv, resolve(inv, "freight_not_on_po", "approved", notes[min(i, len(notes) - 1)]))
        else:
            add(po, inv, resolve(inv, None, "approved",
                                 "Matched PO exactly. Freight included in unit price per new contract."))


def gen_northwind():
    for d in schedule([5, 20]):
        po, inv = make_pair("northwind", d, make_lines("northwind"))
        if random.random() < 0.6:
            finalize(inv, tax_adjust=random.choice([0.01, 0.02, 0.03, 0.04, -0.01, -0.02, -0.03]))
            diff = abs(money(inv["tax"] - po["tax"]))
            add(po, inv, resolve(inv, "tax_mismatch", "approved",
                                 f"Approved. Tax differs from PO by ${diff:.2f}, within the $0.05 rounding tolerance."))
        else:
            add(po, inv, resolve(inv, None, "approved", CLEAN_NOTE))


def gen_kestrel():
    for d in schedule([9]):
        po, inv = make_pair("kestrel", d, make_lines("kestrel"))
        so = f"KIP-SO-{random.randint(3000, 3999)}"
        inv["po_reference"] = so
        add(po, inv, resolve(inv, "po_reference_not_found", "approved",
                             f"Approved. Kestrel quotes their own sales order number {so}; "
                             f"matched to {po['po_number']} by amount and date."))


def gen_blueharbor():
    dates = schedule([3, 18])
    dup_idx = set(random.sample(range(len(dates) - 3), 3))
    for i, d in enumerate(dates):
        po, inv = make_pair("blueharbor", d, make_lines("blueharbor"))
        res = resolve(inv, None, "approved", CLEAN_NOTE)
        add(po, inv, res)
        dup_date = d + timedelta(days=random.randint(12, 18))
        if i in dup_idx and dup_date <= HISTORY_END:
            dup = json.loads(json.dumps(inv))
            dup["invoice_date"] = str(dup_date)
            add(None, dup, resolve(dup, "duplicate_invoice", "rejected",
                                   f"Rejected. Duplicate of {inv['invoice_number']} dated {inv['invoice_date']}, "
                                   f"already approved on {res['resolved_date']}."))


def gen_solace():
    for d in schedule([2], jitter=0):
        base = [line("JAN-MTH", "Monthly janitorial service", 1, 4200)]
        if d.month in (3, 6, 9):
            base.append(line("WIN-CLN", "Window cleaning, quarterly visit", 1, 650))
        raised = [line(l["sku"], l["description"], l["qty"], l["unit_price"] * 1.08) for l in base]
        if d < date(2026, 6, 1):
            po, inv = make_pair("solace", d, base)
            add(po, inv, resolve(inv, None, "approved", CLEAN_NOTE))
        elif d.month == 6:
            po, inv = make_pair("solace", d, base)
            inv["lines"] = raised
            finalize(inv)
            add(po, inv, resolve(inv, "price_variance", "approved",
                                 "Approved. 8% increase per contract amendment effective 2026-06-01. "
                                 "Future POs will use the new rate."))
        else:
            po, inv = make_pair("solace", d, raised)
            add(po, inv, resolve(inv, None, "approved", CLEAN_NOTE))


def gen_ardent():
    for d in schedule([25]):
        seats = random.randint(40, 44)
        lines = [line("MES-SEAT", "Managed endpoint support, per seat", seats, 45),
                 line("OFS-SEAT", "Office suite license, per seat", seats, 22)]
        po, inv = make_pair("ardent", d, lines)
        add(po, inv, resolve(inv, None, "approved", CLEAN_NOTE))


def gen_vireo():
    for d in schedule([12]):
        po, inv = make_pair("vireo", d, make_lines("vireo"))
        if random.random() < 0.7:
            inv["payment_terms"] = "Net 30"
            add(po, inv, resolve(inv, "terms_mismatch", "approved",
                                 "Approved. Vireo's billing system prints default Net 30; "
                                 "pay on 2/10 Net 30 per PO and take the discount."))
        else:
            add(po, inv, resolve(inv, None, "approved", CLEAN_NOTE))


# ---------- demo queue ----------

def gen_live_queue():
    queue = []

    def expect(inv, exc, action, why, **extra):
        inv["_expected"] = {"exception_type": exc, "action": action, "why": why, **extra}
        queue.append(inv)

    po, inv = make_pair("meridian", date(2026, 9, 22), make_lines("meridian"))
    inv["lines"].append(line("FRT-SEP", "Freight charge", 1, 450))
    finalize(inv)
    pos.append(po)
    expect(inv, "freight_not_on_po", "escalate",
           "Old pattern was approve, but the 2026-08-01 contract makes separate freight invalid. "
           "Rejecting for a corrected invoice is equally valid once the contract is known.",
           also_ok=["reject"])

    po, inv = make_pair("northwind", date(2026, 9, 22), make_lines("northwind"))
    finalize(inv, tax_adjust=0.03)
    pos.append(po)
    expect(inv, "tax_mismatch", "auto_approve", "Within $0.05 rounding policy, many prior approvals.")

    po, inv = make_pair("kestrel", date(2026, 9, 23), make_lines("kestrel"))
    inv["po_reference"] = "KIP-SO-3871"
    pos.append(po)
    expect(inv, "po_reference_not_found", "auto_approve",
           "Kestrel always quotes its own sales order number.", matched_po=po["po_number"])

    last_bh = [h for h in history if h["vendor_id"] == "blueharbor"][-1]
    dup = json.loads(json.dumps(last_bh))
    dup["invoice_date"] = "2026-09-24"
    expect(dup, "duplicate_invoice", "reject", f"Duplicate of {last_bh['invoice_number']} already processed.")

    raised = [line("JAN-MTH", "Monthly janitorial service", 1, 4200 * 1.08)]
    po, inv = make_pair("solace", date(2026, 9, 24), raised)
    pos.append(po)
    expect(inv, None, "none", "Clean invoice at the amended rate.")

    lines = [line("MES-SEAT", "Managed endpoint support, per seat", 43, 45),
             line("OFS-SEAT", "Office suite license, per seat", 43, 22)]
    po, inv = make_pair("ardent", date(2026, 9, 25), lines)
    pos.append(po)
    expect(inv, None, "none", "Clean invoice.")

    po, inv = make_pair("vireo", date(2026, 9, 25), make_lines("vireo"))
    inv["payment_terms"] = "Net 30"
    pos.append(po)
    expect(inv, "terms_mismatch", "auto_approve", "Vireo always prints default terms; PO terms apply.")

    po, inv = make_pair("pinecrest", date(2026, 9, 26), [line("TMP-APC", "Temporary AP clerk, hourly", 72, 30)])
    inv["lines"] = [line("TMP-APC", "Temporary AP clerk, hourly", 80, 30)]
    finalize(inv)
    pos.append(po)
    expect(inv, "quantity_variance", "escalate", "New vendor with no history; 80 hours billed vs 72 on PO.")

    return queue


def main():
    OUT.mkdir(exist_ok=True)
    for gen in (gen_meridian, gen_northwind, gen_kestrel, gen_blueharbor, gen_solace, gen_ardent, gen_vireo):
        gen()
    queue = gen_live_queue()

    history.sort(key=lambda x: x["invoice_date"])
    resolutions.sort(key=lambda x: x["invoice_date"])
    pos.sort(key=lambda x: x["po_date"])

    files = {"vendors.json": VENDORS, "purchase_orders.json": pos, "invoices_history.json": history,
             "resolutions.json": resolutions, "events.json": EVENTS, "live_queue.json": queue}
    for name, obj in files.items():
        (OUT / name).write_text(json.dumps(obj, indent=2), encoding="utf-8")

    print(f"POs: {len(pos)}  history invoices: {len(history)}  live queue: {len(queue)}")
    counts = {}
    for r in resolutions:
        key = r["exception_type"] or "clean"
        counts[key] = counts.get(key, 0) + 1
    for k, v in sorted(counts.items()):
        print(f"  {k:<24}{v}")
    print(f"Files written to {OUT.resolve()}")


if __name__ == "__main__":
    main()
