import csv
import io
from datetime import date, timedelta

from flask import Blueprint, render_template, request, session, send_file

import db as db_module
from auth import login_required, office_required
from utils import parse_json

reports_bp = Blueprint("reports_bp", __name__)


def _date_range():
    end = request.args.get("end", date.today().isoformat())
    start = request.args.get("start", (date.today() - timedelta(days=180)).isoformat())
    return start, end


@reports_bp.route("/")
@login_required
@office_required
def index():
    tab = request.args.get("tab", "revenue")
    start, end = _date_range()
    conn = db_module.get_db()

    ctx = {"tab": tab, "start": start, "end": end}

    if tab == "revenue":
        monthly = conn.execute(
            f"SELECT {db_module.ym_expr('invoice_date')} AS ym, "
            "COALESCE(SUM(total_amount),0) AS billed, "
            "COALESCE(SUM(amount_paid),0) AS collected "
            "FROM invoices WHERE invoice_date BETWEEN ? AND ? AND is_void=0 "
            "GROUP BY ym ORDER BY ym", (start, end)
        ).fetchall()
        top_clients = conn.execute(
            "SELECT c.name, COALESCE(SUM(i.total_amount),0) AS total FROM invoices i "
            "JOIN clients c ON c.id = i.client_id "
            "WHERE i.invoice_date BETWEEN ? AND ? AND i.is_void=0 "
            "GROUP BY c.id ORDER BY total DESC LIMIT 10", (start, end)
        ).fetchall()
        by_service = conn.execute(
            "SELECT li.service, COALESCE(SUM(li.amount),0) AS total FROM invoice_line_items li "
            "JOIN invoices i ON i.id = li.invoice_id "
            "WHERE i.invoice_date BETWEEN ? AND ? AND i.is_void=0 "
            "GROUP BY li.service ORDER BY total DESC", (start, end)
        ).fetchall()
        ctx.update(monthly=monthly, top_clients=top_clients, by_service=by_service)

    elif tab == "visits":
        by_status = conn.execute(
            "SELECT status, COUNT(*) AS n FROM visit_schedules "
            "WHERE scheduled_date BETWEEN ? AND ? GROUP BY status", (start, end)
        ).fetchall()
        completion = conn.execute(
            "SELECT tech_name, "
            "SUM(CASE WHEN status='Completed' THEN 1 ELSE 0 END) AS completed, "
            "COUNT(*) AS total FROM visit_schedules "
            "WHERE scheduled_date BETWEEN ? AND ? AND tech_name IS NOT NULL "
            "GROUP BY tech_name", (start, end)
        ).fetchall()
        by_service = conn.execute(
            "SELECT services FROM visit_schedules WHERE scheduled_date BETWEEN ? AND ?",
            (start, end)
        ).fetchall()
        svc_counts = {}
        for r in by_service:
            for s in parse_json(r["services"]):
                svc_counts[s] = svc_counts.get(s, 0) + 1
        ctx.update(by_status=by_status, completion=completion,
                   svc_counts=sorted(svc_counts.items(), key=lambda x: -x[1]))

    elif tab == "technicians":
        techs = conn.execute(
            "SELECT username, full_name FROM users WHERE role='technician'"
        ).fetchall()
        rows = []
        for t in techs:
            completed = conn.execute(
                "SELECT COUNT(*) AS c FROM visit_schedules WHERE tech_name=? "
                "AND status='Completed' AND scheduled_date BETWEEN ? AND ?",
                (t["username"], start, end)
            ).fetchone()["c"]
            avg_rating = conn.execute(
                "SELECT AVG(rating) AS r FROM feedback WHERE tech_name=?",
                (t["username"],)
            ).fetchone()["r"]

            # Attributed by the visit's ASSIGNED technician (v.tech_name),
            # not whoever happened to submit the job card (j.tech_name) —
            # those normally match, but can differ (e.g. office staff
            # entering a job card on a technician's behalf), and a
            # performance report should reflect who the work was assigned
            # to, consistently with how complaints are attributed below.
            # Outcome comes from the job card's own visit_outcome, not
            # visit_schedules.status — that column reads 'Completed' the
            # moment any job card is filed, even an Incomplete-outcome one,
            # so it can't distinguish a genuinely finished job from a
            # no-access/refused one on its own.
            outcome_rows = conn.execute(
                "SELECT j.visit_outcome, COUNT(*) AS c FROM job_cards j "
                "JOIN visit_schedules v ON v.id = j.visit_id "
                "WHERE v.tech_name=? AND v.scheduled_date BETWEEN ? AND ? "
                "GROUP BY j.visit_outcome", (t["username"], start, end)
            ).fetchall()
            outcome_map = {(r["visit_outcome"] or "Completed"): r["c"] for r in outcome_rows}
            total_jobs = sum(outcome_map.values())
            done_count = outcome_map.get("Completed", 0)
            incomplete_count = total_jobs - done_count
            completion_rate = round(100 * done_count / total_jobs) if total_jobs else None

            complaint_count = conn.execute(
                "SELECT COUNT(*) AS c FROM complaints cp "
                "JOIN visit_schedules v ON v.id = cp.visit_id "
                "WHERE v.tech_name=? AND cp.reported_date BETWEEN ? AND ?",
                (t["username"], start, end)
            ).fetchone()["c"]

            followup_count = conn.execute(
                "SELECT COUNT(*) AS c FROM job_cards j "
                "JOIN visit_schedules v ON v.id = j.visit_id "
                "WHERE v.tech_name=? AND j.followup_required=1 "
                "AND v.scheduled_date BETWEEN ? AND ?",
                (t["username"], start, end)
            ).fetchone()["c"]

            rows.append({
                "tech": t["full_name"] or t["username"], "username": t["username"],
                "completed": completed, "avg_rating": round(avg_rating, 1) if avg_rating else None,
                "total_jobs": total_jobs, "incomplete_count": incomplete_count,
                "completion_rate": completion_rate, "complaint_count": complaint_count,
                "followup_count": followup_count,
            })
        ctx["tech_rows"] = rows

    elif tab == "profitability":
        # Chemical cost uses each item's *current* unit_cost, not what it
        # cost at the time it was actually used — unit_cost isn't tracked
        # historically on the transaction itself, so this is an approximation
        # that drifts if prices change significantly over the period.
        # subtotal, not total_amount — GST is collected on the government's
        # behalf and passed through, not revenue the business actually
        # keeps, so including it would overstate both revenue and profit.
        client_rows = conn.execute(
            "SELECT c.id, c.name, COALESCE(SUM(i.subtotal),0) AS revenue "
            "FROM clients c JOIN invoices i ON i.client_id = c.id "
            "WHERE i.invoice_date BETWEEN ? AND ? AND i.is_void=0 "
            "GROUP BY c.id ORDER BY revenue DESC LIMIT 30",
            (start, end)
        ).fetchall()
        profitability = []
        for r in client_rows:
            cost = conn.execute(
                "SELECT COALESCE(SUM(it.quantity * ii.unit_cost),0) AS cost "
                "FROM inventory_transactions it "
                "JOIN inventory_items ii ON ii.id = it.item_id "
                "JOIN job_cards j ON j.id = it.job_card_id "
                "JOIN visit_schedules v ON v.id = j.visit_id "
                "WHERE v.client_id=? AND it.txn_type='OUT' "
                "AND it.created_at BETWEEN ? AND ? AND ii.unit_cost IS NOT NULL",
                (r["id"], start, end + "T23:59:59")
            ).fetchone()["cost"]
            revenue = r["revenue"]
            profit = revenue - cost
            margin = round(100 * profit / revenue) if revenue else None
            profitability.append({"client": r["name"], "revenue": revenue, "cost": cost,
                                   "profit": profit, "margin": margin})
        has_any_cost_data = any(p["cost"] > 0 for p in profitability)
        ctx.update(profitability=profitability, has_any_cost_data=has_any_cost_data)

    elif tab == "amc":
        active = conn.execute(
            "SELECT COUNT(*) AS c, COALESCE(SUM(amount),0) AS total "
            "FROM amc_contracts WHERE status='Active'"
        ).fetchone()
        expiring = conn.execute(
            "SELECT a.*, c.name AS client_name FROM amc_contracts a "
            "JOIN clients c ON c.id=a.client_id WHERE a.status='Active' "
            "AND a.end_date BETWEEN ? AND ? ORDER BY a.end_date",
            (date.today().isoformat(), (date.today() + timedelta(days=90)).isoformat())
        ).fetchall()
        ctx.update(active=active, expiring=expiring)

    elif tab == "inventory":
        items = conn.execute("SELECT * FROM inventory_items ORDER BY name").fetchall()
        consumption = conn.execute(
            "SELECT i.name, COALESCE(SUM(t.quantity),0) AS used FROM inventory_transactions t "
            "JOIN inventory_items i ON i.id = t.item_id "
            "WHERE t.txn_type='OUT' AND t.created_at >= ? "
            "GROUP BY i.id ORDER BY used DESC LIMIT 5", (start,)
        ).fetchall()
        ctx.update(items=items, consumption=consumption)

    elif tab == "leads":
        by_stage = conn.execute(
            "SELECT stage, COUNT(*) AS n FROM leads "
            "WHERE created_at >= ? GROUP BY stage", (start,)
        ).fetchall()
        by_source = conn.execute(
            "SELECT source, COUNT(*) AS n, "
            "SUM(CASE WHEN stage='Won' THEN 1 ELSE 0 END) AS won "
            "FROM leads WHERE created_at >= ? GROUP BY source", (start,)
        ).fetchall()
        totals = conn.execute(
            "SELECT COUNT(*) AS total, "
            "SUM(CASE WHEN stage='Won' THEN 1 ELSE 0 END) AS won, "
            "SUM(CASE WHEN stage='Lost' THEN 1 ELSE 0 END) AS lost "
            "FROM leads WHERE created_at >= ?", (start,)
        ).fetchone()
        lost_reasons = conn.execute(
            "SELECT lost_reason, COUNT(*) AS n FROM leads WHERE stage='Lost' "
            "AND lost_reason IS NOT NULL AND created_at >= ? "
            "GROUP BY lost_reason ORDER BY n DESC LIMIT 8", (start,)
        ).fetchall()
        pipeline_value = conn.execute(
            "SELECT COALESCE(SUM(estimated_value),0) AS v FROM leads "
            "WHERE stage NOT IN ('Won','Lost')"
        ).fetchone()["v"]
        ctx.update(by_stage=by_stage, by_source=by_source, totals=totals,
                   lost_reasons=lost_reasons, pipeline_value=pipeline_value)

    elif tab == "outstanding":
        today = date.today()
        buckets = {"0-30": 0, "31-60": 0, "61-90": 0, ">90": 0}
        rows = conn.execute(
            "SELECT i.*, c.name AS client_name FROM invoices i "
            "JOIN clients c ON c.id=i.client_id "
            "WHERE i.balance_due > 0 AND i.is_void=0 ORDER BY i.due_date"
        ).fetchall()
        by_client = {}
        for r in rows:
            try:
                due = date.fromisoformat(r["due_date"])
                days = (today - due).days
            except (ValueError, TypeError):
                days = 0
            if days <= 30:
                buckets["0-30"] += r["balance_due"]
            elif days <= 60:
                buckets["31-60"] += r["balance_due"]
            elif days <= 90:
                buckets["61-90"] += r["balance_due"]
            else:
                buckets[">90"] += r["balance_due"]
            by_client[r["client_name"]] = by_client.get(r["client_name"], 0) + r["balance_due"]
        total_outstanding = sum(buckets.values())
        ctx.update(buckets=buckets, invoices=rows, by_client=sorted(
            by_client.items(), key=lambda x: -x[1]), total_outstanding=total_outstanding)

    conn.close()
    return render_template("reports/index.html", **ctx)


@reports_bp.route("/export/<tab>.csv")
@login_required
@office_required
def export_csv(tab):
    start, end = _date_range()
    conn = db_module.get_db()
    buf = io.StringIO()
    writer = csv.writer(buf)

    if tab == "revenue":
        writer.writerow(["Invoice #", "Date", "Client", "Subtotal", "CGST", "SGST",
                          "Total", "Paid", "Balance", "Status"])
        rows = conn.execute(
            "SELECT i.*, c.name AS client_name FROM invoices i "
            "JOIN clients c ON c.id=i.client_id "
            "WHERE i.invoice_date BETWEEN ? AND ? AND i.is_void=0 "
            "ORDER BY i.invoice_date", (start, end)
        ).fetchall()
        for r in rows:
            writer.writerow([r["inv_number"], r["invoice_date"], r["client_name"],
                              r["subtotal"], r["cgst_amount"], r["sgst_amount"],
                              r["total_amount"], r["amount_paid"], r["balance_due"],
                              r["payment_status"]])
    elif tab == "visits":
        writer.writerow(["Date", "Client", "Technician", "Services", "Status"])
        rows = conn.execute(
            "SELECT v.*, c.name AS client_name FROM visit_schedules v "
            "JOIN clients c ON c.id=v.client_id "
            "WHERE v.scheduled_date BETWEEN ? AND ? ORDER BY v.scheduled_date",
            (start, end)
        ).fetchall()
        for r in rows:
            writer.writerow([r["scheduled_date"], r["client_name"], r["tech_name"],
                              ", ".join(parse_json(r["services"])), r["status"]])
    elif tab == "outstanding":
        writer.writerow(["Invoice #", "Client", "Due Date", "Balance Due", "Status"])
        rows = conn.execute(
            "SELECT i.*, c.name AS client_name FROM invoices i "
            "JOIN clients c ON c.id=i.client_id WHERE i.balance_due > 0 AND i.is_void=0"
        ).fetchall()
        for r in rows:
            writer.writerow([r["inv_number"], r["client_name"], r["due_date"],
                              r["balance_due"], r["payment_status"]])

    conn.close()
    mem = io.BytesIO(buf.getvalue().encode("utf-8"))
    return send_file(mem, mimetype="text/csv", as_attachment=True,
                      download_name=f"pct_{tab}_report.csv")


@reports_bp.route("/export/<tab>.xlsx")
@login_required
@office_required
def export_xlsx(tab):
    from openpyxl import Workbook
    start, end = _date_range()
    conn = db_module.get_db()
    wb = Workbook()
    ws = wb.active
    ws.title = tab.capitalize()

    if tab == "revenue":
        ws.append(["Invoice #", "Date", "Client", "Subtotal", "CGST", "SGST",
                    "Total", "Paid", "Balance", "Status"])
        rows = conn.execute(
            "SELECT i.*, c.name AS client_name FROM invoices i "
            "JOIN clients c ON c.id=i.client_id "
            "WHERE i.invoice_date BETWEEN ? AND ? AND i.is_void=0 "
            "ORDER BY i.invoice_date", (start, end)
        ).fetchall()
        for r in rows:
            ws.append([r["inv_number"], r["invoice_date"], r["client_name"],
                       r["subtotal"], r["cgst_amount"], r["sgst_amount"],
                       r["total_amount"], r["amount_paid"], r["balance_due"],
                       r["payment_status"]])
    else:
        ws.append(["Report", tab])

    conn.close()
    mem = io.BytesIO()
    wb.save(mem)
    mem.seek(0)
    return send_file(
        mem,
        mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        as_attachment=True, download_name=f"pct_{tab}_report.xlsx",
    )
