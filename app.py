"""
Streamlit UI for the AP Exception Agent.

Setup:
  pip install -r requirements.txt
  .env file with your keys (see .env.example)
  python generate_data.py       (only if the data/ folder is missing)
  streamlit run app.py
"""

import json
from pathlib import Path

import pandas as pd
import streamlit as st

from agent import APAgent

DATA = Path("data")
RED = "background-color: rgba(255, 80, 80, 0.35)"
ACTION_STYLE = {
    "auto_approve": ("green", "AUTO-APPROVED"),
    "recommend_approve": ("orange", "RECOMMEND APPROVE"),
    "reject": ("red", "RECOMMEND REJECT"),
    "escalate": ("violet", "ESCALATE TO SENIOR REVIEWER"),
}

st.set_page_config(page_title="AP Exception Agent", layout="wide")


def esc(text):
    """Streamlit reads $...$ as math, so escape every dollar sign in displayed text."""
    return str(text).replace("$", "\\$")


@st.cache_data
def load_data():
    queue = json.loads((DATA / "live_queue.json").read_text(encoding="utf-8"))
    pos = {p["po_number"]: p for p in json.loads((DATA / "purchase_orders.json").read_text(encoding="utf-8"))}
    return queue, pos


@st.cache_resource
def get_agent():
    return APAgent()


if not (DATA / "live_queue.json").exists():
    st.error("No data found. Run `python generate_data.py` first.")
    st.stop()

try:
    agent = get_agent()
except KeyError as e:
    st.error(f"Missing setting {e}. Create a .env file from .env.example with your API keys.")
    st.stop()

queue, pos = load_data()
state = st.session_state
state.setdefault("results", {})     # (invoice key, memory_on) -> decision
state.setdefault("view", {})        # invoice key -> "single" or "compare"
state.setdefault("processed", {})   # invoice key -> text retained into memory


def inv_key(inv):
    return f"{inv['invoice_number']}_{inv['invoice_date']}"


def run(inv, memory_on):
    key = (inv_key(inv), memory_on)
    if key not in state.results:
        msg = "Consulting Hindsight memory..." if memory_on else "Deciding without memory..."
        with st.spinner(msg):
            state.results[key] = agent.process(inv, memory_on=memory_on)
    return state.results[key]


# ---------- display helpers ----------

def lines_table(lines, compare_to=None):
    df = pd.DataFrame([{"Description": l["description"], "Qty": l["qty"],
                        "Unit price": l["unit_price"], "Amount": l["amount"]} for l in lines])
    styler = df.style.format({"Unit price": "{:,.2f}", "Amount": "{:,.2f}"})
    if compare_to is not None:
        po_keys = {(l["sku"], l["qty"], l["unit_price"]) for l in compare_to}
        flags = [(l["sku"], l["qty"], l["unit_price"]) not in po_keys for l in lines]
        styler = styler.apply(lambda row: [RED if flags[row.name] else "" for _ in row], axis=1)
    st.dataframe(
        styler, hide_index=True, use_container_width=True,
        column_config={
            "Description": st.column_config.TextColumn("Description", width="medium"),
            "Qty": st.column_config.NumberColumn("Qty", width="small"),
            "Unit price": st.column_config.NumberColumn("Unit price", width="small", format="%.2f"),
            "Amount": st.column_config.NumberColumn("Amount", width="small", format="%.2f"),
        },
    )


def totals_line(doc, other=None):
    tax = f"\\${doc['tax']:,.2f}"
    # Only flag tax when lines match; otherwise the tax difference just follows the extra lines.
    if (other is not None and abs(doc["subtotal"] - other["subtotal"]) < 0.001
            and abs(doc["tax"] - other["tax"]) > 0.001):
        tax = f":red[{tax}]"
    st.markdown(f"Subtotal \\${doc['subtotal']:,.2f} | Tax {tax} | **Total \\${doc['total']:,.2f}**")


def render_decision(d, title=None):
    color, label = ACTION_STYLE[d["action"]]
    if title:
        st.markdown(f"**{title}**")
    st.markdown(f"#### :{color}[{label}]")
    st.caption(f"Confidence {d['confidence']:.0%}  |  {d['seconds']}s")
    st.markdown(esc(d["reasoning"]))
    if d.get("guardrail"):
        st.warning(esc(d["guardrail"]))
    if d["cited_evidence"]:
        st.markdown("**Evidence cited**")
        for c in d["cited_evidence"]:
            st.markdown(f"- {esc(c)}")
    elif d["exceptions"]:
        st.caption("No evidence cited: the agent had no history to draw on.")
    if d["resolution_note"]:
        st.caption(f"Draft note: {esc(d['resolution_note'])}")
    mem = d.get("memory")
    if mem:
        with st.expander("What Hindsight recalled"):
            st.markdown("**Synthesized precedent (reflect)**")
            st.markdown(esc(mem["reflection"]))
            obs = [f for f in mem["recalled"] if f["type"] == "observation"]
            facts = [f for f in mem["recalled"] if f["type"] != "observation"]
            if obs:
                st.markdown("**Consolidated observations**")
                for f in obs[:8]:
                    st.markdown(f"- {esc(f['text'])}")
            if facts:
                st.markdown("**Raw facts**")
                for f in facts[:8]:
                    st.markdown(f"- {esc(f['text'])}")


# ---------- sidebar ----------

with st.sidebar:
    st.header("Settings")
    reviewer = st.text_input("Reviewer name", "Priya Nair")
    st.caption("Clicking Approve, Reject or Escalate saves the outcome to Hindsight, "
               "so the agent learns from it. Avoid clicking during rehearsals.")

# ---------- header ----------

st.title("AP Exception Agent")
memory_on = st.toggle("Memory ON (Hindsight)", value=True)
st.caption("Memory ON: the agent uses past resolutions. Memory OFF: same model and prompt, no history.")

left, right = st.columns([1, 2])

with left:
    st.subheader("Invoice queue")
    labels = [f"{'(done) ' if inv_key(i) in state.processed else ''}{i['invoice_number']} | "
              f"{i['vendor_name']} | \\${i['total']:,.2f}" for i in queue]
    choice = st.radio("Select an invoice", range(len(queue)), format_func=lambda k: labels[k])
    inv = queue[choice]
    key = inv_key(inv)

with right:
    st.subheader(f"{inv['invoice_number']} from {inv['vendor_name']}")
    st.write(f"Date: {inv['invoice_date']}  |  PO reference: {inv['po_reference']}  |  Terms: {inv['payment_terms']}")

    po = pos.get(inv["po_reference"])
    if po and po["payment_terms"] != inv["payment_terms"]:
        st.markdown(f":red[Payment terms differ: invoice says {inv['payment_terms']}, PO says {po['payment_terms']}]")

    c1, c2 = st.columns(2)
    with c1:
        st.markdown("**Invoice lines**")
        lines_table(inv["lines"], compare_to=po["lines"] if po else None)
        totals_line(inv, po)
    with c2:
        st.markdown("**Purchase order**")
        if po:
            lines_table(po["lines"])
            totals_line(po)
        else:
            st.warning(f"No PO found for reference {inv['po_reference']}")

    exceptions, _ = agent.detect(inv)
    st.markdown("**Detected exceptions**")
    if exceptions:
        for e in exceptions:
            st.markdown(f"- `{e['type']}`: {esc(e['detail'])}")
    else:
        st.markdown("- None: invoice matches the PO.")

    st.divider()
    b1, b2 = st.columns(2)
    if b1.button("Run agent", type="primary", use_container_width=True):
        state.view[key] = "single"
    if b2.button("Compare memory OFF vs ON", use_container_width=True):
        state.view[key] = "compare"

    view = state.view.get(key)
    decision = None
    if view == "single":
        decision = run(inv, memory_on)
        render_decision(decision, title="Agent decision" + (" (memory ON)" if memory_on else " (memory OFF)"))
    elif view == "compare":
        off = run(inv, False)
        on = run(inv, True)
        colA, colB = st.columns(2)
        with colA:
            render_decision(off, title="Memory OFF")
        with colB:
            render_decision(on, title="Memory ON")
        decision = on

    if decision:
        st.divider()
        st.markdown("**Your decision** (saved to memory)")
        if key in state.processed:
            st.success("Outcome saved to Hindsight:")
            st.caption(esc(state.processed[key]))
        else:
            h1, h2, h3 = st.columns(3)
            for col, action, label in ((h1, "approve", "Approve"), (h2, "reject", "Reject"), (h3, "escalate", "Escalate")):
                if col.button(label, use_container_width=True, key=f"{key}_{action}"):
                    with st.spinner("Saving outcome to memory..."):
                        state.processed[key] = agent.learn(inv, decision, action, reviewer)
                    st.rerun()