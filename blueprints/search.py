"""
PCT CRM — global search across clients, invoices, AMC contracts, and leads.

Visit-level search ("that visit for the Kondapur client") is deliberately
handled by finding the *client* here and following through to their own
visit history on their detail page, rather than indexing every visit
separately — visits don't have a name of their own to search by, and a
client is almost always the more useful unit to land on first anyway.
"""
from flask import Blueprint, render_template, request, jsonify, session

import db as db_module
from auth import login_required

search_bp = Blueprint("search_bp", __name__)


def run_global_search(conn, q, limit=5, clients_only=False):
    """Runs the same search across all four entity types with a shared
    per-category limit, used by both the quick (header dropdown) and full
    results page so the two never drift apart. clients_only restricts to
    just the client match — used for technicians, who can't view invoices,
    AMC contracts, or leads directly, so search must not expose them
    either."""
    like = f"%{q}%"
    clients = conn.execute(
        f"SELECT id, name, phone, area, status FROM clients "
        f"WHERE name LIKE ? OR phone LIKE ? ORDER BY name LIMIT {int(limit)}",
        (like, like)
    ).fetchall()
    if clients_only:
        return {"clients": clients, "invoices": [], "amc": [], "leads": []}
    invoices = conn.execute(
        f"SELECT i.id, i.inv_number, c.name AS client_name, i.total_amount, i.payment_status "
        f"FROM invoices i JOIN clients c ON c.id = i.client_id "
        f"WHERE (i.inv_number LIKE ? OR c.name LIKE ?) AND i.is_void = 0 "
        f"ORDER BY i.created_at DESC LIMIT {int(limit)}",
        (like, like)
    ).fetchall()
    amc = conn.execute(
        f"SELECT a.id, a.contract_number, c.name AS client_name, a.status "
        f"FROM amc_contracts a JOIN clients c ON c.id = a.client_id "
        f"WHERE a.contract_number LIKE ? OR c.name LIKE ? "
        f"ORDER BY a.created_at DESC LIMIT {int(limit)}",
        (like, like)
    ).fetchall()
    leads = conn.execute(
        f"SELECT id, name, phone, stage FROM leads "
        f"WHERE name LIKE ? OR phone LIKE ? ORDER BY created_at DESC LIMIT {int(limit)}",
        (like, like)
    ).fetchall()
    return {"clients": clients, "invoices": invoices, "amc": amc, "leads": leads}


@search_bp.route("/")
@login_required
def index():
    q = request.args.get("q", "").strip()
    results = {"clients": [], "invoices": [], "amc": [], "leads": []}
    if len(q) >= 2:
        conn = db_module.get_db()
        # Technicians can't view invoices, AMC contracts, or leads directly
        # (those routes are office_required) — global search must not
        # become a back door to the same data those role checks protect.
        is_technician = session.get("role") == "technician"
        results = run_global_search(conn, q, limit=50, clients_only=is_technician)
        conn.close()
    total = sum(len(v) for v in results.values())
    return render_template("search/index.html", q=q, results=results, total=total)


@search_bp.route("/quick")
@login_required
def quick():
    q = request.args.get("q", "").strip()
    if len(q) < 2:
        return jsonify({"clients": [], "invoices": [], "amc": [], "leads": []})
    conn = db_module.get_db()
    is_technician = session.get("role") == "technician"
    results = run_global_search(conn, q, limit=5, clients_only=is_technician)
    conn.close()
    return jsonify({k: [dict(r) for r in v] for k, v in results.items()})
