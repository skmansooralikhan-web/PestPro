from datetime import date, timedelta

from flask import (Blueprint, render_template, request, redirect, url_for,
                    flash, session, abort, send_file)

import db as db_module
from auth import login_required, office_required
from config import MSDS_DIR, Config

inventory_bp = Blueprint("inventory_bp", __name__)


def _save_msds(item_id: str, file_storage) -> str | None:
    """Validate and save an uploaded MSDS PDF, returning the stored filename."""
    if not file_storage or not file_storage.filename:
        return None
    ext = file_storage.filename.rsplit(".", 1)[-1].lower() if "." in file_storage.filename else ""
    if ext != "pdf":
        return None
    file_storage.stream.seek(0, 2)
    size = file_storage.stream.tell()
    file_storage.stream.seek(0)
    if size > Config.MAX_FILE_SIZE:
        return None
    filename = f"{item_id}.pdf"
    file_storage.save(MSDS_DIR / filename)
    return filename


@inventory_bp.route("/")
@login_required
@office_required
def index():
    conn = db_module.get_db()
    category = request.args.get("category", "")
    alert = request.args.get("alert", "")
    sql = "SELECT * FROM inventory_items WHERE 1=1"
    params = []
    if category:
        sql += " AND category = ?"
        params.append(category)
    today = date.today().isoformat()
    soon = (date.today() + timedelta(days=30)).isoformat()
    if alert == "low":
        sql += " AND current_stock < minimum_stock"
    elif alert == "expiring":
        sql += " AND expiry_date IS NOT NULL AND expiry_date BETWEEN ? AND ?"
        params += [today, soon]
    elif alert == "expired":
        sql += " AND expiry_date IS NOT NULL AND expiry_date < ?"
        params.append(today)
    sql += " ORDER BY name"
    items = conn.execute(sql, params).fetchall()

    low_count = conn.execute(
        "SELECT COUNT(*) AS c FROM inventory_items WHERE current_stock < minimum_stock"
    ).fetchone()["c"]
    expiring_count = conn.execute(
        "SELECT COUNT(*) AS c FROM inventory_items WHERE expiry_date IS NOT NULL "
        "AND expiry_date BETWEEN ? AND ?", (today, soon)
    ).fetchone()["c"]
    expired_count = conn.execute(
        "SELECT COUNT(*) AS c FROM inventory_items WHERE expiry_date IS NOT NULL "
        "AND expiry_date < ?", (today,)
    ).fetchone()["c"]
    conn.close()
    return render_template(
        "inventory/index.html", items=items, category=category, alert=alert,
        categories=Config.DEFAULT_INVENTORY_CATEGORIES, low_count=low_count,
        expiring_count=expiring_count, expired_count=expired_count, today=today,
    )


@inventory_bp.route("/new", methods=["GET", "POST"])
@login_required
@office_required
def new():
    if request.method == "POST":
        conn = db_module.get_db()
        iid = db_module.new_id()
        ts = db_module.now_iso()
        conn.execute(
            "INSERT INTO inventory_items (id, name, category, unit, current_stock, "
            "minimum_stock, reorder_qty, unit_cost, supplier, batch_number, "
            "expiry_date, hsn_code, notes, created_at, updated_at) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (iid, request.form["name"], request.form.get("category"),
             request.form.get("unit", "Litres"),
             float(request.form.get("current_stock") or 0),
             float(request.form.get("minimum_stock") or 5),
             float(request.form.get("reorder_qty") or 10),
             float(request.form.get("unit_cost") or 0) or None,
             request.form.get("supplier"), request.form.get("batch_number"),
             request.form.get("expiry_date") or None, request.form.get("hsn_code"),
             request.form.get("notes"), ts, ts),
        )
        if float(request.form.get("current_stock") or 0) > 0:
            conn.execute(
                "INSERT INTO inventory_transactions (id, item_id, txn_type, quantity, "
                "balance, reference, done_by, created_at) VALUES (?,?,?,?,?,?,?,?)",
                (db_module.new_id(), iid, "IN", float(request.form["current_stock"]),
                 float(request.form["current_stock"]), "Initial stock",
                 session["username"], ts),
            )
        msds_filename = _save_msds(iid, request.files.get("msds_file"))
        if msds_filename:
            conn.execute("UPDATE inventory_items SET msds_file=? WHERE id=?",
                         (msds_filename, iid))
        elif request.files.get("msds_file") and request.files["msds_file"].filename:
            flash("MSDS upload skipped — only PDF files up to 5MB are accepted.", "error")
        conn.commit()
        db_module.log_audit(session["username"], "INVENTORY_ITEM_CREATED",
                             "inventory_items", iid, new_value=request.form["name"])
        conn.close()
        flash(f"{request.form['name']} added to inventory.", "success")
        return redirect(url_for("inventory_bp.index"))

    return render_template("inventory/form.html", item=None,
                            categories=Config.DEFAULT_INVENTORY_CATEGORIES)


@inventory_bp.route("/<item_id>/edit", methods=["GET", "POST"])
@login_required
@office_required
def edit(item_id):
    conn = db_module.get_db()
    item = conn.execute("SELECT * FROM inventory_items WHERE id=?", (item_id,)).fetchone()
    if not item:
        conn.close()
        abort(404)
    if request.method == "POST":
        conn.execute(
            "UPDATE inventory_items SET name=?, category=?, unit=?, minimum_stock=?, "
            "reorder_qty=?, unit_cost=?, supplier=?, batch_number=?, expiry_date=?, "
            "hsn_code=?, notes=?, updated_at=? WHERE id=?",
            (request.form["name"], request.form.get("category"),
             request.form.get("unit", "Litres"),
             float(request.form.get("minimum_stock") or 5),
             float(request.form.get("reorder_qty") or 10),
             float(request.form.get("unit_cost") or 0) or None,
             request.form.get("supplier"), request.form.get("batch_number"),
             request.form.get("expiry_date") or None, request.form.get("hsn_code"),
             request.form.get("notes"), db_module.now_iso(), item_id),
        )
        msds_filename = _save_msds(item_id, request.files.get("msds_file"))
        if msds_filename:
            conn.execute("UPDATE inventory_items SET msds_file=? WHERE id=?",
                         (msds_filename, item_id))
        elif request.files.get("msds_file") and request.files["msds_file"].filename:
            flash("MSDS upload skipped — only PDF files up to 5MB are accepted.", "error")
        conn.commit()
        db_module.log_audit(session["username"], "INVENTORY_ITEM_UPDATED",
                             "inventory_items", item_id)
        conn.close()
        flash("Item updated.", "success")
        return redirect(url_for("inventory_bp.index"))
    conn.close()
    return render_template("inventory/form.html", item=item,
                            categories=Config.DEFAULT_INVENTORY_CATEGORIES)


@inventory_bp.route("/<item_id>/adjust", methods=["POST"])
@login_required
@office_required
def adjust(item_id):
    conn = db_module.get_db()
    item = conn.execute("SELECT * FROM inventory_items WHERE id=?", (item_id,)).fetchone()
    if not item:
        conn.close()
        abort(404)

    txn_type = request.form["txn_type"]  # IN | OUT | ADJUST | EXPIRE
    qty = float(request.form["quantity"])
    if txn_type == "IN":
        new_balance = item["current_stock"] + qty
    else:  # OUT, ADJUST (as a reduction), EXPIRE
        if qty > item["current_stock"]:
            conn.close()
            flash(f"Cannot remove more than the current stock "
                  f"({item['current_stock']} {item['unit']}).", "error")
            return redirect(url_for("inventory_bp.index"))
        new_balance = item["current_stock"] - qty

    ts = db_module.now_iso()
    conn.execute("UPDATE inventory_items SET current_stock=?, updated_at=? WHERE id=?",
                 (new_balance, ts, item_id))
    conn.execute(
        "INSERT INTO inventory_transactions (id, item_id, txn_type, quantity, balance, "
        "reference, done_by, created_at) VALUES (?,?,?,?,?,?,?,?)",
        (db_module.new_id(), item_id, txn_type, qty, new_balance,
         request.form.get("reference"), session["username"], ts),
    )
    conn.commit()
    db_module.log_audit(session["username"], f"INVENTORY_{txn_type}", "inventory_items",
                         item_id, new_value=f"{qty} {item['unit']}")
    conn.close()
    flash(f"Stock {txn_type.lower()} recorded for {item['name']}.", "success")
    return redirect(url_for("inventory_bp.index"))


@inventory_bp.route("/<item_id>/msds")
@login_required
def msds_download(item_id):
    conn = db_module.get_db()
    item = conn.execute("SELECT msds_file, name FROM inventory_items WHERE id=?",
                         (item_id,)).fetchone()
    conn.close()
    if not item or not item["msds_file"]:
        abort(404)
    full = (MSDS_DIR / item["msds_file"]).resolve()
    if MSDS_DIR.resolve() not in full.parents or not full.exists():
        abort(404)
    return send_file(full, mimetype="application/pdf",
                      download_name=f"MSDS-{item['name']}.pdf")


@inventory_bp.route("/<item_id>/msds/delete", methods=["POST"])
@login_required
@office_required
def msds_delete(item_id):
    conn = db_module.get_db()
    item = conn.execute("SELECT msds_file FROM inventory_items WHERE id=?",
                         (item_id,)).fetchone()
    if item and item["msds_file"]:
        (MSDS_DIR / item["msds_file"]).unlink(missing_ok=True)
        conn.execute("UPDATE inventory_items SET msds_file=NULL, updated_at=? WHERE id=?",
                     (db_module.now_iso(), item_id))
        conn.commit()
        db_module.log_audit(session["username"], "MSDS_REMOVED", "inventory_items", item_id)
    conn.close()
    flash("MSDS document removed.", "success")
    return redirect(url_for("inventory_bp.edit", item_id=item_id))


@inventory_bp.route("/<item_id>/transactions")
@login_required
@office_required
def transactions(item_id):
    conn = db_module.get_db()
    item = conn.execute("SELECT * FROM inventory_items WHERE id=?", (item_id,)).fetchone()
    if not item:
        conn.close()
        abort(404)
    txns = conn.execute(
        "SELECT * FROM inventory_transactions WHERE item_id=? ORDER BY created_at DESC",
        (item_id,)
    ).fetchall()
    conn.close()
    return render_template("inventory/transactions.html", item=item, txns=txns)
