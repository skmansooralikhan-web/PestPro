import base64
import json
import os
import uuid
from datetime import date, timedelta

from flask import (Blueprint, render_template, request, redirect, url_for,
                    flash, session, abort, send_file, jsonify)

import db as db_module
from auth import login_required, office_required
from config import PHOTOS_DIR, Config
from utils import parse_json, to_json, create_notification

jobcards_bp = Blueprint("jobcards_bp", __name__)


@jobcards_bp.route("/tech-home")
@login_required
def tech_home():
    conn = db_module.get_db()
    today = date.today().isoformat()
    week_end = (date.today() + timedelta(days=7)).isoformat()
    username = session["username"]
    visits = conn.execute(
        "SELECT v.*, c.name AS client_name, c.address AS client_address, "
        "c.area AS client_area, c.phone AS client_phone, c.notes AS client_notes, "
        "j.id AS job_card_id "
        "FROM visit_schedules v JOIN clients c ON c.id = v.client_id "
        "LEFT JOIN job_cards j ON j.visit_id = v.id "
        "WHERE v.tech_name = ? AND v.scheduled_date BETWEEN ? AND ? "
        "AND v.status != 'Cancelled' ORDER BY v.scheduled_date, v.time_slot",
        (username, today, week_end)
    ).fetchall()
    conn.close()
    today_visits = [v for v in visits if v["scheduled_date"] == today]
    upcoming_visits = [v for v in visits if v["scheduled_date"] != today]
    return render_template("jobcards/tech_home.html", today_visits=today_visits,
                            upcoming_visits=upcoming_visits, today=today)


@jobcards_bp.route("/")
@login_required
@office_required
def index():
    conn = db_module.get_db()
    tech_filter = request.args.get("tech", "")
    sql = ("SELECT j.*, c.name AS client_name, v.invoice_id AS visit_invoice_id, "
           "i.inv_number FROM job_cards j "
           "JOIN clients c ON c.id = j.client_id "
           "LEFT JOIN visit_schedules v ON v.id = j.visit_id "
           "LEFT JOIN invoices i ON i.id = v.invoice_id WHERE 1=1")
    params = []
    if tech_filter:
        sql += " AND j.tech_name = ?"
        params.append(tech_filter)
    sql += " ORDER BY j.created_at DESC LIMIT 200"
    cards = conn.execute(sql, params).fetchall()
    techs = [t["username"] for t in conn.execute(
        "SELECT username FROM users WHERE role='technician'"
    ).fetchall()]
    conn.close()
    return render_template("jobcards/index.html", cards=cards, techs=techs,
                            tech_filter=tech_filter)


@jobcards_bp.route("/start/<visit_id>", methods=["GET", "POST"])
@login_required
def start(visit_id):
    conn = db_module.get_db()
    visit = conn.execute(
        "SELECT v.*, c.name AS client_name, c.address AS client_address, "
        "c.area AS client_area FROM visit_schedules v "
        "JOIN clients c ON c.id = v.client_id WHERE v.id=?", (visit_id,)
    ).fetchone()
    if not visit:
        conn.close()
        abort(404)

    existing = conn.execute("SELECT * FROM job_cards WHERE visit_id=?", (visit_id,)).fetchone()

    if request.method == "POST":
        pests_found = json.loads(request.form.get("pests_found_json") or "[]")
        treatments_done = json.loads(request.form.get("treatments_done_json") or "[]")
        chemicals_used = json.loads(request.form.get("chemicals_used_json") or "[]")
        photos_data = json.loads(request.form.get("photos_json") or "[]")
        visit_outcome = request.form.get("visit_outcome") or "Completed"

        # Validate stock and expiry before committing any deduction
        for item in chemicals_used:
            inv = conn.execute("SELECT * FROM inventory_items WHERE id=?",
                                (item["item_id"],)).fetchone()
            if not inv:
                conn.close()
                flash(f"Unknown inventory item in chemicals used.", "error")
                return redirect(url_for("jobcards_bp.start", visit_id=visit_id))
            if inv["expiry_date"] and inv["expiry_date"] < date.today().isoformat():
                conn.close()
                flash(f"{inv['name']} has expired and cannot be used.", "error")
                return redirect(url_for("jobcards_bp.start", visit_id=visit_id))
            qty = float(item.get("qty") or 0)
            if qty > inv["current_stock"]:
                conn.close()
                flash(f"Not enough stock of {inv['name']} "
                      f"({inv['current_stock']} {inv['unit']} available).", "error")
                return redirect(url_for("jobcards_bp.start", visit_id=visit_id))

        # Save uploaded photo blobs (base64 data URLs, each tagged before/after
        # by the form) to disk — keep the before/after tag alongside the saved
        # path so the job card detail page and any future report can group by it.
        saved_photos = []
        visit_photo_dir = PHOTOS_DIR / visit["scheduled_date"] / visit_id
        for photo in photos_data:
            try:
                header, encoded = photo["src"].split(",", 1)
                ext = "png" if "png" in header else "jpg"
                visit_photo_dir.mkdir(parents=True, exist_ok=True)
                fname = f"{uuid.uuid4().hex}.{ext}"
                (visit_photo_dir / fname).write_bytes(base64.b64decode(encoded))
                rel_path = str((visit_photo_dir / fname).relative_to(PHOTOS_DIR))
                saved_photos.append({"path": rel_path, "type": photo.get("type") or "before"})
            except (ValueError, OSError, KeyError, base64.binascii.Error):
                continue

        ts = db_module.now_iso()
        jc_id = existing["id"] if existing else db_module.new_id()
        signature_waived = 1 if request.form.get("signature_waived") == "on" else 0
        followup_required = 1 if request.form.get("followup_required") == "on" else 0

        # A follow-up visit gets created automatically the moment this job
        # card is submitted, rather than just recording a "needs follow-up"
        # flag that someone has to remember to act on later.
        followup_visit_id = existing["followup_visit_id"] if existing else None
        if followup_required and not followup_visit_id:
            followup_date = request.form.get("followup_date") or (
                (date.today() + timedelta(days=7)).isoformat())
            followup_visit_id = db_module.new_id()
            booked = conn.execute(
                "SELECT COUNT(*) AS c FROM visit_schedules WHERE client_id=? "
                "AND status != 'Cancelled' AND is_revisit=0", (visit["client_id"],)
            ).fetchone()["c"]
            conn.execute(
                "INSERT INTO visit_schedules (id, client_id, site_id, scheduled_date, "
                "visit_num, services, tech_name, time_slot, status, visit_notes, "
                "is_revisit, created_at, updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (followup_visit_id, visit["client_id"], visit["site_id"], followup_date,
                 booked + 1, visit["services"], visit["tech_name"], visit["time_slot"],
                 "Scheduled", f"Follow-up: {request.form.get('followup_reason') or ''}",
                 1, ts, ts),
            )
            # Write the audit row on this same connection rather than via
            # db_module.log_audit() (which opens its own connection) — the
            # writes above haven't committed yet, and SQLite's write lock
            # would deadlock a second connection trying to write first.
            conn.execute(
                "INSERT INTO audit_logs (id, username, action, table_name, record_id, "
                "old_value, new_value, created_at) VALUES (?,?,?,?,?,?,?,?)",
                (db_module.new_id(), session["username"], "FOLLOWUP_VISIT_SCHEDULED",
                 "visit_schedules", followup_visit_id, None,
                 f"{visit['client_name']} on {followup_date}", ts),
            )

        if existing:
            conn.execute(
                "UPDATE job_cards SET end_time=?, visit_outcome=?, pests_found=?, "
                "treatments_done=?, chemicals_used=?, observations=?, recommendations=?, "
                "followup_required=?, followup_reason=?, followup_visit_id=?, "
                "signature_waived=?, signature_waived_reason=?, client_signature=?, "
                "checkin_lat=?, checkin_lng=?, checkout_lat=?, checkout_lng=?, "
                "photos=?, updated_at=? WHERE id=?",
                (ts, visit_outcome, to_json(pests_found), to_json(treatments_done),
                 to_json(chemicals_used), request.form.get("observations"),
                 request.form.get("recommendations"),
                 followup_required, request.form.get("followup_reason"), followup_visit_id,
                 signature_waived, request.form.get("signature_waived_reason"),
                 request.form.get("client_signature"),
                 request.form.get("checkin_lat") or None,
                 request.form.get("checkin_lng") or None,
                 request.form.get("checkout_lat") or None,
                 request.form.get("checkout_lng") or None,
                 to_json((parse_json(existing["photos"]) + saved_photos)),
                 ts, jc_id),
            )
        else:
            conn.execute(
                "INSERT INTO job_cards (id, visit_id, client_id, tech_name, "
                "start_time, end_time, visit_outcome, pests_found, treatments_done, "
                "chemicals_used, observations, recommendations, followup_required, "
                "followup_reason, followup_visit_id, signature_waived, "
                "signature_waived_reason, client_signature, checkin_lat, checkin_lng, "
                "checkout_lat, checkout_lng, photos, created_at, updated_at) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (jc_id, visit_id, visit["client_id"], session["username"],
                 request.form.get("start_time") or ts, ts, visit_outcome,
                 to_json(pests_found), to_json(treatments_done),
                 to_json(chemicals_used), request.form.get("observations"),
                 request.form.get("recommendations"),
                 followup_required, request.form.get("followup_reason"), followup_visit_id,
                 signature_waived, request.form.get("signature_waived_reason"),
                 request.form.get("client_signature"),
                 request.form.get("checkin_lat") or None,
                 request.form.get("checkin_lng") or None,
                 request.form.get("checkout_lat") or None,
                 request.form.get("checkout_lng") or None,
                 to_json(saved_photos), ts, ts),
            )

        # Deduct inventory + write transaction log (only once, on first submit)
        if not existing:
            for item in chemicals_used:
                inv = conn.execute("SELECT * FROM inventory_items WHERE id=?",
                                    (item["item_id"],)).fetchone()
                qty = float(item.get("qty") or 0)
                new_balance = inv["current_stock"] - qty
                conn.execute(
                    "UPDATE inventory_items SET current_stock=?, updated_at=? WHERE id=?",
                    (new_balance, ts, inv["id"]),
                )
                conn.execute(
                    "INSERT INTO inventory_transactions (id, item_id, txn_type, "
                    "quantity, balance, visit_id, job_card_id, reference, done_by, "
                    "created_at) VALUES (?,?,?,?,?,?,?,?,?,?)",
                    (db_module.new_id(), inv["id"], "OUT", qty, new_balance,
                     visit_id, jc_id, f"Job card for {visit['client_name']}",
                     session["username"], ts),
                )
                if new_balance < inv["minimum_stock"]:
                    create_notification(
                        conn, "low_stock", "Low stock alert",
                        f"{inv['name']} is below minimum stock "
                        f"({new_balance} < {inv['minimum_stock']}).",
                    )

        conn.execute(
            "UPDATE visit_schedules SET status='Completed', completed_at=?, "
            "updated_at=? WHERE id=?", (ts, ts, visit_id)
        )
        conn.commit()
        db_module.log_audit(session["username"], "JOB_CARD_SUBMITTED", "job_cards",
                             jc_id, new_value=visit["client_name"])

        # Best-effort post-visit feedback link over WhatsApp (silently
        # no-ops if WhatsApp credentials aren't configured in Settings)
        if not existing:
            client_row = conn.execute(
                "SELECT phone FROM clients WHERE id=?", (visit["client_id"],)
            ).fetchone()
            if client_row and client_row["phone"]:
                from notifications_service import notify_post_visit_feedback
                feedback_url = request.host_url.rstrip("/") + url_for(
                    "feedback_bp.submit", token=visit_id
                )
                notify_post_visit_feedback(client_row["phone"], feedback_url)

        conn.close()
        if visit_outcome == "Completed":
            msg = "Job card submitted. Visit marked complete."
        else:
            msg = f"Job card submitted — recorded as {visit_outcome.lower()}."
        if followup_required and followup_visit_id and not (existing and existing["followup_visit_id"]):
            msg += " A follow-up visit has been scheduled automatically."
        flash(msg, "success")
        if session.get("role") == "technician":
            return redirect(url_for("jobcards_bp.tech_home"))
        return redirect(url_for("jobcards_bp.detail", job_card_id=jc_id))

    inventory_items = conn.execute(
        "SELECT id, name, unit, current_stock, category FROM inventory_items "
        "WHERE current_stock > 0 AND (expiry_date IS NULL OR expiry_date >= ?) "
        "ORDER BY name", (date.today().isoformat(),)
    ).fetchall()
    inventory_data = [[it["id"], it["name"], it["unit"], it["current_stock"]]
                       for it in inventory_items]
    services_catalog = parse_json(db_module.get_setting("serviceCatalog"), [])
    conn.close()
    return render_template("jobcards/form.html", visit=visit, existing=existing,
                            inventory_items=inventory_items, inventory_data=inventory_data,
                            services_catalog=services_catalog)


@jobcards_bp.route("/<job_card_id>")
@login_required
def detail(job_card_id):
    conn = db_module.get_db()
    jc = conn.execute(
        "SELECT j.*, c.name AS client_name, c.address AS client_address, "
        "c.area AS client_area, v.scheduled_date, v.invoice_id AS visit_invoice_id, "
        "i.inv_number FROM job_cards j "
        "JOIN clients c ON c.id = j.client_id "
        "LEFT JOIN visit_schedules v ON v.id = j.visit_id "
        "LEFT JOIN invoices i ON i.id = v.invoice_id WHERE j.id=?",
        (job_card_id,)
    ).fetchone()
    if not jc:
        conn.close()
        abort(404)
    if session.get("role") == "technician" and jc["tech_name"] != session["username"]:
        conn.close()
        abort(403)

    # Resolve chemical item_id -> a readable name/unit rather than showing
    # the raw internal ID, and keep each row's own dilution note alongside it.
    chemicals_used = parse_json(jc["chemicals_used"])
    for c in chemicals_used:
        item = conn.execute("SELECT name, unit FROM inventory_items WHERE id=?",
                             (c.get("item_id"),)).fetchone()
        c["name"] = item["name"] if item else c.get("item_id") or "—"
        c["unit"] = item["unit"] if item else ""

    followup_visit = None
    if jc["followup_visit_id"]:
        followup_visit = conn.execute(
            "SELECT id, scheduled_date, status FROM visit_schedules WHERE id=?",
            (jc["followup_visit_id"],)
        ).fetchone()

    photos = parse_json(jc["photos"])
    # Older job cards (before before/after tagging existed) stored photos as
    # a flat list of path strings — normalize those to the same {path, type}
    # shape so the template only needs to handle one format.
    photos = [p if isinstance(p, dict) else {"path": p, "type": "before"} for p in photos]

    conn.close()
    return render_template(
        "jobcards/detail.html", jc=jc,
        pests_found=parse_json(jc["pests_found"]),
        treatments_done=parse_json(jc["treatments_done"]),
        chemicals_used=chemicals_used,
        photos=photos,
        followup_visit=followup_visit,
    )


@jobcards_bp.route("/<job_card_id>/pdf")
@login_required
def pdf(job_card_id):
    conn = db_module.get_db()
    jc = conn.execute(
        "SELECT j.*, c.name AS client_name, c.address AS client_address, "
        "c.area AS client_area, v.scheduled_date FROM job_cards j "
        "JOIN clients c ON c.id = j.client_id "
        "LEFT JOIN visit_schedules v ON v.id = j.visit_id WHERE j.id=?",
        (job_card_id,)
    ).fetchone()
    if not jc:
        conn.close()
        abort(404)
    # Same access rule as the detail page: a technician can print their
    # own job cards (the common case — handing a copy to the customer
    # on-site right after finishing) but not anyone else's.
    if session.get("role") == "technician" and jc["tech_name"] != session["username"]:
        conn.close()
        abort(403)

    chemicals_used = parse_json(jc["chemicals_used"])
    for c in chemicals_used:
        item = conn.execute("SELECT name, unit FROM inventory_items WHERE id=?",
                             (c.get("item_id"),)).fetchone()
        c["name"] = item["name"] if item else c.get("item_id") or "—"
        c["unit"] = item["unit"] if item else ""

    followup_visit = None
    if jc["followup_visit_id"]:
        followup_visit = conn.execute(
            "SELECT id, scheduled_date, status FROM visit_schedules WHERE id=?",
            (jc["followup_visit_id"],)
        ).fetchone()

    company = db_module.get_all_settings()
    conn.close()

    from pdf_templates.job_card_print import build_job_card_pdf
    buf = build_job_card_pdf(jc, parse_json(jc["pests_found"]), parse_json(jc["treatments_done"]),
                              chemicals_used, followup_visit, company)
    filename = f"job-card-{jc['client_name'].replace(' ', '-')}-{(jc['start_time'] or '')[:10]}.pdf"
    return send_file(buf, mimetype="application/pdf", as_attachment=False,
                      download_name=filename)


@jobcards_bp.route("/photo/<path:relpath>")
@login_required
def photo(relpath):
    full = (PHOTOS_DIR / relpath).resolve()
    if PHOTOS_DIR.resolve() not in full.parents and full != PHOTOS_DIR.resolve():
        abort(403)
    if not full.exists():
        abort(404)
    return send_file(full)
