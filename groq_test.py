"""
Groq test for the AP Exception Agent (Person B).

Goal: prove we can get a reliable, structured JSON decision from Groq,
with validation and one retry. No tool calling (see design notes).

Setup:
  pip install openai
  PowerShell:  $env:GROQ_API_KEY="your-groq-key"
  python groq_test.py
"""

import json
import os

from openai import OpenAI

MODEL = "openai/gpt-oss-120b"   # fallback option: "qwen/qwen3-32b"
REQUIRED = {"action", "confidence", "cited_invoices", "reasoning", "resolution_note"}
ACTIONS = {"auto_approve", "recommend_approve", "reject", "escalate"}

client = OpenAI(api_key=os.environ["GROQ_API_KEY"], base_url="https://api.groq.com/openai/v1")

SYSTEM = (
    "You are an accounts payable exception analyst. Respond with ONLY a JSON object with keys: "
    "action (one of auto_approve, recommend_approve, reject, escalate), confidence (0 to 1), "
    "cited_invoices (list of invoice numbers you relied on), reasoning (2 sentences max), "
    "resolution_note (one sentence a reviewer would write). If there is no evidence from past "
    "invoices, you must choose escalate."
)

# Pretend this came from Hindsight reflect (memory ON)
EVIDENCE_WITH_MEMORY = (
    "Past resolutions: MER-2201, MER-2202, MER-2203 had separate freight lines, all approved under the "
    "2025 contract. On 2026-08-01 Meridian signed a new contract: freight is now included in the PO unit "
    "price and separate freight lines are no longer valid."
)
INVOICE = "Invoice MER-2208 from Meridian Logistics dated 2026-09-22. Exception: $450 freight line not on PO-7761."


def decide(invoice, evidence):
    user = f"Invoice: {invoice}\n\nEvidence from memory: {evidence or 'NONE (no history available)'}"
    for attempt in range(2):
        resp = client.chat.completions.create(
            model=MODEL,
            messages=[{"role": "system", "content": SYSTEM}, {"role": "user", "content": user}],
            response_format={"type": "json_object"},
            temperature=0,
        )
        text = resp.choices[0].message.content
        try:
            data = json.loads(text)
            missing = REQUIRED - data.keys()
            if missing:
                raise ValueError(f"missing keys {missing}")
            if data["action"] not in ACTIONS:
                raise ValueError(f"bad action {data['action']}")
            return data
        except (json.JSONDecodeError, ValueError) as e:
            print(f"Attempt {attempt + 1} invalid ({e}), retrying...")
    return {"action": "escalate", "confidence": 0, "cited_invoices": [],
            "reasoning": "Model output invalid twice; escalating for safety.", "resolution_note": ""}


if __name__ == "__main__":
    print("=== MEMORY OFF ===")
    print(json.dumps(decide(INVOICE, None), indent=2))
    print("\n=== MEMORY ON ===")
    print(json.dumps(decide(INVOICE, EVIDENCE_WITH_MEMORY), indent=2))
