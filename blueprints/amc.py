from datetime import date, timedelta
import calendar

from flask import (Blueprint, render_template, request, redirect, url_for,
                    flash, session, abort)

import db as db_module
from auth import login_required, office_required
from utils import parse_json, to_json, next_contract_number, next_invoice_number, parse_date

amc_bp = Blueprint("amc_bp", __name__)


def _next_cycle_date(d: date, frequency: str) -> date:
    """Advance a date by one AMC billing cycle."""
    if frequency == "Monthly":
        months_to_add = 1
    elif frequency == "Quarterly":
        months_to_add = 3
    else:  # Annually
        months_to_add = 12
    month = d.month - 1 + months_to_add
    year = d.year + month // 12
    month = month % 12 + 1
    day = min(d.day, calendar.monthrange(year, month)[1])
    return date(year, month, day)


def _cycle_amount(total_amount, frequency) -> float:
    divisor = {"Monthly": 12, "Quarterly": 4, "Annually": 1}.get(frequency, 1)
    return round((total_amount or 0) / divisor, 2)


def generate_recurring_invoice(conn, contract):
    """Create one invoice for an AMC contract's next due billing cycle.

    Returns the new invoice number, or None if nothing is currently due
    (auto-invoicing off, contract inactive, next cycle date in the future,
    or the contract has run past its end date).
    """
    if not contract["auto_invoice"] or contract["status"] != "Active":
        return None
    next_date_d = parse_date(contract["next_invoice_date"] or contract["start_date"])
    end_date_d = parse_date(contract["end_date"])
    if not next_date_d or next_date_d > date.today():
        return None
    if end_date_d and next_date_d > end_date_d:
        return None

    client = conn.execute("SELECT * FROM clients WHERE id=?",
                           (contract["client_id"],)).fetchone()
    if not client:
        return None

    inv_id = db_module.new_id()
    inv_number = next_invoice_number(conn)
    ts = db_module.now_iso()
    due_days = int(db_module.get_setting("invoiceDueDays", "30"))
    cgst_rate = float(db_module.get_setting("cgstRate", "9"))
    sgst_rate = float(db_module.get_setting("sgstRate", "9"))
    if client["gst_exempt"]:
        cgst_rate = sgst_rate = 0.0
    amount = _cycle_amount(contract["amount"], contract["billing_frequency"])
    cycle_label = f"{next_date_d.isoformat()} ({contract['billing_frequency']})"

    conn.execute(
        "INSERT INTO invoices (id, inv_number, invoice_date, due_date, client_id, "
        "service_period, cgst_rate, sgst_rate, notes, created_by, created_at, "
        "updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
        (inv_id, inv_number, date.today().isoformat(),
         (date.today() + timedelta(days=due_days)).isoformat(), contract["client_id"],
         cycle_label, cgst_rate, sgst_rate,
         f"Auto-generated for AMC {contract['contract_number']}", "system", ts, ts),
    )
    hsn = db_module.get_setting("hsnCode", "998531")
    conn.execute(
        "INSERT INTO invoice_line_items (id, invoice_id, service, description, "
        "quantity, unit_price, amount, hsn_sac) VALUES (?,?,?,?,?,?,?,?)",
        (db_module.new_id(), inv_id, f"AMC — {contract['contract_type']}",
         f"Billing cycle: {cycle_label}", 1, amount, amount, hsn),
    )
    from blueprints.invoices import _recalc_totals
    _recalc_totals(conn, inv_id)

    new_next = _next_cycle_date(next_date_d, contract["billing_frequency"])
    conn.execute("UPDATE amc_contracts SET next_invoice_date=?, updated_at=? WHERE id=?",
                 (new_next.isoformat(), ts, contract["id"]))
    # Write the audit row on this same connection/transaction rather than via
    # db_module.log_audit() (which opens its own connection) — the caller's
    # writes above haven't committed yet, and SQLite's write lock would
    # deadlock a second connection trying to write before that commit lands.
    conn.execute(
        "INSERT INTO audit_logs (id, username, action, table_name, record_id, "
        "old_value, new_value, created_at) VALUES (?,?,?,?,?,?,?,?)",
        (db_module.new_id(), "system", "AMC_AUTO_INVOICE", "invoices", inv_id,
         None, f"{inv_number} for AMC {contract['contract_number']}", ts),
    )
    return inv_number


def _cycle_days(frequency: str) -> int:
    return {"Monthly": 30, "Quarterly": 91, "Annually": 365}.get(frequency, 365)


def generate_recurring_visit(conn, contract):
    """Auto-schedule an AMC contract's next due visit, spread evenly across
    each billing cycle (visits_per_cycle visits per cycle). Returns the
    scheduled date as a string if a visit was created, or None if nothing
    is currently due (auto-scheduling off, contract inactive, next visit
    date in the future, contract past its end date, or a visit already
    exists for that date — this last case still advances next_visit_date,
    so a duplicate run doesn't get stuck retrying the same date forever).
    """
    if not contract["auto_schedule"] or contract["status"] != "Active":
        return None
    next_date_d = parse_date(contract["next_visit_date"] or contract["start_date"])
    end_date_d = parse_date(contract["end_date"])
    if not next_date_d or next_date_d > date.today():
        return None
    if end_date_d and next_date_d > end_date_d:
        return None

    client = conn.execute("SELECT * FROM clients WHERE id=?",
                           (contract["client_id"],)).fetchone()
    if not client:
        return None

    ts = db_module.now_iso()
    created_date = None
    already = conn.execute(
        "SELECT id FROM visit_schedules WHERE amc_contract_id=? AND scheduled_date=? "
        "AND status != 'Cancelled'", (contract["id"], next_date_d.isoformat())
    ).fetchone()
    if not already:
        booked = conn.execute(
            "SELECT COUNT(*) AS c FROM visit_schedules WHERE client_id=? "
            "AND status != 'Cancelled' AND is_revisit=0", (contract["client_id"],)
        ).fetchone()["c"]
        vid = db_module.new_id()
        conn.execute(
            "INSERT INTO visit_schedules (id, client_id, scheduled_date, visit_num, "
            "services, tech_name, time_slot, status, visit_notes, is_revisit, "
            "amc_contract_id, created_at, updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (vid, contract["client_id"], next_date_d.isoformat(), booked + 1,
             contract["services"], contract["default_tech"], contract["default_time_slot"],
             "Scheduled", f"Auto-scheduled for AMC {contract['contract_number']}",
             0, contract["id"], ts, ts),
        )
        conn.execute(
            "INSERT INTO audit_logs (id, username, action, table_name, record_id, "
            "old_value, new_value, created_at) VALUES (?,?,?,?,?,?,?,?)",
            (db_module.new_id(), "system", "AMC_AUTO_SCHEDULE", "visit_schedules", vid,
             None, f"{client['name']} on {next_date_d.isoformat()}", ts),
        )
        created_date = next_date_d.isoformat()

    cycle_days = _cycle_days(contract["billing_frequency"])
    per_visit_days = max(1, cycle_days // max(1, contract["visits_per_cycle"] or 1))
    new_next = next_date_d + timedelta(days=per_visit_days)
    conn.execute("UPDATE amc_contracts SET next_visit_date=?, updated_at=? WHERE id=?",
                 (new_next.isoformat(), ts, contract["id"]))
    return created_date


@amc_bp.route("/")
@login_required
@office_required
def index():
    conn = db_module.get_db()
    status = request.args.get("status", "")
    sql = ("SELECT a.*, c.name AS client_name, c.area AS client_area FROM amc_contracts a "
           "JOIN clients c ON c.id = a.client_id WHERE 1=1")
    params = []
    if status:
        sql += " AND a.status = ?"
        params.append(status)
    sql += " ORDER BY a.end_date"
    contracts = conn.execute(sql, params).fetchall()

    today = date.today()
    forecast = {"30": 0, "60": 0, "90": 0}
    expiry_days = {}
    for c in contracts:
        if c["status"] != "Active":
            continue
        try:
            end = date.fromisoformat(c["end_date"])
        except ValueError:
            continue
        days = (end - today).days
        expiry_days[c["id"]] = days
        if 0 <= days <= 30:
            forecast["30"] += 1
        if 0 <= days <= 60:
            forecast["60"] += 1
        if 0 <= days <= 90:
            forecast["90"] += 1

    active_value = conn.execute(
        "SELECT COALESCE(SUM(amount),0) AS t FROM amc_contracts WHERE status='Active'"
    ).fetchone()["t"]
    techs = [t["username"] for t in conn.execute(
        "SELECT username FROM users WHERE role='technician' AND disabled=0"
    ).fetchall()]
    conn.close()
    return render_template("amc/index.html", contracts=contracts, status=status,
                            forecast=forecast, today=today.isoformat(),
                            active_value=active_value, techs=techs, expiry_days=expiry_days)


@amc_bp.route("/new", methods=["GET", "POST"])
@login_required
@office_required
def new():
    conn = db_module.get_db()
    if request.method == "POST":
        client_id = request.form["client_id"]
        start_date = request.form["start_date"]
        end_date = request.form["end_date"]
        if end_date <= start_date:
            conn.close()
            flash("AMC end date must be after the start date.", "error")
            return redirect(url_for("amc_bp.new", client=client_id))

        existing_active = conn.execute(
            "SELECT id FROM amc_contracts WHERE client_id=? AND contract_type=? "
            "AND status='Active'", (client_id, request.form["contract_type"])
        ).fetchone()
        if existing_active:
            conn.close()
            flash("This client already has an active AMC of that type. Renew "
                  "the existing contract instead.", "error")
            return redirect(url_for("amc_bp.new", client=client_id))

        aid = db_module.new_id()
        ts = db_module.now_iso()
        services = request.form.getlist("services")
        billing_frequency = request.form.get("billing_frequency", "Annually")
        auto_invoice = 1 if request.form.get("auto_invoice") == "on" else 0
        next_invoice_date = start_date if auto_invoice else None
        conn.execute(
            "INSERT INTO amc_contracts (id, client_id, contract_type, contract_number, "
            "start_date, end_date, amount, visit_count, services, warranty_expiry, "
            "notes, status, billing_frequency, auto_invoice, next_invoice_date, "
            "created_at, updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (aid, client_id, request.form["contract_type"], next_contract_number(conn),
             start_date, end_date, float(request.form.get("amount") or 0),
             int(request.form.get("visit_count") or 0), to_json(services),
             request.form.get("warranty_expiry"), request.form.get("notes"),
             "Active", billing_frequency, auto_invoice, next_invoice_date, ts, ts),
        )
        conn.commit()
        db_module.log_audit(session["username"], "AMC_CREATED", "amc_contracts", aid)
        conn.close()
        flash("AMC contract created.", "success")
        return redirect(url_for("amc_bp.index"))

    client_id = request.args.get("client", "")
    clients = conn.execute("SELECT id, name FROM clients ORDER BY name").fetchall()
    services_catalog = parse_json(db_module.get_setting("serviceCatalog"), [])
    conn.close()
    return render_template("amc/form.html", clients=clients,
                            preselect_client=client_id, contract=None,
                            services_catalog=services_catalog,
                            today=date.today().isoformat())


@amc_bp.route("/<contract_id>/renew", methods=["GET", "POST"])
@login_required
@office_required
def renew(contract_id):
    conn = db_module.get_db()
    old = conn.execute("SELECT * FROM amc_contracts WHERE id=?", (contract_id,)).fetchone()
    if not old:
        conn.close()
        abort(404)

    if request.method == "POST":
        new_start = request.form.get("start_date") or old["end_date"]
        new_end = request.form.get("end_date") or (
            date.fromisoformat(new_start) + timedelta(days=365)
        ).isoformat()
        aid = db_module.new_id()
        ts = db_module.now_iso()
        billing_frequency = request.form.get("billing_frequency", old["billing_frequency"] or "Annually")
        auto_invoice = 1 if request.form.get("auto_invoice") == "on" else 0
        next_invoice_date = new_start if auto_invoice else None
        conn.execute(
            "INSERT INTO amc_contracts (id, client_id, contract_type, contract_number, "
            "start_date, end_date, amount, visit_count, services, warranty_expiry, "
            "notes, status, renewed_from, billing_frequency, auto_invoice, "
            "next_invoice_date, created_at, updated_at) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (aid, old["client_id"], old["contract_type"], next_contract_number(conn),
             new_start, new_end, float(request.form.get("amount") or old["amount"] or 0),
             int(request.form.get("visit_count") or old["visit_count"] or 0),
             old["services"], request.form.get("warranty_expiry"),
             request.form.get("notes"), "Active", contract_id, billing_frequency,
             auto_invoice, next_invoice_date, ts, ts),
        )
        conn.execute("UPDATE amc_contracts SET status='Renewed', updated_at=? WHERE id=?",
                     (ts, contract_id))
        conn.commit()
        db_module.log_audit(session["username"], "AMC_RENEWED", "amc_contracts", aid,
                             old_value=old["contract_number"])
        conn.close()
        flash("AMC contract renewed.", "success")
        return redirect(url_for("amc_bp.index"))

    conn.close()
    suggested_start = old["end_date"]
    suggested_end = (date.fromisoformat(old["end_date"]) + timedelta(days=365)).isoformat()
    return render_template("amc/renew_form.html", contract=old,
                            suggested_start=suggested_start, suggested_end=suggested_end)


@amc_bp.route("/<contract_id>/billing", methods=["POST"])
@login_required
@office_required
def update_billing(contract_id):
    conn = db_module.get_db()
    contract = conn.execute("SELECT * FROM amc_contracts WHERE id=?", (contract_id,)).fetchone()
    if not contract:
        conn.close()
        abort(404)
    billing_frequency = request.form.get("billing_frequency", "Annually")
    auto_invoice = 1 if request.form.get("auto_invoice") == "on" else 0
    # Turning auto-invoicing on for the first time starts the cycle today;
    # turning it back on later resumes from wherever it left off.
    next_invoice_date = contract["next_invoice_date"]
    if auto_invoice and not next_invoice_date:
        next_invoice_date = date.today().isoformat()
    conn.execute(
        "UPDATE amc_contracts SET billing_frequency=?, auto_invoice=?, "
        "next_invoice_date=?, updated_at=? WHERE id=?",
        (billing_frequency, auto_invoice, next_invoice_date if auto_invoice else None,
         db_module.now_iso(), contract_id),
    )
    conn.commit()
    db_module.log_audit(session["username"], "AMC_BILLING_UPDATED", "amc_contracts",
                         contract_id, new_value=f"{billing_frequency}, auto={auto_invoice}")
    conn.close()
    flash("Billing settings updated.", "success")
    return redirect(url_for("amc_bp.index"))


@amc_bp.route("/<contract_id>/scheduling", methods=["POST"])
@login_required
@office_required
def update_scheduling(contract_id):
    conn = db_module.get_db()
    contract = conn.execute("SELECT * FROM amc_contracts WHERE id=?", (contract_id,)).fetchone()
    if not contract:
        conn.close()
        abort(404)
    auto_schedule = 1 if request.form.get("auto_schedule") == "on" else 0
    visits_per_cycle = int(request.form.get("visits_per_cycle") or 1)
    default_tech = request.form.get("default_tech") or None
    default_time_slot = request.form.get("default_time_slot") or None
    # Same as auto-invoicing: turning it on for the first time starts the
    # cycle today, turning it back on later resumes where it left off.
    next_visit_date = contract["next_visit_date"]
    if auto_schedule and not next_visit_date:
        next_visit_date = date.today().isoformat()
    conn.execute(
        "UPDATE amc_contracts SET auto_schedule=?, visits_per_cycle=?, default_tech=?, "
        "default_time_slot=?, next_visit_date=?, updated_at=? WHERE id=?",
        (auto_schedule, visits_per_cycle, default_tech, default_time_slot,
         next_visit_date if auto_schedule else None, db_module.now_iso(), contract_id),
    )
    conn.commit()
    db_module.log_audit(session["username"], "AMC_SCHEDULING_UPDATED", "amc_contracts",
                         contract_id, new_value=f"auto={auto_schedule}, {visits_per_cycle}/cycle")
    conn.close()
    flash("Visit scheduling settings updated.", "success")
    return redirect(url_for("amc_bp.index"))


@amc_bp.route("/<contract_id>/schedule-visit", methods=["POST"])
@login_required
@office_required
def schedule_visit_now(contract_id):
    conn = db_module.get_db()
    contract = conn.execute("SELECT * FROM amc_contracts WHERE id=?", (contract_id,)).fetchone()
    if not contract:
        conn.close()
        abort(404)
    scheduled_date = generate_recurring_visit(conn, contract)
    conn.commit()
    conn.close()
    if scheduled_date:
        flash(f"Scheduled a visit for {scheduled_date}.", "success")
    else:
        flash("Nothing due yet — check that visit auto-scheduling is enabled and the "
              "next visit date has arrived (or turn it on first).", "error")
    return redirect(url_for("amc_bp.index"))


@amc_bp.route("/<contract_id>/generate-invoice", methods=["POST"])
@login_required
@office_required
def generate_invoice_now(contract_id):
    conn = db_module.get_db()
    contract = conn.execute("SELECT * FROM amc_contracts WHERE id=?", (contract_id,)).fetchone()
    if not contract:
        conn.close()
        abort(404)
    inv_number = generate_recurring_invoice(conn, contract)
    conn.commit()
    conn.close()
    if inv_number:
        flash(f"Generated invoice {inv_number} for this billing cycle.", "success")
    else:
        flash("Nothing due yet — check that auto-invoicing is enabled and the "
              "next billing date has arrived (or turn it on first).", "error")
    return redirect(url_for("amc_bp.index"))


@amc_bp.route("/<contract_id>/cancel", methods=["POST"])
@login_required
@office_required
def cancel(contract_id):
    conn = db_module.get_db()
    conn.execute("UPDATE amc_contracts SET status='Cancelled', updated_at=? WHERE id=?",
                 (db_module.now_iso(), contract_id))
    conn.commit()
    conn.close()
    db_module.log_audit(session["username"], "AMC_CANCELLED", "amc_contracts", contract_id)
    flash("AMC contract cancelled.", "success")
    return redirect(url_for("amc_bp.index"))
