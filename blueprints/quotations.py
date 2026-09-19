from datetime import date, timedelta

from flask import (Blueprint, render_template, request, redirect, url_for,
                    flash, session, abort, send_file)

import db as db_module
from auth import login_required, office_required
from utils import next_quote_number, parse_json, amount_in_words, next_invoice_number

quotations_bp = Blueprint("quotations_bp", __name__)

STATUSES = ["Draft", "Sent", "Accepted", "Rejected", "Expired"]


def _recalc_totals(conn, quote_id):
    items = conn.execute(
        "SELECT * FROM quotation_line_items WHERE quotation_id=?", (quote_id,)
    ).fetchall()
    subtotal = sum(i["amount"] for i in items)
    q = conn.execute("SELECT * FROM quotations WHERE id=?", (quote_id,)).fetchone()
    cgst_amount = round(subtotal * q["cgst_rate"] / 100, 2)
    sgst_amount = round(subtotal * q["sgst_rate"] / 100, 2)
    total = round(subtotal + cgst_amount + sgst_amount, 2)
    conn.execute(
        "UPDATE quotations SET subtotal=?, cgst_amount=?, sgst_amount=?, "
        "total_amount=?, updated_at=? WHERE id=?",
        (subtotal, cgst_amount, sgst_amount, total, db_module.now_iso(), quote_id),
    )


@quotations_bp.route("/new", methods=["GET", "POST"])
@login_required
@office_required
def new():
    lead_id = request.args.get("lead", "") or request.form.get("lead_id", "")
    client_id = request.args.get("client", "") or request.form.get("client_id", "")
    conn = db_module.get_db()

    lead = conn.execute("SELECT * FROM leads WHERE id=?", (lead_id,)).fetchone() if lead_id else None
    client = conn.execute("SELECT * FROM clients WHERE id=?", (client_id,)).fetchone() if client_id else None

    if request.method == "POST":
        if not lead and not client:
            conn.close()
            flash("A quotation needs either a lead or an existing client.", "error")
            return redirect(url_for("leads_bp.index"))

        if not any(s.strip() for s in request.form.getlist("item_service")):
            conn.close()
            flash("Add at least one line item with a service before creating "
                  "the quotation — remove any empty rows first.", "error")
            return redirect(url_for("quotations_bp.new", lead=lead_id, client=client_id))

        qid = db_module.new_id()
        quote_number = next_quote_number(conn)
        ts = db_module.now_iso()
        quote_date = request.form.get("quote_date") or date.today().isoformat()
        valid_until = request.form.get("valid_until") or (
            date.today() + timedelta(days=15)
        ).isoformat()
        cgst_rate = float(request.form.get("cgst_rate") or db_module.get_setting("cgstRate", "9"))
        sgst_rate = float(request.form.get("sgst_rate") or db_module.get_setting("sgstRate", "9"))

        conn.execute(
            "INSERT INTO quotations (id, quote_number, lead_id, client_id, quote_date, "
            "valid_until, cgst_rate, sgst_rate, status, notes, created_by, created_at, "
            "updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (qid, quote_number, lead["id"] if lead else None,
             client["id"] if client else None, quote_date, valid_until,
             cgst_rate, sgst_rate, "Draft", request.form.get("notes"),
             session["username"], ts, ts),
        )

        services = request.form.getlist("item_service")
        descriptions = request.form.getlist("item_description")
        quantities = request.form.getlist("item_qty")
        rates = request.form.getlist("item_rate")
        # See invoices_bp.new() for why this indexes off `services` rather
        # than zip()-ing the four lists — avoids silently dropping rows if
        # one field's list ends up a different length than the others.
        for i, svc in enumerate(services):
            if not svc:
                continue
            desc = descriptions[i] if i < len(descriptions) else ""
            qty_f = float(quantities[i]) if i < len(quantities) and quantities[i] else 1
            rate_f = float(rates[i]) if i < len(rates) and rates[i] else 0
            conn.execute(
                "INSERT INTO quotation_line_items (id, quotation_id, service, "
                "description, quantity, unit_price, amount) VALUES (?,?,?,?,?,?,?)",
                (db_module.new_id(), qid, svc, desc, qty_f, rate_f,
                 round(qty_f * rate_f, 2)),
            )

        _recalc_totals(conn, qid)
        if lead:
            conn.execute("UPDATE leads SET stage='Quoted', updated_at=? WHERE id=?",
                         (ts, lead["id"]))
        conn.commit()
        db_module.log_audit(session["username"], "QUOTATION_CREATED", "quotations",
                             qid, new_value=quote_number)
        conn.close()
        flash(f"Quotation {quote_number} created.", "success")
        return redirect(url_for("quotations_bp.detail", quote_id=qid))

    services_catalog = parse_json(db_module.get_setting("serviceCatalog"), [])
    default_cgst = "0" if (client and client["gst_exempt"]) else db_module.get_setting("cgstRate", "9")
    default_sgst = "0" if (client and client["gst_exempt"]) else db_module.get_setting("sgstRate", "9")
    conn.close()
    return render_template(
        "quotations/form.html", lead=lead, client=client,
        services_catalog=services_catalog, today=date.today().isoformat(),
        default_valid=(date.today() + timedelta(days=15)).isoformat(),
        default_cgst=default_cgst, default_sgst=default_sgst,
    )


@quotations_bp.route("/<quote_id>")
@login_required
@office_required
def detail(quote_id):
    conn = db_module.get_db()
    q = conn.execute("SELECT * FROM quotations WHERE id=?", (quote_id,)).fetchone()
    if not q:
        conn.close()
        abort(404)
    items = conn.execute(
        "SELECT * FROM quotation_line_items WHERE quotation_id=?", (quote_id,)
    ).fetchall()
    lead = conn.execute("SELECT * FROM leads WHERE id=?", (q["lead_id"],)).fetchone() \
        if q["lead_id"] else None
    client = conn.execute("SELECT * FROM clients WHERE id=?", (q["client_id"],)).fetchone() \
        if q["client_id"] else None
    conn.close()
    recipient_name = client["name"] if client else (lead["name"] if lead else "—")
    return render_template("quotations/detail.html", q=q, items=items, lead=lead,
                            client=client, recipient_name=recipient_name,
                            amount_words=amount_in_words(q["total_amount"]),
                            statuses=STATUSES)


@quotations_bp.route("/<quote_id>/pdf")
@login_required
@office_required
def pdf(quote_id):
    conn = db_module.get_db()
    q = conn.execute("SELECT * FROM quotations WHERE id=?", (quote_id,)).fetchone()
    if not q:
        conn.close()
        abort(404)
    items = conn.execute(
        "SELECT * FROM quotation_line_items WHERE quotation_id=?", (quote_id,)
    ).fetchall()
    lead = conn.execute("SELECT * FROM leads WHERE id=?", (q["lead_id"],)).fetchone() \
        if q["lead_id"] else None
    client = conn.execute("SELECT * FROM clients WHERE id=?", (q["client_id"],)).fetchone() \
        if q["client_id"] else None
    company = db_module.get_all_settings()
    conn.close()

    recipient = dict(client) if client else (dict(lead) if lead else {})
    from pdf_templates.quotation import build_quotation_pdf
    buf = build_quotation_pdf(q, items, recipient, company)
    filename = f"{q['quote_number'].replace('/', '-')}.pdf"
    return send_file(buf, mimetype="application/pdf", download_name=filename)


@quotations_bp.route("/<quote_id>/status", methods=["POST"])
@login_required
@office_required
def update_status(quote_id):
    conn = db_module.get_db()
    q = conn.execute("SELECT * FROM quotations WHERE id=?", (quote_id,)).fetchone()
    if not q:
        conn.close()
        abort(404)
    new_status = request.form["status"]
    if new_status not in STATUSES:
        conn.close()
        abort(400)
    conn.execute("UPDATE quotations SET status=?, updated_at=? WHERE id=?",
                 (new_status, db_module.now_iso(), quote_id))
    conn.commit()
    db_module.log_audit(session["username"], "QUOTATION_STATUS_CHANGED", "quotations",
                         quote_id, old_value=q["status"], new_value=new_status)
    conn.close()
    flash(f"Quotation marked {new_status}.", "success")
    return redirect(url_for("quotations_bp.detail", quote_id=quote_id))


@quotations_bp.route("/<quote_id>/convert-to-invoice", methods=["POST"])
@login_required
@office_required
def convert_to_invoice(quote_id):
    conn = db_module.get_db()
    q = conn.execute("SELECT * FROM quotations WHERE id=?", (quote_id,)).fetchone()
    if not q:
        conn.close()
        abort(404)
    if not q["client_id"]:
        conn.close()
        flash("This quotation isn't linked to a client yet — convert the lead "
              "to a client first.", "error")
        return redirect(url_for("quotations_bp.detail", quote_id=quote_id))
    if q["converted_invoice_id"]:
        conn.close()
        flash("This quotation has already been converted to an invoice.", "error")
        return redirect(url_for("quotations_bp.detail", quote_id=quote_id))

    items = conn.execute(
        "SELECT * FROM quotation_line_items WHERE quotation_id=?", (quote_id,)
    ).fetchall()
    client = conn.execute("SELECT * FROM clients WHERE id=?", (q["client_id"],)).fetchone()

    inv_id = db_module.new_id()
    inv_number = next_invoice_number(conn)
    ts = db_module.now_iso()
    due_days = int(db_module.get_setting("invoiceDueDays", "30"))
    cgst_rate, sgst_rate = q["cgst_rate"], q["sgst_rate"]
    if client and client["gst_exempt"]:
        cgst_rate = sgst_rate = 0.0

    conn.execute(
        "INSERT INTO invoices (id, inv_number, invoice_date, due_date, client_id, "
        "cgst_rate, sgst_rate, notes, created_by, created_at, updated_at) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
        (inv_id, inv_number, date.today().isoformat(),
         (date.today() + timedelta(days=due_days)).isoformat(), q["client_id"],
         cgst_rate, sgst_rate, f"Converted from quotation {q['quote_number']}",
         session["username"], ts, ts),
    )
    hsn = db_module.get_setting("hsnCode", "998531")
    for it in items:
        conn.execute(
            "INSERT INTO invoice_line_items (id, invoice_id, service, description, "
            "quantity, unit_price, amount, hsn_sac) VALUES (?,?,?,?,?,?,?,?)",
            (db_module.new_id(), inv_id, it["service"], it["description"],
             it["quantity"], it["unit_price"], it["amount"], hsn),
        )

    from blueprints.invoices import _recalc_totals as recalc_invoice_totals
    recalc_invoice_totals(conn, inv_id)
    conn.execute("UPDATE quotations SET converted_invoice_id=?, updated_at=? WHERE id=?",
                 (inv_id, ts, quote_id))
    conn.commit()
    db_module.log_audit(session["username"], "QUOTATION_CONVERTED", "quotations",
                         quote_id, new_value=inv_number)
    conn.close()
    flash(f"Converted to invoice {inv_number}.", "success")
    return redirect(url_for("invoices_bp.detail", invoice_id=inv_id))
