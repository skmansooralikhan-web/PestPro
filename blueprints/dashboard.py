from datetime import date, timedelta

from flask import Blueprint, render_template, session, redirect, url_for

import db as db_module
from auth import login_required

dashboard_bp = Blueprint("dashboard_bp", __name__)


@dashboard_bp.route("/dashboard")
@login_required
def dashboard():
    if session.get("role") == "technician":
        return redirect(url_for("jobcards_bp.tech_home"))

    conn = db_module.get_db()
    today = date.today().isoformat()
    month_start = date.today().replace(day=1).isoformat()

    revenue_month = conn.execute(
        "SELECT COALESCE(SUM(total_amount),0) AS t FROM invoices "
        "WHERE invoice_date >= ? AND is_void = 0", (month_start,)
    ).fetchone()["t"]

    outstanding = conn.execute(
        "SELECT COALESCE(SUM(balance_due),0) AS t FROM invoices "
        "WHERE payment_status IN ('Unpaid','Partial','Overdue') AND is_void = 0"
    ).fetchone()["t"]

    visits_today_total = conn.execute(
        "SELECT COUNT(*) AS c FROM visit_schedules WHERE scheduled_date = ?",
        (today,)
    ).fetchone()["c"]
    visits_today_done = conn.execute(
        "SELECT COUNT(*) AS c FROM visit_schedules WHERE scheduled_date = ? "
        "AND status = 'Completed'", (today,)
    ).fetchone()["c"]

    active_clients = conn.execute(
        "SELECT COUNT(*) AS c FROM clients WHERE status = 'Active'"
    ).fetchone()["c"]

    low_stock_count = conn.execute(
        "SELECT COUNT(*) AS c FROM inventory_items WHERE current_stock < minimum_stock"
    ).fetchone()["c"]

    amc_due_this_month = conn.execute(
        "SELECT COUNT(*) AS c FROM amc_contracts WHERE status = 'Active' "
        f"AND {db_module.ym_expr('end_date')} = {db_module.current_ym_expr()}"
    ).fetchone()["c"]

    todays_schedule = conn.execute(
        "SELECT v.*, c.name AS client_name, c.area AS client_area, c.phone AS client_phone "
        "FROM visit_schedules v JOIN clients c ON c.id = v.client_id "
        "WHERE v.scheduled_date = ? ORDER BY v.time_slot", (today,)
    ).fetchall()

    overdue_invoices = conn.execute(
        "SELECT i.*, c.name AS client_name FROM invoices i "
        "JOIN clients c ON c.id = i.client_id "
        "WHERE i.due_date < ? AND i.payment_status IN ('Unpaid','Partial','Overdue') "
        "AND i.is_void = 0 ORDER BY i.due_date LIMIT 8",
        (today,)
    ).fetchall()

    expiring_amc = conn.execute(
        "SELECT a.*, c.name AS client_name FROM amc_contracts a "
        "JOIN clients c ON c.id = a.client_id "
        "WHERE a.status = 'Active' AND a.end_date BETWEEN ? AND ? "
        "ORDER BY a.end_date LIMIT 8",
        (today, (date.today() + timedelta(days=30)).isoformat())
    ).fetchall()

    low_stock_items = conn.execute(
        "SELECT * FROM inventory_items WHERE current_stock < minimum_stock "
        "ORDER BY (current_stock * 1.0 / NULLIF(minimum_stock,0)) LIMIT 8"
    ).fetchall()

    stale_visits = conn.execute(
        "SELECT v.id, v.scheduled_date, v.status, v.tech_name, c.name AS client_name "
        "FROM visit_schedules v JOIN clients c ON c.id = v.client_id "
        "WHERE v.scheduled_date <= ? AND v.status IN ('Scheduled', 'In Progress') "
        "ORDER BY v.scheduled_date LIMIT 8",
        ((date.today() - timedelta(days=1)).isoformat(),)
    ).fetchall()

    # Last 6 months revenue, for the dashboard chart
    revenue_chart = []
    for i in range(5, -1, -1):
        ref = date.today().replace(day=1) - timedelta(days=1)
        for _ in range(i):
            ref = ref.replace(day=1) - timedelta(days=1)
        ym = ref.strftime("%Y-%m")
        total = conn.execute(
            "SELECT COALESCE(SUM(total_amount),0) AS t FROM invoices "
            f"WHERE {db_module.ym_expr('invoice_date')} = ? AND is_void = 0", (ym,)
        ).fetchone()["t"]
        revenue_chart.append({"month": ref.strftime("%b %Y"), "amount": total})

    recent_activity = conn.execute(
        "SELECT * FROM audit_logs ORDER BY created_at DESC LIMIT 10"
    ).fetchall()

    conn.close()

    return render_template(
        "dashboard.html",
        revenue_month=revenue_month,
        outstanding=outstanding,
        visits_today_total=visits_today_total,
        visits_today_done=visits_today_done,
        active_clients=active_clients,
        low_stock_count=low_stock_count,
        amc_due_this_month=amc_due_this_month,
        todays_schedule=todays_schedule,
        overdue_invoices=overdue_invoices,
        expiring_amc=expiring_amc,
        low_stock_items=low_stock_items,
        stale_visits=stale_visits,
        revenue_chart=revenue_chart,
        recent_activity=recent_activity,
        today=today,
    )
