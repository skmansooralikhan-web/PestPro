from datetime import date

from flask import (Blueprint, render_template, request, redirect, url_for,
                    flash, session, jsonify, abort, send_file)

import db as db_module
from auth import login_required, office_required, admin_required
from utils import parse_json, to_json, normalize_phone, render_confirm_page

clients_bp = Blueprint("clients_bp", __name__)


def _client_stats(conn, client_id: str) -> dict:
    client = conn.execute("SELECT * FROM clients WHERE id=?", (client_id,)).fetchone()
    if not client:
        return {}

    completed = conn.execute(
        "SELECT COUNT(*) AS c FROM visit_schedules WHERE client_id=? "
        "AND status='Completed' AND is_revisit=0", (client_id,)
    ).fetchone()["c"]
    pending = conn.execute(
        "SELECT COUNT(*) AS c FROM visit_schedules WHERE client_id=? "
        "AND status IN ('Scheduled','In Progress') AND is_revisit=0", (client_id,)
    ).fetchone()["c"]
    total = client["total_visits"] or 0
    remaining = max(total - completed, 0)

    operators = conn.execute(
        "SELECT DISTINCT tech_name FROM visit_schedules WHERE client_id=? "
        "AND tech_name IS NOT NULL AND tech_name != ''", (client_id,)
    ).fetchall()

    contracted_services = parse_json(client["services"])
    done_services = set()
    rows = conn.execute(
        "SELECT services FROM visit_schedules WHERE client_id=? AND status='Completed'",
        (client_id,)
    ).fetchall()
    for r in rows:
        for s in parse_json(r["services"]):
            done_services.add(s)
    pending_services = [s for s in contracted_services if s not in done_services]

    last_visits = conn.execute(
        "SELECT * FROM visit_schedules WHERE client_id=? ORDER BY scheduled_date DESC LIMIT 3",
        (client_id,)
    ).fetchall()

    return {
        "total": total, "completed": completed, "pending": pending,
        "assigned": completed + pending, "remaining": remaining,
        "pct": round((completed / total) * 100) if total else 0,
        "operators": [o["tech_name"] for o in operators],
        "contracted_services": contracted_services,
        "done_services": sorted(done_services),
        "pending_services": pending_services,
        "last_visits": last_visits,
    }


def _full_client_context(conn, client_id: str) -> dict:
    """Everything clients/_detail.html needs, gathered in one place so the
    full-page load (index, via ?client=<id>) and the AJAX panel route never
    drift out of sync with what the template actually references."""
    client = conn.execute("SELECT * FROM clients WHERE id=?", (client_id,)).fetchone()
    if not client:
        return None
    return {
        "client": client,
        "detail": _client_stats(conn, client_id),
        "sites": conn.execute(
            "SELECT * FROM client_sites WHERE client_id=? ORDER BY site_name",
            (client_id,)).fetchall(),
        "visits": conn.execute(
            "SELECT * FROM visit_schedules WHERE client_id=? ORDER BY scheduled_date DESC",
            (client_id,)).fetchall(),
        "complaints": conn.execute(
            "SELECT * FROM complaints WHERE client_id=? ORDER BY reported_date DESC",
            (client_id,)).fetchall(),
        "invoices": conn.execute(
            "SELECT * FROM invoices WHERE client_id=? ORDER BY invoice_date DESC",
            (client_id,)).fetchall(),
        "feedback": conn.execute(
            "SELECT * FROM feedback WHERE client_id=? ORDER BY submitted_at DESC",
            (client_id,)).fetchall(),
    }


@clients_bp.route("/")
@login_required
def index():
    conn = db_module.get_db()
    q = request.args.get("q", "").strip()
    status = request.args.get("status", "")
    area = request.args.get("area", "")
    tech = request.args.get("tech", "")
    sort = request.args.get("sort", "name")

    sql = "SELECT * FROM clients WHERE 1=1"
    params = []
    if q:
        sql += " AND (name LIKE ? OR phone LIKE ? OR area LIKE ?)"
        params += [f"%{q}%", f"%{q}%", f"%{q}%"]
    if status:
        sql += " AND status = ?"
        params.append(status)
    if area:
        sql += " AND area = ?"
        params.append(area)
    if tech:
        sql += " AND assigned_tech = ?"
        params.append(tech)

    order_map = {
        "name": "name ASC",
        "recent": "updated_at DESC",
        "outstanding": "name ASC",  # computed client-side of the query below
    }
    sql += f" ORDER BY {order_map.get(sort, 'name ASC')}"

    clients = conn.execute(sql, params).fetchall()

    # progress bar data per client for the list cards
    client_rows = []
    for c in clients:
        completed = conn.execute(
            "SELECT COUNT(*) AS n FROM visit_schedules WHERE client_id=? "
            "AND status='Completed' AND is_revisit=0", (c["id"],)
        ).fetchone()["n"]
        client_rows.append({"c": c, "completed": completed,
                             "pct": round((completed / c["total_visits"]) * 100)
                             if c["total_visits"] else 0})

    areas = [a["area"] for a in conn.execute(
        "SELECT DISTINCT area FROM clients WHERE area IS NOT NULL AND area != '' "
        "ORDER BY area"
    ).fetchall()]
    techs = [t["username"] for t in conn.execute(
        "SELECT username FROM users WHERE role='technician' AND disabled=0"
    ).fetchall()]

    selected_id = request.args.get("client")
    ctx = _full_client_context(conn, selected_id) if selected_id else None

    conn.close()
    return render_template(
        "clients/index.html", client_rows=client_rows, q=q, status=status,
        area=area, tech=tech, sort=sort, areas=areas, techs=techs,
        selected_client=ctx["client"] if ctx else None, **(ctx or {}),
    )


@clients_bp.route("/<client_id>/panel")
@login_required
def detail_panel(client_id):
    """AJAX partial used by the directory's right-hand panel."""
    conn = db_module.get_db()
    ctx = _full_client_context(conn, client_id)
    conn.close()
    if not ctx:
        abort(404)
    return render_template("clients/_detail.html", **ctx)


@clients_bp.route("/<client_id>/stats.json")
@login_required
def stats_json(client_id):
    """JSON stats used by the Smart Schedule Visit modal."""
    conn = db_module.get_db()
    client = conn.execute("SELECT * FROM clients WHERE id=?", (client_id,)).fetchone()
    if not client:
        conn.close()
        return jsonify({"error": "not found"}), 404
    detail = _client_stats(conn, client_id)
    conn.close()
    return jsonify({
        "id": client["id"], "name": client["name"],
        "assigned_tech": client["assigned_tech"],
        "preferred_days": parse_json(client["preferred_days"]),
        "total": detail["total"], "completed": detail["completed"],
        "pending": detail["pending"], "assigned": detail["assigned"],
        "remaining": detail["remaining"],
        "operators": detail["operators"],
        "contracted_services": detail["contracted_services"],
        "done_services": detail["done_services"],
        "pending_services": detail["pending_services"],
        "last_visits": [
            {"date": v["scheduled_date"], "services": parse_json(v["services"]),
             "tech": v["tech_name"], "status": v["status"]}
            for v in detail["last_visits"]
        ],
    })


@clients_bp.route("/<client_id>/billable-visits.json")
@login_required
def billable_visits_json(client_id):
    """Completed visits for this client that haven't been put on an
    invoice yet, plus the client's overall visit progress — used by the
    invoice form to let staff pick which visits to bill for instead of
    typing line items from scratch."""
    conn = db_module.get_db()
    client = conn.execute("SELECT * FROM clients WHERE id=?", (client_id,)).fetchone()
    if not client:
        conn.close()
        return jsonify({"error": "not found"}), 404
    detail = _client_stats(conn, client_id)
    visits = conn.execute(
        "SELECT v.id, v.scheduled_date, v.services, v.tech_name FROM visit_schedules v "
        "LEFT JOIN job_cards j ON j.visit_id = v.id "
        "LEFT JOIN amc_contracts a ON a.id = v.amc_contract_id "
        "WHERE v.client_id=? AND v.status='Completed' AND v.invoice_id IS NULL "
        # Only exclude an AMC-linked visit when that contract is actually,
        # currently auto-invoicing for it — auto_schedule and auto_invoice
        # are independent toggles (a contract can auto-schedule visits but
        # still bill them manually), and a contract can also be cancelled
        # or have auto-invoicing turned off after a visit was completed
        # under it. Excluding every AMC-linked visit unconditionally would
        # make those visits permanently unbillable anywhere in that case —
        # this only excludes the ones genuinely still covered elsewhere.
        "AND COALESCE(a.auto_invoice, 0) = 0 "
        # A visit whose job card came back Incomplete (no access, refused,
        # etc.) had no actual service delivered — nothing to bill for. A
        # visit with no job card at all (e.g. marked Completed by hand,
        # not through the mobile flow) is treated as billable as normal.
        "AND COALESCE(j.visit_outcome, 'Completed') = 'Completed' "
        "ORDER BY v.scheduled_date", (client_id,)
    ).fetchall()
    conn.close()
    return jsonify({
        "total": detail["total"], "completed": detail["completed"],
        "assigned": detail["assigned"], "remaining": detail["remaining"],
        "visits": [
            {"id": v["id"], "date": v["scheduled_date"],
             "services": parse_json(v["services"]), "tech": v["tech_name"]}
            for v in visits
        ],
    })


@clients_bp.route("/<client_id>/job-sheet")
@login_required
def job_sheet(client_id):
    """Printable 8x5 job sheet — a physical visit-log card for this client,
    pre-filled with up to 4 upcoming (or, failing that, most recent) visits'
    date and assigned operator, ready for the operator to fill in details
    and sign on-site at each visit."""
    conn = db_module.get_db()
    client = conn.execute("SELECT * FROM clients WHERE id=?", (client_id,)).fetchone()
    if not client:
        conn.close()
        abort(404)

    visits = list(conn.execute(
        "SELECT id, scheduled_date, tech_name FROM visit_schedules WHERE client_id=? "
        "AND status IN ('Scheduled','In Progress') AND scheduled_date >= ? "
        "ORDER BY scheduled_date LIMIT 4",
        (client_id, date.today().isoformat())
    ).fetchall())
    if len(visits) < 4:
        seen_ids = {v["id"] for v in visits}
        backfill = conn.execute(
            "SELECT id, scheduled_date, tech_name FROM visit_schedules WHERE client_id=? "
            "ORDER BY scheduled_date DESC", (client_id,)
        ).fetchall()
        for v in backfill:
            if v["id"] in seen_ids:
                continue
            visits.append(v)
            if len(visits) >= 4:
                break
    company = db_module.get_all_settings()
    conn.close()

    from pdf_templates.job_sheet import build_job_sheet_pdf
    buf = build_job_sheet_pdf(client, visits[:4], company)
    safe_name = "".join(c if c.isalnum() else "-" for c in client["name"])
    return send_file(buf, mimetype="application/pdf",
                      download_name=f"job-sheet-{safe_name}.pdf")


@clients_bp.route("/import", methods=["GET", "POST"])
@login_required
@office_required
def bulk_import():
    if request.method == "POST":
        file = request.files.get("file")
        if not file or not file.filename:
            flash("Choose a CSV or Excel file first.", "error")
            return redirect(url_for("clients_bp.bulk_import"))

        ext = file.filename.rsplit(".", 1)[-1].lower()
        rows = []
        try:
            if ext == "csv":
                import csv, io
                text = file.read().decode("utf-8-sig")
                rows = list(csv.DictReader(io.StringIO(text)))
            elif ext in ("xlsx", "xls"):
                from openpyxl import load_workbook
                wb = load_workbook(file, read_only=True, data_only=True)
                ws = wb.active
                rows_iter = ws.iter_rows(values_only=True)
                headers = [str(h).strip() if h else "" for h in next(rows_iter)]
                for r in rows_iter:
                    if not any(r):
                        continue
                    rows.append(dict(zip(headers, r)))
            else:
                flash("Only .csv and .xlsx files are supported.", "error")
                return redirect(url_for("clients_bp.bulk_import"))
        except Exception:
            flash("Couldn't read that file — please use the template format.", "error")
            return redirect(url_for("clients_bp.bulk_import"))

        conn = db_module.get_db()
        created, skipped, duplicates = 0, 0, 0
        ts = db_module.now_iso()
        # Seed with every existing client's normalized phone, then add each
        # newly-imported one as we go — this catches duplicates both against
        # clients already in the system and between rows within this same
        # file (e.g. the same customer listed twice in someone's old sheet).
        seen_phones = {normalize_phone(c["phone"]) for c in conn.execute("SELECT phone FROM clients").fetchall()}
        for row in rows:
            name = str(row.get("name") or "").strip()
            phone = str(row.get("phone") or "").strip()
            if not name or not phone:
                skipped += 1
                continue
            phone_norm = normalize_phone(phone)
            if phone_norm and phone_norm in seen_phones:
                duplicates += 1
                continue
            if phone_norm:
                seen_phones.add(phone_norm)
            services = [s.strip() for s in str(row.get("services") or "").split(",") if s.strip()]
            conn.execute(
                "INSERT INTO clients (id, name, client_type, phone, email, area, "
                "address, contact_person, total_visits, visit_frequency, services, "
                "assigned_tech, contract_amount, status, created_at, updated_at) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (db_module.new_id(), name, str(row.get("client_type") or "").strip() or None,
                 phone, str(row.get("email") or "").strip() or None,
                 str(row.get("area") or "").strip() or None,
                 str(row.get("address") or "").strip() or None,
                 str(row.get("contact_person") or "").strip() or None,
                 int(row.get("total_visits") or 4) if str(row.get("total_visits") or "").strip() else 4,
                 str(row.get("visit_frequency") or "Weekly").strip() or "Weekly",
                 to_json(services), str(row.get("assigned_tech") or "").strip() or None,
                 float(row.get("contract_amount") or 0) or None, "Active", ts, ts),
            )
            created += 1
        conn.commit()
        db_module.log_audit(session["username"], "CLIENTS_BULK_IMPORTED", "clients",
                             new_value=f"{created} created, {skipped} skipped, {duplicates} duplicates")
        conn.close()
        msg = f"Imported {created} client(s)."
        if skipped:
            msg += f" Skipped {skipped} row(s) missing a name or phone."
        if duplicates:
            msg += f" Skipped {duplicates} row(s) with a phone number already on file."
        flash(msg, "success")
        return redirect(url_for("clients_bp.index"))

    return render_template("clients/import.html")


@clients_bp.route("/import/template.csv")
@login_required
@office_required
def import_template():
    import io
    csv_content = (
        "name,phone,email,client_type,area,address,contact_person,total_visits,"
        "visit_frequency,services,assigned_tech,contract_amount\n"
        "Sunrise Apartments,9876500001,manager@sunrise.example,Apartment,Kondapur,"
        "Plot 12 Kondapur Main Road,Asha Rao,12,Monthly,"
        "\"General Pest Control,Rodent Control\",tech,18000\n"
    )
    mem = io.BytesIO(csv_content.encode("utf-8"))
    return send_file(mem, mimetype="text/csv", as_attachment=True,
                      download_name="pct_client_import_template.csv")


@clients_bp.route("/new", methods=["GET", "POST"])
@login_required
@office_required
def new():
    conn = db_module.get_db()
    if request.method == "POST":
        # A phone number matching an existing client is almost always a
        # typo'd duplicate rather than a genuinely new client — warn (with
        # an override, the same soft-warning pattern used for scheduling
        # conflicts elsewhere) rather than silently allowing it, since a
        # duplicate client record fragments that customer's visit and
        # billing history across two entries with no easy way to notice.
        new_phone_norm = normalize_phone(request.form.get("phone", ""))
        if new_phone_norm and not request.form.get("confirm_duplicate"):
            existing = conn.execute(
                "SELECT id, name, phone, status FROM clients"
            ).fetchall()
            match = next((c for c in existing if normalize_phone(c["phone"]) == new_phone_norm), None)
            if match:
                conn.close()
                msg = (f"A client named \"{match['name']}\" ({match['status']}) already has this "
                       f"phone number. Submit again to add this as a separate client anyway.")
                return render_confirm_page(msg, url_for("clients_bp.new"), "confirm_duplicate",
                                            url_for("clients_bp.new"))

        cid = db_module.new_id()
        ts = db_module.now_iso()
        services = request.form.getlist("services")
        preferred_days = request.form.getlist("preferred_days")
        conn.execute(
            "INSERT INTO clients (id, name, client_type, phone, email, area, address, "
            "gst_number, gst_exempt, contact_person, total_visits, visit_frequency, "
            "services, preferred_days, assigned_tech, contract_amount, notes, status, "
            "latitude, longitude, created_at, updated_at) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (cid, request.form["name"], request.form.get("client_type"),
             request.form["phone"], request.form.get("email"),
             request.form.get("area"), request.form.get("address"),
             request.form.get("gst_number"),
             1 if request.form.get("gst_exempt") == "on" else 0,
             request.form.get("contact_person"),
             int(request.form.get("total_visits") or 4),
             request.form.get("visit_frequency", "Weekly"),
             to_json(services), to_json(preferred_days),
             request.form.get("assigned_tech"),
             float(request.form.get("contract_amount") or 0) or None,
             request.form.get("notes"),
             request.form.get("status", "Active"),
             float(request.form.get("latitude") or 0) or None,
             float(request.form.get("longitude") or 0) or None, ts, ts),
        )
        conn.commit()
        db_module.log_audit(session["username"], "CLIENT_CREATED", "clients", cid,
                             new_value=request.form["name"])
        conn.close()
        flash(f"Client \"{request.form['name']}\" added.", "success")
        return redirect(url_for("clients_bp.index", client=cid))

    services_catalog = parse_json(db_module.get_setting("serviceCatalog"), [])
    areas = parse_json(db_module.get_setting("areaList"), [])
    techs = [t["username"] for t in conn.execute(
        "SELECT username FROM users WHERE role='technician' AND disabled=0"
    ).fetchall()]
    conn.close()
    return render_template("clients/form.html", client=None,
                            services_catalog=services_catalog, areas=areas,
                            techs=techs)


@clients_bp.route("/<client_id>/edit", methods=["GET", "POST"])
@login_required
@office_required
def edit(client_id):
    conn = db_module.get_db()
    client = conn.execute("SELECT * FROM clients WHERE id=?", (client_id,)).fetchone()
    if not client:
        conn.close()
        abort(404)

    if request.method == "POST":
        new_phone_norm = normalize_phone(request.form.get("phone", ""))
        if new_phone_norm and not request.form.get("confirm_duplicate"):
            others = conn.execute(
                "SELECT id, name, phone, status FROM clients WHERE id != ?", (client_id,)
            ).fetchall()
            match = next((c for c in others if normalize_phone(c["phone"]) == new_phone_norm), None)
            if match:
                conn.close()
                msg = (f"A client named \"{match['name']}\" ({match['status']}) already has this "
                       f"phone number. Submit again to save it anyway.")
                return render_confirm_page(msg, url_for("clients_bp.edit", client_id=client_id),
                                            "confirm_duplicate",
                                            url_for("clients_bp.edit", client_id=client_id))

        services = request.form.getlist("services")
        preferred_days = request.form.getlist("preferred_days")
        old = dict(client)
        conn.execute(
            "UPDATE clients SET name=?, client_type=?, phone=?, email=?, area=?, "
            "address=?, gst_number=?, gst_exempt=?, contact_person=?, total_visits=?, "
            "visit_frequency=?, services=?, preferred_days=?, assigned_tech=?, "
            "contract_amount=?, notes=?, status=?, latitude=?, longitude=?, "
            "updated_at=? WHERE id=?",
            (request.form["name"], request.form.get("client_type"),
             request.form["phone"], request.form.get("email"),
             request.form.get("area"), request.form.get("address"),
             request.form.get("gst_number"),
             1 if request.form.get("gst_exempt") == "on" else 0,
             request.form.get("contact_person"),
             int(request.form.get("total_visits") or 4),
             request.form.get("visit_frequency", "Weekly"),
             to_json(services), to_json(preferred_days),
             request.form.get("assigned_tech"),
             float(request.form.get("contract_amount") or 0) or None,
             request.form.get("notes"), request.form.get("status", "Active"),
             float(request.form.get("latitude") or 0) or None,
             float(request.form.get("longitude") or 0) or None,
             db_module.now_iso(), client_id),
        )
        conn.commit()
        db_module.log_audit(session["username"], "CLIENT_UPDATED", "clients",
                             client_id, old_value=old["name"],
                             new_value=request.form["name"])
        conn.close()
        flash("Client updated.", "success")
        return redirect(url_for("clients_bp.index", client=client_id))

    services_catalog = parse_json(db_module.get_setting("serviceCatalog"), [])
    areas = parse_json(db_module.get_setting("areaList"), [])
    techs = [t["username"] for t in conn.execute(
        "SELECT username FROM users WHERE role='technician' AND disabled=0"
    ).fetchall()]
    conn.close()
    return render_template(
        "clients/form.html", client=client, services_catalog=services_catalog,
        areas=areas, techs=techs,
        client_services=parse_json(client["services"]),
        client_days=parse_json(client["preferred_days"]),
    )


@clients_bp.route("/<client_id>/deactivate", methods=["POST"])
@login_required
@office_required
def deactivate(client_id):
    conn = db_module.get_db()
    conn.execute("UPDATE clients SET status='On Hold', updated_at=? WHERE id=?",
                 (db_module.now_iso(), client_id))
    conn.commit()
    conn.close()
    db_module.log_audit(session["username"], "CLIENT_DEACTIVATED", "clients", client_id)
    flash("Client set to On Hold.", "success")
    return redirect(url_for("clients_bp.index", client=client_id))


@clients_bp.route("/<client_id>/delete", methods=["POST"])
@login_required
@admin_required
def delete(client_id):
    conn = db_module.get_db()
    client = conn.execute("SELECT name FROM clients WHERE id=?", (client_id,)).fetchone()
    conn.execute("DELETE FROM clients WHERE id=?", (client_id,))
    conn.commit()
    conn.close()
    db_module.log_audit(session["username"], "CLIENT_DELETED", "clients", client_id,
                         old_value=client["name"] if client else None)
    flash("Client deleted.", "success")
    return redirect(url_for("clients_bp.index"))


# ---- Client sites -----------------------------------------------------------

@clients_bp.route("/<client_id>/sites/new", methods=["POST"])
@login_required
@office_required
def add_site(client_id):
    conn = db_module.get_db()
    conn.execute(
        "INSERT INTO client_sites (id, client_id, site_name, address, area, contact, "
        "notes, created_at) VALUES (?,?,?,?,?,?,?,?)",
        (db_module.new_id(), client_id, request.form["site_name"],
         request.form.get("address"), request.form.get("area"),
         request.form.get("contact"), request.form.get("notes"),
         db_module.now_iso()),
    )
    conn.commit()
    conn.close()
    flash("Site added.", "success")
    return redirect(url_for("clients_bp.index", client=client_id))


@clients_bp.route("/sites/<site_id>/delete", methods=["POST"])
@login_required
@office_required
def delete_site(site_id):
    conn = db_module.get_db()
    site = conn.execute("SELECT client_id FROM client_sites WHERE id=?",
                         (site_id,)).fetchone()
    conn.execute("DELETE FROM client_sites WHERE id=?", (site_id,))
    conn.commit()
    conn.close()
    flash("Site removed.", "success")
    return redirect(url_for("clients_bp.index", client=site["client_id"] if site else ""))
