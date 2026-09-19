from datetime import date, datetime, timedelta

from flask import (Blueprint, render_template, request, redirect, url_for,
                    flash, session, jsonify, abort)

import db as db_module
from auth import login_required, office_required
from utils import parse_json, to_json, create_notification, render_confirm_page

scheduling_bp = Blueprint("scheduling_bp", __name__)

STATUS_COLORS = {
    "Scheduled": "amber", "In Progress": "blue", "Completed": "green",
    "Cancelled": "gray", "Rescheduled": "purple",
}


def _week_bounds(anchor: date):
    start = anchor - timedelta(days=anchor.weekday() + 1 if anchor.weekday() != 6 else 0)
    # Normalize to Sunday-start week
    start = anchor - timedelta(days=(anchor.weekday() + 1) % 7)
    return start, start + timedelta(days=6)


def _techs(conn):
    return [t["username"] for t in conn.execute(
        "SELECT username FROM users WHERE role='technician' AND disabled=0"
    ).fetchall()]


def _parse_time_slot(time_slot):
    """Best-effort parse of a time_slot string (HH:MM, what the visit form
    now produces) into a time object for overlap math. Older free-text
    values (e.g. "10 AM") that don't match just return None, and the
    caller falls back to exact-string-match for those."""
    try:
        return datetime.strptime(time_slot.strip(), "%H:%M").time()
    except (ValueError, AttributeError):
        return None


def _check_conflict(conn, tech_name, scheduled_date, time_slot, duration_minutes=60,
                     exclude_visit_id=None):
    """Returns the other visit this one overlaps with for the same
    technician on the same day, or None. Real start/end overlap math when
    both sides have a parseable HH:MM time_slot; falls back to an exact
    time_slot string match for older free-text values."""
    if not tech_name or not time_slot:
        return None
    sql = ("SELECT v.*, c.name AS client_name FROM visit_schedules v "
           "JOIN clients c ON c.id = v.client_id "
           "WHERE v.tech_name=? AND v.scheduled_date=? AND v.status != 'Cancelled'")
    params = [tech_name, scheduled_date]
    if exclude_visit_id:
        sql += " AND v.id != ?"
        params.append(exclude_visit_id)
    others = conn.execute(sql, params).fetchall()

    new_start = _parse_time_slot(time_slot)
    if new_start is None:
        return next((o for o in others if o["time_slot"] == time_slot), None)

    anchor = date(2000, 1, 1)
    new_start_dt = datetime.combine(anchor, new_start)
    new_end_dt = new_start_dt + timedelta(minutes=duration_minutes or 60)

    for o in others:
        other_start = _parse_time_slot(o["time_slot"])
        if other_start is None:
            if o["time_slot"] == time_slot:
                return o
            continue
        other_start_dt = datetime.combine(anchor, other_start)
        other_end_dt = other_start_dt + timedelta(minutes=o["duration_minutes"] or 60)
        if new_start_dt < other_end_dt and other_start_dt < new_end_dt:
            return o
    return None



def _check_leave(conn, tech_name, scheduled_date):
    """Returns the tech_leave row covering this technician and date, or
    None — used to warn (not hard-block) when scheduling someone who's
    marked as on leave."""
    if not tech_name:
        return None
    return conn.execute(
        "SELECT * FROM tech_leave WHERE tech_name=? AND ? BETWEEN start_date AND end_date",
        (tech_name, scheduled_date)
    ).fetchone()


@scheduling_bp.route("/")
@login_required
def week_view():
    anchor_str = request.args.get("start")
    anchor = datetime.strptime(anchor_str, "%Y-%m-%d").date() if anchor_str else date.today()
    start, end = _week_bounds(anchor)

    conn = db_module.get_db()
    visits = conn.execute(
        "SELECT v.*, c.name AS client_name, c.area AS client_area FROM visit_schedules v "
        "JOIN clients c ON c.id = v.client_id "
        "WHERE v.scheduled_date BETWEEN ? AND ? ORDER BY v.time_slot",
        (start.isoformat(), end.isoformat())
    ).fetchall()

    days = []
    for i in range(7):
        d = start + timedelta(days=i)
        day_visits = [v for v in visits if v["scheduled_date"] == d.isoformat()]
        days.append({"date": d, "visits": day_visits})

    techs = _techs(conn)
    workload = []
    for t in techs:
        total = sum(1 for v in visits if v["tech_name"] == t)
        done = sum(1 for v in visits if v["tech_name"] == t and v["status"] == "Completed")
        on_leave = conn.execute(
            "SELECT * FROM tech_leave WHERE tech_name=? AND start_date <= ? AND end_date >= ?",
            (t, end.isoformat(), start.isoformat())
        ).fetchone()
        workload.append({"tech": t, "total": total, "done": done, "on_leave": on_leave})

    routes = conn.execute("SELECT * FROM routes ORDER BY name").fetchall()
    conn.close()
    return render_template(
        "scheduling/index.html", days=days, week_start=start, week_end=end,
        prev_week=(start - timedelta(days=7)).isoformat(),
        next_week=(start + timedelta(days=7)).isoformat(),
        today=date.today().isoformat(), techs=techs, workload=workload,
        status_colors=STATUS_COLORS, routes=routes,
    )


@scheduling_bp.route("/month")
@login_required
def month_view():
    year = int(request.args.get("year", date.today().year))
    month = int(request.args.get("month", date.today().month))
    first = date(year, month, 1)
    if month == 12:
        next_first = date(year + 1, 1, 1)
    else:
        next_first = date(year, month + 1, 1)
    last = next_first - timedelta(days=1)

    conn = db_module.get_db()
    visits = conn.execute(
        "SELECT scheduled_date, COUNT(*) AS n FROM visit_schedules "
        "WHERE scheduled_date BETWEEN ? AND ? GROUP BY scheduled_date",
        (first.isoformat(), last.isoformat())
    ).fetchall()
    counts = {v["scheduled_date"]: v["n"] for v in visits}
    conn.close()

    lead_blank = (first.weekday() + 1) % 7  # Sunday-start grid
    weeks, week = [], [None] * lead_blank
    d = first
    while d <= last:
        week.append(d)
        if len(week) == 7:
            weeks.append(week)
            week = []
        d += timedelta(days=1)
    if week:
        while len(week) < 7:
            week.append(None)
        weeks.append(week)

    prev_month = (first - timedelta(days=1))
    next_month = next_first

    return render_template(
        "scheduling/month.html", weeks=weeks, counts=counts, first=first,
        month_name=first.strftime("%B %Y"),
        prev_year=prev_month.year, prev_month=prev_month.month,
        next_year=next_month.year, next_month=next_month.month,
        today=date.today().isoformat(),
    )


@scheduling_bp.route("/agenda")
@login_required
def agenda_view():
    start_str = request.args.get("start", date.today().isoformat())
    days_ahead = int(request.args.get("days", 14))
    start = datetime.strptime(start_str, "%Y-%m-%d").date()
    end = start + timedelta(days=days_ahead)

    tech_filter = request.args.get("tech", "")
    status_filter = request.args.get("status", "")

    conn = db_module.get_db()
    sql = ("SELECT v.*, c.name AS client_name, c.area AS client_area FROM visit_schedules v "
           "JOIN clients c ON c.id = v.client_id "
           "WHERE v.scheduled_date BETWEEN ? AND ?")
    params = [start.isoformat(), end.isoformat()]
    if tech_filter:
        sql += " AND v.tech_name = ?"
        params.append(tech_filter)
    if status_filter:
        sql += " AND v.status = ?"
        params.append(status_filter)
    sql += " ORDER BY v.scheduled_date, v.time_slot"
    visits = conn.execute(sql, params).fetchall()

    grouped = {}
    for v in visits:
        grouped.setdefault(v["scheduled_date"], []).append(v)

    techs = _techs(conn)
    conn.close()
    return render_template(
        "scheduling/agenda.html", grouped=grouped, techs=techs,
        tech_filter=tech_filter, status_filter=status_filter,
        start=start_str, days_ahead=days_ahead, status_colors=STATUS_COLORS,
    )


@scheduling_bp.route("/visits/new", methods=["GET", "POST"])
@login_required
@office_required
def new_visit():
    conn = db_module.get_db()
    if request.method == "POST":
        client_id = request.form["client_id"]
        scheduled_date = request.form["scheduled_date"]
        time_slot = request.form.get("time_slot", "")
        duration_minutes = int(request.form.get("duration_minutes") or 60)
        tech_name = request.form.get("tech_name")
        services = request.form.getlist("services")

        client = conn.execute("SELECT * FROM clients WHERE id=?", (client_id,)).fetchone()
        if not client:
            conn.close()
            flash("Client not found.", "error")
            return redirect(url_for("scheduling_bp.week_view"))

        booked = conn.execute(
            "SELECT COUNT(*) AS c FROM visit_schedules WHERE client_id=? "
            "AND status != 'Cancelled' AND is_revisit=0", (client_id,)
        ).fetchone()["c"]
        is_revisit = request.form.get("is_revisit") == "on"
        over_quota = (not is_revisit and client["total_visits"]
                      and booked >= client["total_visits"])

        leave = _check_leave(conn, tech_name, scheduled_date)
        if leave and not request.form.get("confirm_leave"):
            conn.close()
            msg = (f"{tech_name} is marked on leave for {scheduled_date}"
                   f"{' (' + leave['reason'] + ')' if leave['reason'] else ''}.")
            return render_confirm_page(msg, url_for("scheduling_bp.new_visit"), "confirm_leave",
                                  url_for("scheduling_bp.new_visit", client=client_id))

        conflict = _check_conflict(conn, tech_name, scheduled_date, time_slot, duration_minutes)
        if conflict and not request.form.get("confirm_conflict"):
            conn.close()
            msg = (f"Scheduling conflict: {tech_name} already has a visit for "
                   f"{conflict['client_name']} at {time_slot} on {scheduled_date}.")
            return render_confirm_page(msg, url_for("scheduling_bp.new_visit"), "confirm_conflict",
                                  url_for("scheduling_bp.new_visit", client=client_id))

        visit_num = booked + 1
        vid = db_module.new_id()
        ts = db_module.now_iso()
        conn.execute(
            "INSERT INTO visit_schedules (id, client_id, site_id, scheduled_date, "
            "visit_num, services, tech_name, time_slot, duration_minutes, status, "
            "visit_notes, is_revisit, created_at, updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (vid, client_id, request.form.get("site_id") or None, scheduled_date,
             visit_num, to_json(services), tech_name, time_slot, duration_minutes,
             "Scheduled", request.form.get("visit_notes"), 1 if is_revisit else 0, ts, ts),
        )
        conn.commit()
        db_module.log_audit(session["username"], "VISIT_SCHEDULED", "visit_schedules",
                             vid, new_value=f"{client['name']} on {scheduled_date}")
        conn.close()

        # Scheduling is never hard-blocked by the contracted visit count —
        # it's shown as a running "X of Y assigned" count instead, so office
        # staff can keep booking ahead across months without a contract
        # total getting in the way; this is just a heads-up when a booking
        # goes past what was contracted.
        assigned_now = booked + 1 if not is_revisit else booked
        if is_revisit:
            flash(f"Re-visit scheduled for {client['name']} (doesn't count "
                  f"against the visit quota).", "success")
        elif over_quota:
            flash(f"Visit scheduled — {assigned_now} of {client['total_visits']} "
                  f"visits now assigned for {client['name']} (past the contracted "
                  f"count; consider updating it on the client's profile if this "
                  f"client is on an ongoing/rolling schedule).", "success")
        elif client["total_visits"]:
            flash(f"Visit scheduled — {assigned_now} of {client['total_visits']} "
                  f"visits now assigned for {client['name']}.", "success")
        else:
            flash("Visit scheduled.", "success")
        return redirect(url_for("scheduling_bp.week_view", start=scheduled_date))

    client_id = request.args.get("client", "")
    clients = conn.execute(
        "SELECT id, name, area FROM clients WHERE status='Active' ORDER BY name"
    ).fetchall()
    sites = conn.execute(
        "SELECT * FROM client_sites WHERE client_id=?", (client_id,)
    ).fetchall() if client_id else []
    techs = _techs(conn)
    default_date = request.args.get("date", date.today().isoformat())
    services_catalog = parse_json(db_module.get_setting("serviceCatalog"), [])
    conn.close()
    return render_template("scheduling/visit_form.html", visit=None,
                            clients=clients, sites=sites, techs=techs,
                            preselect_client=client_id, default_date=default_date,
                            services_catalog=services_catalog)


@scheduling_bp.route("/visits/<visit_id>")
@login_required
def view_visit(visit_id):
    conn = db_module.get_db()
    visit = conn.execute(
        "SELECT v.*, c.name AS client_name, c.phone AS client_phone, "
        "c.address AS client_address, c.area AS client_area, c.notes AS client_notes "
        "FROM visit_schedules v JOIN clients c ON c.id = v.client_id WHERE v.id=?",
        (visit_id,)
    ).fetchone()
    if not visit:
        conn.close()
        abort(404)
    job_card = conn.execute(
        "SELECT * FROM job_cards WHERE visit_id=?", (visit_id,)
    ).fetchone()
    conn.close()
    return render_template("scheduling/visit_detail.html", visit=visit, job_card=job_card)


@scheduling_bp.route("/visits/<visit_id>/edit", methods=["GET", "POST"])
@login_required
@office_required
def edit_visit(visit_id):
    conn = db_module.get_db()
    visit = conn.execute("SELECT * FROM visit_schedules WHERE id=?", (visit_id,)).fetchone()
    if not visit:
        conn.close()
        abort(404)

    if request.method == "POST":
        scheduled_date = request.form["scheduled_date"]
        time_slot = request.form.get("time_slot", "")
        duration_minutes = int(request.form.get("duration_minutes") or 60)
        tech_name = request.form.get("tech_name")
        services = request.form.getlist("services")

        leave = _check_leave(conn, tech_name, scheduled_date)
        if leave and not request.form.get("confirm_leave"):
            conn.close()
            msg = (f"{tech_name} is marked on leave for {scheduled_date}"
                   f"{' (' + leave['reason'] + ')' if leave['reason'] else ''}.")
            return render_confirm_page(msg, url_for("scheduling_bp.edit_visit", visit_id=visit_id),
                                  "confirm_leave", url_for("scheduling_bp.edit_visit", visit_id=visit_id))

        conflict = _check_conflict(conn, tech_name, scheduled_date, time_slot,
                                    duration_minutes, visit_id)
        if conflict and not request.form.get("confirm_conflict"):
            conn.close()
            msg = f"Scheduling conflict with {conflict['client_name']} at {time_slot}."
            return render_confirm_page(msg, url_for("scheduling_bp.edit_visit", visit_id=visit_id),
                                  "confirm_conflict", url_for("scheduling_bp.edit_visit", visit_id=visit_id))

        status = request.form.get("status", visit["status"])
        was_scheduled_date = visit["scheduled_date"]
        conn.execute(
            "UPDATE visit_schedules SET scheduled_date=?, services=?, tech_name=?, "
            "time_slot=?, duration_minutes=?, status=?, visit_notes=?, updated_at=? WHERE id=?",
            (scheduled_date, to_json(services), tech_name, time_slot, duration_minutes,
             status, request.form.get("visit_notes"), db_module.now_iso(), visit_id),
        )
        conn.commit()
        action = "VISIT_RESCHEDULED" if scheduled_date != was_scheduled_date else "VISIT_UPDATED"
        db_module.log_audit(session["username"], action, "visit_schedules", visit_id)
        conn.close()
        flash("Visit updated.", "success")
        return redirect(url_for("scheduling_bp.week_view", start=scheduled_date))

    client = conn.execute("SELECT * FROM clients WHERE id=?", (visit["client_id"],)).fetchone()
    clients = conn.execute("SELECT id, name, area FROM clients WHERE status='Active' ORDER BY name").fetchall()
    sites = conn.execute("SELECT * FROM client_sites WHERE client_id=?", (visit["client_id"],)).fetchall()
    techs = _techs(conn)
    services_catalog = parse_json(db_module.get_setting("serviceCatalog"), [])
    conn.close()
    return render_template("scheduling/visit_form.html", visit=visit, client=client,
                            clients=clients, sites=sites, techs=techs,
                            preselect_client=visit["client_id"],
                            default_date=visit["scheduled_date"],
                            services_catalog=services_catalog,
                            visit_services=parse_json(visit["services"]))


@scheduling_bp.route("/visits/<visit_id>/cancel", methods=["POST"])
@login_required
@office_required
def cancel_visit(visit_id):
    conn = db_module.get_db()
    conn.execute("UPDATE visit_schedules SET status='Cancelled', updated_at=? WHERE id=?",
                 (db_module.now_iso(), visit_id))
    conn.commit()
    conn.close()
    db_module.log_audit(session["username"], "VISIT_CANCELLED", "visit_schedules", visit_id)
    flash("Visit cancelled.", "success")
    return redirect(request.referrer or url_for("scheduling_bp.week_view"))


@scheduling_bp.route("/visits/<visit_id>/quick-move", methods=["POST"])
@login_required
@office_required
def quick_move(visit_id):
    """AJAX endpoint for drag-and-drop rescheduling on the week grid."""
    data = request.get_json(force=True)
    new_date = data.get("scheduled_date")
    conn = db_module.get_db()
    visit = conn.execute("SELECT * FROM visit_schedules WHERE id=?", (visit_id,)).fetchone()
    if not visit:
        conn.close()
        return jsonify({"ok": False, "error": "Visit not found"}), 404

    conflict = _check_conflict(conn, visit["tech_name"], new_date, visit["time_slot"],
                                visit["duration_minutes"], visit_id)
    if conflict:
        conn.close()
        return jsonify({"ok": False, "error": f"{visit['tech_name']} already has a "
                         f"visit at that time on {new_date}."}), 409

    conn.execute(
        "UPDATE visit_schedules SET scheduled_date=?, status='Rescheduled', "
        "updated_at=? WHERE id=?", (new_date, db_module.now_iso(), visit_id)
    )
    conn.commit()
    conn.close()
    db_module.log_audit(session["username"], "VISIT_DRAG_RESCHEDULED",
                         "visit_schedules", visit_id, new_value=new_date)
    return jsonify({"ok": True})


# ---- Routes (grouped clients for field ops) ---------------------------------

@scheduling_bp.route("/routes/new", methods=["GET", "POST"])
@login_required
@office_required
def new_route():
    conn = db_module.get_db()
    if request.method == "POST":
        client_ids = request.form.getlist("client_ids")
        rid = db_module.new_id()
        ts = db_module.now_iso()
        conn.execute(
            "INSERT INTO routes (id, name, area, assigned_tech, client_ids, notes, "
            "created_at, updated_at) VALUES (?,?,?,?,?,?,?,?)",
            (rid, request.form["name"], request.form.get("area"),
             request.form.get("assigned_tech"), to_json(client_ids),
             request.form.get("notes"), ts, ts),
        )
        conn.commit()
        conn.close()
        flash("Route created.", "success")
        return redirect(url_for("scheduling_bp.week_view"))

    clients = conn.execute("SELECT id, name, area FROM clients WHERE status='Active' ORDER BY area, name").fetchall()
    techs = _techs(conn)
    conn.close()
    return render_template("scheduling/route_form.html", clients=clients, techs=techs)


@scheduling_bp.route("/routes/<route_id>/schedule-all", methods=["POST"])
@login_required
@office_required
def schedule_route(route_id):
    """Schedule every client on a route for the given date in one click."""
    conn = db_module.get_db()
    route = conn.execute("SELECT * FROM routes WHERE id=?", (route_id,)).fetchone()
    if not route:
        conn.close()
        abort(404)
    scheduled_date = request.form["scheduled_date"]
    created = 0
    for cid in parse_json(route["client_ids"]):
        client = conn.execute("SELECT * FROM clients WHERE id=?", (cid,)).fetchone()
        if not client:
            continue
        booked = conn.execute(
            "SELECT COUNT(*) AS c FROM visit_schedules WHERE client_id=? "
            "AND status != 'Cancelled' AND is_revisit=0", (cid,)
        ).fetchone()["c"]
        if client["total_visits"] and booked >= client["total_visits"]:
            continue
        vid = db_module.new_id()
        ts = db_module.now_iso()
        conn.execute(
            "INSERT INTO visit_schedules (id, client_id, scheduled_date, visit_num, "
            "services, tech_name, time_slot, status, created_at, updated_at) "
            "VALUES (?,?,?,?,?,?,?,?,?,?)",
            (vid, cid, scheduled_date, booked + 1, client["services"],
             route["assigned_tech"], "", "Scheduled", ts, ts),
        )
        created += 1
    conn.commit()
    conn.close()
    flash(f"Scheduled {created} visits for route \"{route['name']}\".", "success")
    return redirect(url_for("scheduling_bp.week_view", start=scheduled_date))


@scheduling_bp.route("/tech-leave")
@login_required
@office_required
def tech_leave_index():
    conn = db_module.get_db()
    leave_entries = conn.execute(
        "SELECT * FROM tech_leave WHERE end_date >= ? ORDER BY start_date",
        (date.today().isoformat(),)
    ).fetchall()
    past_entries = conn.execute(
        "SELECT * FROM tech_leave WHERE end_date < ? ORDER BY start_date DESC LIMIT 20",
        (date.today().isoformat(),)
    ).fetchall()
    techs = _techs(conn)
    conn.close()
    return render_template("scheduling/tech_leave.html", leave_entries=leave_entries,
                            past_entries=past_entries, techs=techs)


@scheduling_bp.route("/tech-leave/new", methods=["POST"])
@login_required
@office_required
def new_tech_leave():
    tech_name = request.form.get("tech_name")
    start_date = request.form.get("start_date")
    end_date = request.form.get("end_date")
    if not tech_name or not start_date or not end_date:
        flash("Technician, start date, and end date are all required.", "error")
        return redirect(url_for("scheduling_bp.tech_leave_index"))
    if end_date < start_date:
        flash("End date can't be before the start date.", "error")
        return redirect(url_for("scheduling_bp.tech_leave_index"))

    conn = db_module.get_db()
    lid = db_module.new_id()
    conn.execute(
        "INSERT INTO tech_leave (id, tech_name, start_date, end_date, reason, "
        "created_by, created_at) VALUES (?,?,?,?,?,?,?)",
        (lid, tech_name, start_date, end_date, request.form.get("reason"),
         session["username"], db_module.now_iso()),
    )
    conn.commit()
    db_module.log_audit(session["username"], "TECH_LEAVE_ADDED", "tech_leave", lid,
                         new_value=f"{tech_name}: {start_date} to {end_date}")
    conn.close()
    flash(f"Leave recorded for {tech_name}.", "success")
    return redirect(url_for("scheduling_bp.tech_leave_index"))


@scheduling_bp.route("/tech-leave/<leave_id>/delete", methods=["POST"])
@login_required
@office_required
def delete_tech_leave(leave_id):
    conn = db_module.get_db()
    entry = conn.execute("SELECT * FROM tech_leave WHERE id=?", (leave_id,)).fetchone()
    if entry:
        conn.execute("DELETE FROM tech_leave WHERE id=?", (leave_id,))
        conn.commit()
        db_module.log_audit(session["username"], "TECH_LEAVE_REMOVED", "tech_leave",
                             leave_id, old_value=f"{entry['tech_name']}: {entry['start_date']} to {entry['end_date']}")
    conn.close()
    flash("Leave entry removed.", "success")
    return redirect(url_for("scheduling_bp.tech_leave_index"))


@scheduling_bp.route("/routes")
@login_required
@office_required
def routes_index():
    conn = db_module.get_db()
    routes = conn.execute("SELECT * FROM routes ORDER BY name").fetchall()
    conn.close()
    return render_template("scheduling/routes_index.html", routes=routes)


@scheduling_bp.route("/routes/<route_id>")
@login_required
@office_required
def route_detail(route_id):
    conn = db_module.get_db()
    route = conn.execute("SELECT * FROM routes WHERE id=?", (route_id,)).fetchone()
    if not route:
        conn.close()
        abort(404)
    client_ids = parse_json(route["client_ids"])
    clients = []
    for cid in client_ids:
        c = conn.execute("SELECT id, name, area, address, latitude, longitude "
                          "FROM clients WHERE id=?", (cid,)).fetchone()
        if c:
            clients.append(c)
    conn.close()
    missing_coords = [c for c in clients if c["latitude"] is None or c["longitude"] is None]
    return render_template("scheduling/route_detail.html", route=route, clients=clients,
                            missing_coords=missing_coords)


@scheduling_bp.route("/routes/<route_id>/optimize", methods=["POST"])
@login_required
@office_required
def optimize_route_view(route_id):
    from utils import optimize_route

    conn = db_module.get_db()
    route = conn.execute("SELECT * FROM routes WHERE id=?", (route_id,)).fetchone()
    if not route:
        conn.close()
        abort(404)
    client_ids = parse_json(route["client_ids"])
    stops = []
    for cid in client_ids:
        c = conn.execute("SELECT id, name, latitude, longitude FROM clients WHERE id=?",
                          (cid,)).fetchone()
        if c:
            stops.append({"id": c["id"], "name": c["name"], "lat": c["latitude"],
                          "lng": c["longitude"]})

    result = optimize_route(stops)
    new_order = [s["id"] for s in result["order"]]
    conn.execute("UPDATE routes SET client_ids=?, updated_at=? WHERE id=?",
                 (to_json(new_order), db_module.now_iso(), route_id))
    conn.commit()
    conn.close()

    if result["skipped"]:
        names = ", ".join(s["name"] for s in result["skipped"])
        flash(f"Route reordered — estimated {result['total_km']} km between the "
              f"{len(result['order']) - len(result['skipped'])} geocoded stops. "
              f"{names} couldn't be optimized (no coordinates yet) and stayed at the end.",
              "success")
    else:
        flash(f"Route reordered for shortest estimated travel — about "
              f"{result['total_km']} km total.", "success")
    return redirect(url_for("scheduling_bp.route_detail", route_id=route_id))
