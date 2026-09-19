from datetime import date

from flask import (Blueprint, render_template, request, redirect, url_for,
                    flash, session, abort)

import db as db_module
from auth import login_required, office_required
from utils import parse_json, to_json

leads_bp = Blueprint("leads_bp", __name__)

STAGES = ["New", "Contacted", "Quoted", "Won", "Lost"]
SOURCES = ["Referral", "Website", "Walk-in", "Phone Inquiry", "Social Media", "Other"]


@leads_bp.route("/")
@login_required
@office_required
def index():
    conn = db_module.get_db()
    source_filter = request.args.get("source", "")
    sql = "SELECT * FROM leads WHERE 1=1"
    params = []
    if source_filter:
        sql += " AND source = ?"
        params.append(source_filter)
    sql += " ORDER BY updated_at DESC"
    leads = conn.execute(sql, params).fetchall()
    conn.close()

    board = {stage: [l for l in leads if l["stage"] == stage] for stage in STAGES}
    total = len(leads)
    won = len(board["Won"])
    conversion_rate = round((won / total) * 100, 1) if total else 0

    return render_template("leads/index.html", board=board, stages=STAGES,
                            sources=SOURCES, source_filter=source_filter,
                            total=total, won=won, conversion_rate=conversion_rate)


@leads_bp.route("/new", methods=["GET", "POST"])
@login_required
@office_required
def new():
    if request.method == "POST":
        lid = db_module.new_id()
        ts = db_module.now_iso()
        conn = db_module.get_db()
        conn.execute(
            "INSERT INTO leads (id, name, phone, email, client_type, area, address, "
            "source, stage, estimated_value, assigned_to, notes, created_at, updated_at) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (lid, request.form["name"], request.form["phone"],
             request.form.get("email"), request.form.get("client_type"),
             request.form.get("area"), request.form.get("address"),
             request.form.get("source", "Other"), "New",
             float(request.form.get("estimated_value") or 0) or None,
             request.form.get("assigned_to"), request.form.get("notes"), ts, ts),
        )
        conn.commit()
        db_module.log_audit(session["username"], "LEAD_CREATED", "leads", lid,
                             new_value=request.form["name"])
        conn.close()
        flash(f"Lead \"{request.form['name']}\" added.", "success")
        return redirect(url_for("leads_bp.detail", lead_id=lid))

    conn = db_module.get_db()
    techs = [t["username"] for t in conn.execute(
        "SELECT username FROM users WHERE role IN ('manager','admin') OR role='technician'"
    ).fetchall()]
    areas = parse_json(db_module.get_setting("areaList"), [])
    conn.close()
    return render_template("leads/form.html", lead=None, sources=SOURCES,
                            techs=techs, areas=areas)


@leads_bp.route("/<lead_id>")
@login_required
@office_required
def detail(lead_id):
    conn = db_module.get_db()
    lead = conn.execute("SELECT * FROM leads WHERE id=?", (lead_id,)).fetchone()
    if not lead:
        conn.close()
        abort(404)
    quotations = conn.execute(
        "SELECT * FROM quotations WHERE lead_id=? ORDER BY created_at DESC", (lead_id,)
    ).fetchall()
    conn.close()
    return render_template("leads/detail.html", lead=lead, quotations=quotations,
                            stages=STAGES)


@leads_bp.route("/<lead_id>/edit", methods=["GET", "POST"])
@login_required
@office_required
def edit(lead_id):
    conn = db_module.get_db()
    lead = conn.execute("SELECT * FROM leads WHERE id=?", (lead_id,)).fetchone()
    if not lead:
        conn.close()
        abort(404)

    if request.method == "POST":
        conn.execute(
            "UPDATE leads SET name=?, phone=?, email=?, client_type=?, area=?, "
            "address=?, source=?, estimated_value=?, assigned_to=?, notes=?, "
            "updated_at=? WHERE id=?",
            (request.form["name"], request.form["phone"], request.form.get("email"),
             request.form.get("client_type"), request.form.get("area"),
             request.form.get("address"), request.form.get("source", "Other"),
             float(request.form.get("estimated_value") or 0) or None,
             request.form.get("assigned_to"), request.form.get("notes"),
             db_module.now_iso(), lead_id),
        )
        conn.commit()
        db_module.log_audit(session["username"], "LEAD_UPDATED", "leads", lead_id)
        conn.close()
        flash("Lead updated.", "success")
        return redirect(url_for("leads_bp.detail", lead_id=lead_id))

    techs = [t["username"] for t in conn.execute(
        "SELECT username FROM users WHERE disabled=0"
    ).fetchall()]
    areas = parse_json(db_module.get_setting("areaList"), [])
    conn.close()
    return render_template("leads/form.html", lead=lead, sources=SOURCES,
                            techs=techs, areas=areas)


@leads_bp.route("/<lead_id>/stage", methods=["POST"])
@login_required
@office_required
def update_stage(lead_id):
    conn = db_module.get_db()
    lead = conn.execute("SELECT * FROM leads WHERE id=?", (lead_id,)).fetchone()
    if not lead:
        conn.close()
        abort(404)

    new_stage = request.form["stage"]
    if new_stage not in STAGES:
        conn.close()
        abort(400)
    if new_stage == "Lost" and not request.form.get("lost_reason"):
        conn.close()
        flash("Give a reason before marking a lead as Lost — it helps spot "
              "patterns later.", "error")
        return redirect(url_for("leads_bp.detail", lead_id=lead_id))
    if new_stage == "Won":
        conn.close()
        flash("Use \"Convert to Client\" to move a lead to Won — that's what "
              "actually creates the client record.", "error")
        return redirect(url_for("leads_bp.detail", lead_id=lead_id))

    conn.execute(
        "UPDATE leads SET stage=?, lost_reason=?, updated_at=? WHERE id=?",
        (new_stage, request.form.get("lost_reason") if new_stage == "Lost" else None,
         db_module.now_iso(), lead_id),
    )
    conn.commit()
    db_module.log_audit(session["username"], "LEAD_STAGE_CHANGED", "leads", lead_id,
                         old_value=lead["stage"], new_value=new_stage)
    conn.close()
    flash(f"Lead moved to {new_stage}.", "success")
    return redirect(url_for("leads_bp.detail", lead_id=lead_id))


@leads_bp.route("/<lead_id>/convert", methods=["POST"])
@login_required
@office_required
def convert(lead_id):
    conn = db_module.get_db()
    lead = conn.execute("SELECT * FROM leads WHERE id=?", (lead_id,)).fetchone()
    if not lead:
        conn.close()
        abort(404)
    if lead["converted_client_id"]:
        conn.close()
        flash("This lead has already been converted.", "error")
        return redirect(url_for("leads_bp.detail", lead_id=lead_id))

    cid = db_module.new_id()
    ts = db_module.now_iso()
    conn.execute(
        "INSERT INTO clients (id, name, client_type, phone, email, area, address, "
        "contact_person, total_visits, visit_frequency, services, notes, status, "
        "created_at, updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (cid, lead["name"], lead["client_type"], lead["phone"], lead["email"],
         lead["area"], lead["address"], None,
         int(request.form.get("total_visits") or 4), "Monthly", to_json([]),
         lead["notes"], "Active", ts, ts),
    )
    conn.execute(
        "UPDATE leads SET stage='Won', converted_client_id=?, updated_at=? WHERE id=?",
        (cid, ts, lead_id),
    )
    # Any quotations already tied to this lead now belong to the new client too,
    # so "Convert to Invoice" becomes available on them.
    conn.execute("UPDATE quotations SET client_id=? WHERE lead_id=?", (cid, lead_id))
    conn.commit()
    db_module.log_audit(session["username"], "LEAD_CONVERTED", "leads", lead_id,
                         new_value=cid)
    conn.close()
    flash(f"{lead['name']} converted to a client.", "success")
    return redirect(url_for("clients_bp.index", client=cid))
