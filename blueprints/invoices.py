from datetime import date, timedelta

from flask import (Blueprint, render_template, request, redirect, url_for,
                    flash, session, abort, send_file)

import db as db_module
from auth import login_required, office_required, admin_required
from utils import next_invoice_number, parse_date, render_confirm_page

invoices_bp = Blueprint("invoices_bp", __name__)


def _recalc_totals(conn, invoice_id):
    items = conn.execute(
        "SELECT * FROM invoice_line_items WHERE invoice_id=?", (invoice_id,)
    ).fetchall()
    subtotal = sum(i["amount"] for i in items)
    inv = conn.execute("SELECT * FROM invoices WHERE id=?", (invoice_id,)).fetchone()
    cgst_rate = inv["cgst_rate"]
    sgst_rate = inv["sgst_rate"]
    cgst_amount = round(subtotal * cgst_rate / 100, 2)
    sgst_amount = round(subtotal * sgst_rate / 100, 2)
    total = round(subtotal + cgst_amount + sgst_amount, 2)
    paid = inv["amount_paid"] or 0
    balance = round(total - paid, 2)
    status = inv["payment_status"]
    if status != "Overdue" or balance <= 0:
        if paid <= 0:
            status = "Unpaid"
        elif balance <= 0:
            status = "Paid"
        else:
            status = "Partial"
    conn.execute(
        "UPDATE invoices SET subtotal=?, cgst_amount=?, sgst_amount=?, "
        "total_amount=?, balance_due=?, payment_status=?, updated_at=? WHERE id=?",
        (subtotal, cgst_amount, sgst_amount, total, balance, status,
         db_module.now_iso(), invoice_id),
    )


@invoices_bp.route("/")
@login_required
@office_required
def index():
    conn = db_module.get_db()
    status = request.args.get("status", "")
    q = request.args.get("q", "").strip()
    sql = ("SELECT i.*, c.name AS client_name FROM invoices i "
           "JOIN clients c ON c.id = i.client_id WHERE 1=1")
    params = []
    if status:
        sql += " AND i.payment_status = ?"
        params.append(status)
    if q:
        sql += " AND (i.inv_number LIKE ? OR c.name LIKE ?)"
        params += [f"%{q}%", f"%{q}%"]
    sql += " ORDER BY i.invoice_date DESC LIMIT 300"
    invoices = conn.execute(sql, params).fetchall()

    summary = conn.execute(
        "SELECT COALESCE(SUM(total_amount),0) AS billed, "
        "COALESCE(SUM(amount_paid),0) AS collected, "
        "COALESCE(SUM(balance_due),0) AS outstanding "
        "FROM invoices WHERE is_void=0"
    ).fetchone()
    status_counts = {row["payment_status"]: row["c"] for row in conn.execute(
        "SELECT payment_status, COUNT(*) AS c FROM invoices WHERE is_void=0 "
        "GROUP BY payment_status"
    ).fetchall()}
    conn.close()
    return render_template("invoices/index.html", invoices=invoices, status=status,
                            q=q, summary=summary, status_counts=status_counts)


@invoices_bp.route("/new", methods=["GET", "POST"])
@login_required
@office_required
def new():
    conn = db_module.get_db()
    if request.method == "POST":
        client_id = request.form["client_id"]
        client = conn.execute("SELECT * FROM clients WHERE id=?", (client_id,)).fetchone()
        if not client:
            conn.close()
            flash("Client not found.", "error")
            return redirect(url_for("invoices_bp.new"))

        # At least one line item needs a service filled in — a blank row
        # left over from clicking "Add line item" without filling it in
        # (or removing it) shouldn't silently create a ₹0 invoice.
        item_services_raw = request.form.getlist("item_service")
        if not any(s.strip() for s in item_services_raw):
            conn.close()
            flash("Add at least one line item with a service before creating "
                  "the invoice — remove any empty rows first.", "error")
            return redirect(url_for("invoices_bp.new", client=client_id))

        # A whole invoice totalling ₹0 is almost always a forgotten rate,
        # not a deliberate free invoice — warn once rather than silently
        # billing nothing for real completed work. A *partially* free
        # invoice (some line items zero, others not) is common enough
        # (e.g. a comped visit alongside a paid one) that it's left alone.
        item_qty_raw = request.form.getlist("item_qty")
        item_rate_raw = request.form.getlist("item_rate")
        nonblank_total = sum(
            (float(item_qty_raw[i]) if i < len(item_qty_raw) and item_qty_raw[i] else 1)
            * (float(item_rate_raw[i]) if i < len(item_rate_raw) and item_rate_raw[i] else 0)
            for i, s in enumerate(item_services_raw) if s.strip()
        )
        if nonblank_total == 0 and not request.form.get("confirm_zero_total"):
            conn.close()
            msg = ("Every line item has a rate of ₹0, so this invoice would total ₹0 — "
                   "if that's not intentional, fill in the rate(s) first.")
            return render_confirm_page(msg, url_for("invoices_bp.new"), "confirm_zero_total",
                                        url_for("invoices_bp.new", client=client_id))

        inv_id = db_module.new_id()
        inv_number = next_invoice_number(conn)
        ts = db_module.now_iso()
        invoice_date = request.form.get("invoice_date") or date.today().isoformat()
        due_days = int(db_module.get_setting("invoiceDueDays", "30"))
        due_date = request.form.get("due_date") or (
            date.fromisoformat(invoice_date) + timedelta(days=due_days)
        ).isoformat()

        cgst_rate = float(request.form.get("cgst_rate") or db_module.get_setting("cgstRate", "9"))
        sgst_rate = float(request.form.get("sgst_rate") or db_module.get_setting("sgstRate", "9"))

        conn.execute(
            "INSERT INTO invoices (id, inv_number, invoice_date, due_date, client_id, "
            "work_order_no, service_period, cgst_rate, sgst_rate, notes, "
            "created_by, created_at, updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (inv_id, inv_number, invoice_date, due_date, client_id,
             request.form.get("work_order_no"), request.form.get("service_period"),
             cgst_rate, sgst_rate, request.form.get("notes"),
             session["username"], ts, ts),
        )

        services = request.form.getlist("item_service")
        descriptions = request.form.getlist("item_description")
        quantities = request.form.getlist("item_qty")
        rates = request.form.getlist("item_rate")
        visit_ids_per_item = request.form.getlist("item_visit_id")
        hsn = db_module.get_setting("hsnCode", "998531")
        # Index off `services` (the one field every row always has) rather
        # than zip()-ing the four lists together — zip() silently truncates
        # to the shortest list, so a row missing just one field (e.g. an
        # empty description) would drop every row after it, not just that
        # one field.
        linked_visit_ids = []
        for i, svc in enumerate(services):
            if not svc:
                continue
            desc = descriptions[i] if i < len(descriptions) else ""
            qty_f = float(quantities[i]) if i < len(quantities) and quantities[i] else 1
            rate_f = float(rates[i]) if i < len(rates) and rates[i] else 0
            amount = round(qty_f * rate_f, 2)
            conn.execute(
                "INSERT INTO invoice_line_items (id, invoice_id, service, description, "
                "quantity, unit_price, amount, hsn_sac) VALUES (?,?,?,?,?,?,?,?)",
                (db_module.new_id(), inv_id, svc, desc, qty_f, rate_f, amount, hsn),
            )
            if i < len(visit_ids_per_item) and visit_ids_per_item[i]:
                linked_visit_ids.append(visit_ids_per_item[i])

        # Mark any visits this invoice was built from as billed, so they
        # drop out of "still needs invoicing" lists and can't accidentally
        # be picked up again on a future invoice for the same client. The
        # AND invoice_id IS NULL guards against two people invoicing the
        # same visit at once — whoever's UPDATE lands second here loses
        # the race and that visit stays only on their invoice's line items
        # (still billed correctly) rather than silently overwriting the
        # first invoice's claim on it.
        lost_race_visits = []
        for vid in linked_visit_ids:
            cur = conn.execute(
                "UPDATE visit_schedules SET invoice_id=? WHERE id=? AND client_id=? "
                "AND invoice_id IS NULL",
                (inv_id, vid, client_id),
            )
            if cur.rowcount == 0:
                lost_race_visits.append(vid)

        _recalc_totals(conn, inv_id)
        conn.commit()
        db_module.log_audit(session["username"], "INVOICE_CREATED", "invoices",
                             inv_id, new_value=inv_number)
        conn.close()
        if lost_race_visits:
            flash(f"Invoice {inv_number} created, but {len(lost_race_visits)} of the "
                  f"selected visit(s) had already been invoiced by someone else in the "
                  f"meantime — double-check this invoice's line items are still correct.",
                  "error")
        else:
            flash(f"Invoice {inv_number} created.", "success")
        return redirect(url_for("invoices_bp.detail", invoice_id=inv_id))

    client_id = request.args.get("client", "")
    clients = conn.execute("SELECT * FROM clients WHERE status != 'Cancelled' ORDER BY name").fetchall()
    services_catalog = conn.execute("SELECT value FROM settings WHERE key='serviceCatalog'").fetchone()
    from utils import parse_json
    services_catalog = parse_json(services_catalog["value"] if services_catalog else None, [])
    due_days = db_module.get_setting("invoiceDueDays", "30")
    default_cgst = db_module.get_setting("cgstRate", "9")
    default_sgst = db_module.get_setting("sgstRate", "9")
    conn.close()
    default_due = (date.today() + timedelta(days=int(due_days))).isoformat()
    return render_template("invoices/form.html", clients=clients,
                            preselect_client=client_id,
                            services_catalog=services_catalog,
                            today=date.today().isoformat(), default_due=default_due,
                            default_cgst=default_cgst, default_sgst=default_sgst)


@invoices_bp.route("/<invoice_id>")
@login_required
@office_required
def detail(invoice_id):
    conn = db_module.get_db()
    invoice = conn.execute(
        "SELECT i.*, c.name AS client_name, c.address AS client_address, "
        "c.gst_number AS client_gst, c.phone AS client_phone, c.email AS client_email "
        "FROM invoices i JOIN clients c ON c.id = i.client_id WHERE i.id=?",
        (invoice_id,)
    ).fetchone()
    if not invoice:
        conn.close()
        abort(404)
    items = conn.execute(
        "SELECT * FROM invoice_line_items WHERE invoice_id=?", (invoice_id,)
    ).fetchall()
    payments = conn.execute(
        "SELECT * FROM payments WHERE invoice_id=? ORDER BY payment_date DESC",
        (invoice_id,)
    ).fetchall()
    conn.close()
    from utils import amount_in_words
    return render_template("invoices/detail.html", invoice=invoice, items=items,
                            payments=payments,
                            amount_words=amount_in_words(invoice["total_amount"]))


@invoices_bp.route("/<invoice_id>/pdf")
@login_required
@office_required
def pdf(invoice_id):
    conn = db_module.get_db()
    invoice = conn.execute(
        "SELECT i.*, c.name AS client_name, c.address AS client_address, "
        "c.gst_number AS client_gst, c.phone AS client_phone, c.email AS client_email "
        "FROM invoices i JOIN clients c ON c.id = i.client_id WHERE i.id=?",
        (invoice_id,)
    ).fetchone()
    if not invoice:
        conn.close()
        abort(404)
    items = conn.execute(
        "SELECT * FROM invoice_line_items WHERE invoice_id=?", (invoice_id,)
    ).fetchall()
    company = db_module.get_all_settings()
    conn.close()

    from pdf_templates.invoice import build_invoice_pdf
    buf = build_invoice_pdf(invoice, items, company)
    filename = f"{invoice['inv_number'].replace('/', '-')}.pdf"
    return send_file(buf, mimetype="application/pdf", as_attachment=False,
                      download_name=filename)


@invoices_bp.route("/<invoice_id>/email", methods=["POST"])
@login_required
@office_required
def email_invoice_route(invoice_id):
    conn = db_module.get_db()
    invoice = conn.execute(
        "SELECT i.*, c.name AS client_name, c.address AS client_address, "
        "c.gst_number AS client_gst, c.phone AS client_phone, c.email AS client_email "
        "FROM invoices i JOIN clients c ON c.id = i.client_id WHERE i.id=?",
        (invoice_id,)
    ).fetchone()
    if not invoice:
        conn.close()
        abort(404)
    if not invoice["client_email"]:
        conn.close()
        flash("This client has no email address on file.", "error")
        return redirect(url_for("invoices_bp.detail", invoice_id=invoice_id))

    items = conn.execute(
        "SELECT * FROM invoice_line_items WHERE invoice_id=?", (invoice_id,)
    ).fetchall()
    company = db_module.get_all_settings()
    conn.close()

    from pdf_templates.invoice import build_invoice_pdf
    from notifications_service import email_invoice
    pdf_bytes = build_invoice_pdf(invoice, items, company).getvalue()
    sent = email_invoice(invoice["client_email"], invoice["inv_number"], pdf_bytes)

    if sent:
        db_module.log_audit(session["username"], "INVOICE_EMAILED", "invoices",
                             invoice_id, new_value=invoice["client_email"])
        flash(f"Invoice emailed to {invoice['client_email']}.", "success")
    else:
        flash("Couldn't send the email — check SMTP settings are configured "
              "correctly under Settings.", "error")
    return redirect(url_for("invoices_bp.detail", invoice_id=invoice_id))


@invoices_bp.route("/<invoice_id>/void", methods=["POST"])
@login_required
@admin_required
def void(invoice_id):
    conn = db_module.get_db()
    invoice = conn.execute("SELECT * FROM invoices WHERE id=?", (invoice_id,)).fetchone()
    if not invoice:
        conn.close()
        abort(404)
    if invoice["payment_status"] == "Paid":
        conn.close()
        flash("A fully paid invoice cannot be voided — issue a credit note "
              "process outside the system instead, or contact support.", "error")
        return redirect(url_for("invoices_bp.detail", invoice_id=invoice_id))
    conn.execute("UPDATE invoices SET is_void=1, updated_at=? WHERE id=?",
                 (db_module.now_iso(), invoice_id))
    # Release any visits this invoice was built from back to "billable" —
    # otherwise a voided invoice leaves them permanently stuck pointing at
    # a dead invoice, invisible to the "still needs invoicing" list forever.
    conn.execute("UPDATE visit_schedules SET invoice_id=NULL WHERE invoice_id=?",
                 (invoice_id,))
    conn.commit()
    conn.close()
    db_module.log_audit(session["username"], "INVOICE_VOIDED", "invoices", invoice_id,
                         old_value=invoice["inv_number"])
    flash(f"Invoice {invoice['inv_number']} voided.", "success")
    return redirect(url_for("invoices_bp.index"))
