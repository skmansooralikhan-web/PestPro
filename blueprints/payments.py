from datetime import date

from flask import (Blueprint, render_template, request, redirect, url_for,
                    flash, session, abort, send_file)

import db as db_module
from auth import login_required, office_required
from blueprints.invoices import _recalc_totals

payments_bp = Blueprint("payments_bp", __name__)


@payments_bp.route("/new/<invoice_id>", methods=["GET", "POST"])
@login_required
@office_required
def new(invoice_id):
    conn = db_module.get_db()
    invoice = conn.execute(
        "SELECT i.*, c.name AS client_name FROM invoices i "
        "JOIN clients c ON c.id = i.client_id WHERE i.id=?", (invoice_id,)
    ).fetchone()
    if not invoice:
        conn.close()
        abort(404)

    if request.method == "POST":
        amount = float(request.form["amount"])
        already_paid = invoice["amount_paid"] or 0
        if already_paid + amount > invoice["total_amount"] + 0.01:
            conn.close()
            flash(f"That payment would exceed the invoice total. Balance due is "
                  f"₹{invoice['balance_due']:.2f}.", "error")
            return redirect(url_for("payments_bp.new", invoice_id=invoice_id))

        pid = db_module.new_id()
        ts = db_module.now_iso()
        conn.execute(
            "INSERT INTO payments (id, invoice_id, client_id, payment_date, amount, "
            "payment_mode, reference_no, notes, received_by, created_at) "
            "VALUES (?,?,?,?,?,?,?,?,?,?)",
            (pid, invoice_id, invoice["client_id"],
             request.form.get("payment_date") or date.today().isoformat(),
             amount, request.form.get("payment_mode", "Cash"),
             request.form.get("reference_no"), request.form.get("notes"),
             session["username"], ts),
        )
        conn.execute(
            "UPDATE invoices SET amount_paid = amount_paid + ? WHERE id=?",
            (amount, invoice_id),
        )
        _recalc_totals(conn, invoice_id)
        conn.commit()
        db_module.log_audit(session["username"], "PAYMENT_RECORDED", "payments", pid,
                             new_value=f"₹{amount:.2f} against {invoice['inv_number']}")
        conn.close()
        flash("Payment recorded.", "success")
        return redirect(url_for("invoices_bp.detail", invoice_id=invoice_id))

    conn.close()
    return render_template("invoices/payment_form.html", invoice=invoice,
                            today=date.today().isoformat())


@payments_bp.route("/<payment_id>/receipt")
@login_required
@office_required
def receipt(payment_id):
    conn = db_module.get_db()
    payment = conn.execute(
        "SELECT p.*, c.name AS client_name, c.address AS client_address, "
        "i.inv_number FROM payments p JOIN clients c ON c.id = p.client_id "
        "JOIN invoices i ON i.id = p.invoice_id WHERE p.id=?", (payment_id,)
    ).fetchone()
    if not payment:
        conn.close()
        abort(404)
    company = db_module.get_all_settings()
    conn.close()

    from pdf_templates.receipt import build_receipt_pdf
    buf = build_receipt_pdf(payment, company)
    filename = f"receipt-{payment_id[:8]}.pdf"
    return send_file(buf, mimetype="application/pdf", download_name=filename)
