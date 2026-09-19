from flask import Blueprint, jsonify, request

import db as db_module
from auth import login_required
from utils import parse_json

api_bp = Blueprint("api_bp", __name__)


@api_bp.route("/clients/search")
@login_required
def client_search():
    q = request.args.get("q", "").strip()
    conn = db_module.get_db()
    if q:
        rows = conn.execute(
            "SELECT id, name, area, phone, total_visits, assigned_tech FROM clients "
            "WHERE status='Active' AND (name LIKE ? OR phone LIKE ?) ORDER BY name LIMIT 15",
            (f"%{q}%", f"%{q}%")
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT id, name, area, phone, total_visits, assigned_tech FROM clients "
            "WHERE status='Active' ORDER BY name LIMIT 15"
        ).fetchall()
    conn.close()
    return jsonify([dict(r) for r in rows])


@api_bp.route("/clients/<client_id>/sites")
@login_required
def client_sites(client_id):
    conn = db_module.get_db()
    rows = conn.execute(
        "SELECT id, site_name, address, area FROM client_sites WHERE client_id=?",
        (client_id,)
    ).fetchall()
    conn.close()
    return jsonify([dict(r) for r in rows])


@api_bp.route("/clients/<client_id>/geocode", methods=["POST"])
@login_required
def geocode_client(client_id):
    from utils import geocode_address

    conn = db_module.get_db()
    client = conn.execute("SELECT * FROM clients WHERE id=?", (client_id,)).fetchone()
    if not client:
        conn.close()
        return jsonify({"ok": False, "error": "Client not found"}), 404

    api_key = db_module.get_setting("mapsApiKey", "")
    if not api_key:
        conn.close()
        return jsonify({"ok": False, "error": "No Maps API key configured under "
                         "Settings — enter coordinates manually instead."}), 400

    address_parts = [p for p in (client["address"], client["area"], "Hyderabad, India") if p]
    result = geocode_address(", ".join(address_parts), api_key)
    if not result:
        conn.close()
        return jsonify({"ok": False, "error": "Couldn't resolve that address — "
                         "try entering coordinates manually."}), 400

    lat, lng = result
    conn.execute("UPDATE clients SET latitude=?, longitude=?, updated_at=? WHERE id=?",
                 (lat, lng, db_module.now_iso(), client_id))
    conn.commit()
    conn.close()
    return jsonify({"ok": True, "lat": lat, "lng": lng})


@api_bp.route("/inventory/item/<item_id>")
@login_required
def inventory_item(item_id):
    conn = db_module.get_db()
    row = conn.execute(
        "SELECT id, name, unit, current_stock, expiry_date FROM inventory_items WHERE id=?",
        (item_id,)
    ).fetchone()
    conn.close()
    if not row:
        return jsonify({"error": "not found"}), 404
    return jsonify(dict(row))
