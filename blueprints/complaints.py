from datetime import date

from flask import Blueprint, render_template, request, redirect, url_for, flash, session, abort

import db as db_module
from auth import login_required, office_required

complaints_bp = Blueprint("complaints_bp", __name__)

COMPLAINT_TYPES = ["Re-infestation", "Service Quality", "Technician Behaviour",
                    "Billing", "Other"]


@complaints_bp.route("/")
@login_required
def index():
    conn = db_module.get_db()
    status = request.args.get("status", "")
    sql = ("SELECT co.*, c.name AS client_name FROM complaints co "
           "JOIN clients c ON c.id = co.client_id WHERE 1=1")
    params = []
    if status:
        sql += " AND co.status = ?"
        params.append(status)
    if session.get("role") == "technician":
        sql += " AND co.assigned_to = ?"
        params.append(session["username"])
    sql += " ORDER BY co.reported_date DESC"
    complaints = conn.execute(sql, params).fetchall()
    conn.close()
    return render_template("complaints/index.html", complaints=complaints, status=status)


@complaints_bp.route("/new", methods=["GET", "POST"])
@login_required
@office_required
def new():
    conn = db_module.get_db()
    if request.method == "POST":
        cid = db_module.new_id()
        ts = db_module.now_iso()
        conn.execute(
            "INSERT INTO complaints (id, client_id, visit_id, reported_date, "
            "complaint_type, description, status, assigned_to, created_at, updated_at) "
            "VALUES (?,?,?,?,?,?,?,?,?,?)",
            (cid, request.form["client_id"], request.form.get("visit_id") or None,
             request.form.get("reported_date") or date.today().isoformat(),
             request.form.get("complaint_type"), request.form["description"],
             "Open", request.form.get("assigned_to"), ts, ts),
        )
        conn.commit()
        db_module.log_audit(session["username"], "COMPLAINT_LOGGED", "complaints", cid)
        conn.close()
        flash("Complaint logged.", "success")
        return redirect(url_for("complaints_bp.index"))

    client_id = request.args.get("client", "")
    clients = conn.execute("SELECT id, name FROM clients ORDER BY name").fetchall()
    techs = [t["username"] for t in conn.execute(
        "SELECT username FROM users WHERE role IN ('technician','manager','admin')"
    ).fetchall()]
    conn.close()
    return render_template("complaints/form.html", clients=clients,
                            preselect_client=client_id, types=COMPLAINT_TYPES, techs=techs)


@complaints_bp.route("/<complaint_id>/update", methods=["POST"])
@login_required
def update(complaint_id):
    conn = db_module.get_db()
    complaint = conn.execute("SELECT * FROM complaints WHERE id=?", (complaint_id,)).fetchone()
    if not complaint:
        conn.close()
        abort(404)
    if session.get("role") == "technician" and complaint["assigned_to"] != session["username"]:
        conn.close()
        abort(403)

    status = request.form["status"]
    resolved_date = date.today().isoformat() if status in ("Resolved", "Closed") else None
    conn.execute(
        "UPDATE complaints SET status=?, resolution=?, resolved_date=?, updated_at=? "
        "WHERE id=?",
        (status, request.form.get("resolution"), resolved_date, db_module.now_iso(),
         complaint_id),
    )
    conn.commit()
    conn.close()
    db_module.log_audit(session["username"], "COMPLAINT_UPDATED", "complaints",
                         complaint_id, new_value=status)
    flash("Complaint updated.", "success")
    return redirect(url_for("complaints_bp.index"))
