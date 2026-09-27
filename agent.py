"""
AP Exception Agent core: detect -> remember -> decide -> guardrail -> learn.

Setup:
  pip install -r requirements.txt
  Create a file named .env (copy .env.example) with your keys.

Evaluate on the demo queue (memory OFF vs ON):
  python agent.py              all 8 invoices   (~12 Hindsight calls)
  python agent.py MER-2208     just one invoice (2 Hindsight calls)
Results are also saved to results.json.
"""

import json
import os
import sys
import time
from datetime import datetime
from pathlib import Path

from dotenv import load_dotenv
from hindsight_client import Hindsight
from openai import OpenAI

load_dotenv()

DATA = Path("data")
BANK_ID = os.getenv("HINDSIGHT_BANK_ID", "ap-demo-v1")
MODEL = os.getenv("GROQ_MODEL", "openai/gpt-oss-120b")
ACTIONS = {"auto_approve", "recommend_approve", "reject", "escalate"}
AUTO_APPROVE_MIN_CONFIDENCE = 0.8
AUTO_APPROVE_MIN_EVIDENCE = 2

SYSTEM_PROMPT = """You are an accounts payable exception analyst.
You receive an invoice, the exceptions detected by comparing it to its purchase order,
and possibly evidence from the team's memory of past resolutions.

Respond with ONLY a JSON object with these keys:
  action: one of auto_approve, recommend_approve, reject, escalate
  confidence: number from 0 to 1
  cited_evidence: list of short strings naming the past invoices or events you relied on,
                  e.g. "MER-2205 approved 2026-07-15" or "Meridian contract change 2026-08-01"
  reasoning: at most 2 sentences
  resolution_note: one sentence, written the way an AP reviewer would write it

Rules:
- Only cite evidence that actually appears in the memory evidence. Never invent invoice numbers.
- If there is no memory evidence, you do not know this vendor's patterns: never auto_approve.
- A newer contract change or policy overrides older approval patterns.
- Duplicate invoices should be rejected.
- If memory shows at least 3 consistent past resolutions of the same exception type for this vendor,
  nothing newer overrides them, and the current facts fit that pattern, choose auto_approve.
- Choose escalate, not reject, when the vendor has no relevant history: a human should investigate first.
- Only cite evidence relevant to the detected exception. Do not cite unrelated policies."""


def load_json(name):
    return json.loads((DATA / name).read_text(encoding="utf-8"))


def money(x):
    return f"${x:,.2f}"


class APAgent:
    def __init__(self):
        self.vendors = {v["vendor_id"]: v for v in load_json("vendors.json")}
        self.pos = {p["po_number"]: p for p in load_json("purchase_orders.json")}
        self.history = load_json("invoices_history.json")
        self.memory = Hindsight(base_url=os.environ["HINDSIGHT_API_URL"],
                                api_key=os.environ["HINDSIGHT_API_KEY"], timeout=120.0)
        self.llm = OpenAI(api_key=os.environ["GROQ_API_KEY"], base_url="https://api.groq.com/openai/v1")

    def close(self):
        closer = getattr(self.memory, "close", None)
        if callable(closer):
            closer()

    # ---------- 1. detect (deterministic, no AI) ----------

    def candidate_pos(self, inv):
        return [p["po_number"] for p in self.pos.values()
                if p["vendor_id"] == inv["vendor_id"] and abs(p["total"] - inv["total"]) < 0.01]

    def detect(self, inv):
        found = []
        earlier = [h for h in self.history
                   if h["invoice_number"] == inv["invoice_number"] and h["vendor_id"] == inv["vendor_id"]]
        if earlier:
            found.append({"type": "duplicate_invoice",
                          "detail": f"Invoice number {inv['invoice_number']} was already submitted on "
                                    f"{earlier[0]['invoice_date']}."})

        po = self.pos.get(inv["po_reference"])
        if po is None:
            cands = self.candidate_pos(inv)
            hint = f" Candidate PO by vendor and amount: {', '.join(cands)}." if cands else " No candidate PO found."
            found.append({"type": "po_reference_not_found",
                          "detail": f"PO reference {inv['po_reference']} does not match any purchase order.{hint}",
                          "candidates": cands})
            return found, None

        po_lines = {l["sku"]: l for l in po["lines"]}
        for l in inv["lines"]:
            p = po_lines.get(l["sku"])
            if p is None:
                etype = "freight_not_on_po" if l["sku"].startswith("FRT") else "line_not_on_po"
                found.append({"type": etype, "detail": f"Line '{l['description']}' for {money(l['amount'])} "
                                                       f"is not on {po['po_number']}."})
                continue
            if l["qty"] != p["qty"]:
                found.append({"type": "quantity_variance",
                              "detail": f"'{l['description']}': invoice qty {l['qty']} vs PO qty {p['qty']}."})
            if abs(l["unit_price"] - p["unit_price"]) > 0.001:
                pct = (l["unit_price"] / p["unit_price"] - 1) * 100
                found.append({"type": "price_variance",
                              "detail": f"'{l['description']}': unit price {money(l['unit_price'])} vs PO "
                                        f"{money(p['unit_price'])} ({pct:+.1f}%)."})
        # Only a tax issue if the lines match; otherwise the tax difference just follows the line differences.
        if abs(inv["subtotal"] - po["subtotal"]) < 0.001 and abs(inv["tax"] - po["tax"]) > 0.001:
            found.append({"type": "tax_mismatch",
                          "detail": f"Tax {money(inv['tax'])} vs PO tax {money(po['tax'])} "
                                    f"(difference {money(abs(inv['tax'] - po['tax']))})."})
        if inv["payment_terms"] != po["payment_terms"]:
            found.append({"type": "terms_mismatch",
                          "detail": f"Invoice terms '{inv['payment_terms']}' vs PO terms '{po['payment_terms']}'."})
        return found, po

    # ---------- 2. remember (Hindsight) ----------

    def remember(self, inv, exceptions):
        vendor = self.vendors[inv["vendor_id"]]["name"]
        tags = [f"vendor:{inv['vendor_id']}", "policy"]
        details = " ".join(e["detail"] for e in exceptions)

        kwargs = dict(bank_id=BANK_ID, query=f"{vendor} past invoices, exceptions and resolutions. {details}",
                      budget="mid", max_tokens=2048)
        try:
            recalled = self.memory.recall(**kwargs, tags=tags, tags_match="any")
        except TypeError:
            recalled = self.memory.recall(**kwargs)
        facts = [{"type": r.type, "text": r.text} for r in recalled.results]

        question = (
            f"New invoice {inv['invoice_number']} from {vendor}, dated {inv['invoice_date']}, total "
            f"{money(inv['total'])}. Detected exceptions: {details} "
            f"Based on this vendor's history, AP policies and any contract changes, what does past precedent "
            f"say about resolving these exceptions? Has anything changed recently that overrides older "
            f"patterns? Cite specific past invoices and events by number and date. "
            f"If there is no relevant history for this vendor, say so plainly."
        )
        try:
            answer = self.memory.reflect(bank_id=BANK_ID, query=question, budget="mid", tags=tags, tags_match="any")
        except TypeError:
            answer = self.memory.reflect(bank_id=BANK_ID, query=question, budget="mid")
        return {"recalled": facts, "reflection": answer.text}

    # ---------- 3. decide (Groq) ----------

    def decide(self, inv, exceptions, evidence):
        user = (
            f"Invoice {inv['invoice_number']} from {self.vendors[inv['vendor_id']]['name']}, dated "
            f"{inv['invoice_date']}, total {money(inv['total'])}.\n"
            f"Detected exceptions:\n" + "\n".join(f"- {e['type']}: {e['detail']}" for e in exceptions) +
            f"\n\nMemory evidence:\n{evidence or 'NONE. No memory is available for this decision.'}"
        )
        for attempt in range(2):
            try:
                resp = self.llm.chat.completions.create(
                    model=MODEL, temperature=0, response_format={"type": "json_object"},
                    messages=[{"role": "system", "content": SYSTEM_PROMPT}, {"role": "user", "content": user}])
                d = json.loads(resp.choices[0].message.content)
                if d.get("action") not in ACTIONS:
                    raise ValueError(f"bad action {d.get('action')}")
                d["confidence"] = float(d.get("confidence", 0))
                d.setdefault("cited_evidence", [])
                d.setdefault("reasoning", "")
                d.setdefault("resolution_note", "")
                return d
            except Exception as e:  # malformed JSON, API error, rate limit
                print(f"    LLM attempt {attempt + 1} failed: {e}")
        return {"action": "escalate", "confidence": 0.0, "cited_evidence": [],
                "reasoning": "The decision model failed twice, so this is escalated for safety.",
                "resolution_note": "Escalated: automated decision unavailable."}

    # ---------- 4. guardrail ----------

    @staticmethod
    def guardrail(d, memory_on):
        if d["action"] == "auto_approve":
            reasons = []
            if not memory_on:
                reasons.append("no memory evidence")
            if d["confidence"] < AUTO_APPROVE_MIN_CONFIDENCE:
                reasons.append(f"confidence {d['confidence']:.2f} below {AUTO_APPROVE_MIN_CONFIDENCE}")
            if len(d["cited_evidence"]) < AUTO_APPROVE_MIN_EVIDENCE:
                reasons.append(f"fewer than {AUTO_APPROVE_MIN_EVIDENCE} cited precedents")
            if reasons:
                d["action"] = "recommend_approve"
                d["guardrail"] = "Downgraded from auto_approve: " + ", ".join(reasons) + "."
        return d

    # ---------- full pipeline ----------

    def process(self, inv, memory_on=True):
        start = time.time()
        exceptions, po = self.detect(inv)
        if not exceptions:
            return {"invoice_number": inv["invoice_number"], "memory_on": memory_on, "action": "auto_approve",
                    "confidence": 1.0, "cited_evidence": [], "exceptions": [], "memory": None,
                    "reasoning": "Invoice matches the purchase order exactly.",
                    "resolution_note": "Matched PO exactly, no exceptions.",
                    "seconds": round(time.time() - start, 1)}

        mem = self.remember(inv, exceptions) if memory_on else None
        evidence = None
        if mem:
            evidence = "Synthesized precedent:\n" + mem["reflection"] + "\n\nRecalled facts:\n" + \
                       "\n".join(f"- {f['text']}" for f in mem["recalled"][:15])
        d = self.guardrail(self.decide(inv, exceptions, evidence), memory_on)
        d.update({"invoice_number": inv["invoice_number"], "memory_on": memory_on,
                  "exceptions": exceptions, "memory": mem, "seconds": round(time.time() - start, 1)})
        return d

    # ---------- 5. learn ----------

    def learn(self, inv, decision, human_action, reviewer, note=""):
        """Retain the human's final outcome. human_action: approve, reject or escalate."""
        verb = {"approve": "approved", "reject": "rejected", "escalate": "escalated"}[human_action]
        vendor = self.vendors[inv["vendor_id"]]["name"]
        exc = " ".join(e["detail"] for e in decision["exceptions"]) or "No exceptions."
        agent_word = decision["action"].split("_")[-1]  # approve, reject or escalate
        agreed = "agreed with" if agent_word == human_action else "overrode"
        content = (f"Invoice {inv['invoice_number']} from {vendor}, dated {inv['invoice_date']}, total "
                   f"{money(inv['total'])}, PO reference {inv['po_reference']}. Exceptions: {exc} "
                   f"The agent recommended {decision['action']}; reviewer {reviewer} {agreed} it and {verb} "
                   f"the invoice on {datetime.now().date()}. Note: {note or decision['resolution_note']}")
        first_type = decision["exceptions"][0]["type"] if decision["exceptions"] else "none"
        self.memory.retain(bank_id=BANK_ID, content=content, context="AP exception resolution",
                           timestamp=datetime.now(), retain_async=False,
                           document_id=f"live_{inv['invoice_number']}_{inv['invoice_date']}_{int(time.time())}",
                           tags=[f"vendor:{inv['vendor_id']}", f"exception:{first_type}", f"decision:{verb}"])
        return content


# ---------- evaluation ----------

# Strict scoring: reject (send back to vendor) and escalate (send to a senior reviewer) are different outcomes.
ACCEPTABLE = {"none": {"auto_approve"}, "auto_approve": {"auto_approve"},
              "reject": {"reject"}, "escalate": {"escalate"}}


def main():
    only = sys.argv[1] if len(sys.argv) > 1 else None
    queue = [i for i in load_json("live_queue.json") if not only or i["invoice_number"] == only]
    agent = APAgent()
    score = {False: 0, True: 0}
    results = []
    try:
        run_eval(agent, queue, score, results)
    finally:
        agent.close()

    n = len(queue)
    print(f"\nSCORE  memory OFF: {score[False]}/{n}   memory ON: {score[True]}/{n}")
    auto = {m: sum(1 for r in results if r["memory_on"] == m and r["action"] == "auto_approve") for m in (False, True)}
    print(f"RESOLVED WITHOUT A HUMAN  memory OFF: {auto[False]}/{n}   memory ON: {auto[True]}/{n}")
    Path("results.json").write_text(json.dumps(results, indent=2, default=str), encoding="utf-8")
    print("Full details saved to results.json")


def run_eval(agent, queue, score, results):
    for inv in queue:
        exp = inv["_expected"]
        print(f"\n=== {inv['invoice_number']} {inv['vendor_name']} | expected: {exp['action']} ===")
        for memory_on in (False, True):
            r = agent.process(inv, memory_on=memory_on)
            ok = r["action"] in ACCEPTABLE[exp["action"]]
            score[memory_on] += ok
            label = "ON " if memory_on else "OFF"
            print(f"  Memory {label}: {r['action']:<18} conf {r['confidence']:.2f}  "
                  f"{'PASS' if ok else 'MISS'}  ({r['seconds']}s)")
            print(f"    Reasoning: {r['reasoning']}")
            if r.get("guardrail"):
                print(f"    Guardrail: {r['guardrail']}")
            if r["cited_evidence"]:
                print(f"    Cited: {', '.join(map(str, r['cited_evidence']))}")
            results.append({**r, "expected": exp["action"], "pass": ok})


if __name__ == "__main__":
    main()
