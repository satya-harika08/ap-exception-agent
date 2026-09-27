"""
Streamlit UI skeleton for the AP Exception Agent (Person C).

Setup:
  pip install streamlit
  python generate_data.py      (creates the data/ folder, run once)
  streamlit run app.py

This version only DISPLAYS data. The decision panel is a placeholder;
we'll plug in the agent (Hindsight + Groq) once Persons A and B finish.
"""

import json
from pathlib import Path

import pandas as pd
import streamlit as st

DATA = Path("data")

st.set_page_config(page_title="AP Exception Agent", layout="wide")


@st.cache_data
def load():
    queue = json.loads((DATA / "live_queue.json").read_text(encoding="utf-8"))
    pos = {p["po_number"]: p for p in json.loads((DATA / "purchase_orders.json").read_text(encoding="utf-8"))}
    return queue, pos


if not (DATA / "live_queue.json").exists():
    st.error("No data found. Run `python generate_data.py` first.")
    st.stop()

queue, pos = load()

RED = "background-color: rgba(255, 80, 80, 0.35)"


def lines_table(lines, compare_to=None):
    """Show lines with 2 decimals. Rows not on the PO are highlighted red."""
    df = pd.DataFrame([{"Description": l["description"], "Qty": l["qty"],
                        "Unit price": l["unit_price"], "Amount": l["amount"]} for l in lines])
    styler = df.style.format({"Unit price": "{:,.2f}", "Amount": "{:,.2f}"})
    if compare_to is not None:
        po_keys = {(l["sku"], l["qty"], l["unit_price"]) for l in compare_to}
        flags = [(l["sku"], l["qty"], l["unit_price"]) not in po_keys for l in lines]
        styler = styler.apply(lambda row: [RED if flags[row.name] else "" for _ in row], axis=1)
    st.dataframe(styler, hide_index=True, use_container_width=True)


def totals_line(doc, other=None):
    """Escaped dollar signs (Streamlit reads $...$ as math). Tax shown red if it differs."""
    tax = f"\\${doc['tax']:,.2f}"
    if other is not None and abs(doc["tax"] - other["tax"]) > 0.001:
        tax = f":red[{tax}]"
    st.markdown(f"Subtotal \\${doc['subtotal']:,.2f} | Tax {tax} | **Total \\${doc['total']:,.2f}**")

# ---------- header ----------
st.title("AP Exception Agent")
memory_on = st.toggle("Memory ON (Hindsight)", value=True)
st.caption("Memory ON: the agent uses past resolutions. Memory OFF: same model, no history.")

left, right = st.columns([1, 2])

# ---------- left: invoice queue ----------
with left:
    st.subheader("Invoice queue")
    labels = [f"{i['invoice_number']} | {i['vendor_name']} | ${i['total']:,.2f}" for i in queue]
    choice = st.radio("Select an invoice", range(len(queue)), format_func=lambda k: labels[k])
    inv = queue[choice]

# ---------- right: invoice vs PO + decision ----------
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

    st.divider()
    st.subheader("Agent decision")
    st.info("Placeholder: the agent's decision, confidence and cited past invoices will appear here.")

    with st.expander("What memory recalled"):
        st.write("Placeholder: recalled facts and observations from Hindsight.")

    b1, b2, b3 = st.columns(3)
    b1.button("Approve", use_container_width=True)
    b2.button("Reject", use_container_width=True)
    b3.button("Escalate", use_container_width=True)