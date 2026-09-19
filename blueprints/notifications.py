from flask import Blueprint, render_template, redirect, url_for, session, jsonify, request

import db as db_module
from auth import login_required

notifications_bp = Blueprint("notifications_bp", __name__)


@notifications_bp.route("/")
@login_required
def index():
    conn = db_module.get_db()
    notes = conn.execute(
        "SELECT * FROM notifications ORDER BY created_at DESC LIMIT 100"
    ).fetchall()
    conn.close()
    return render_template("notifications/index.html", notes=notes)


@notifications_bp.route("/unread-count.json")
@login_required
def unread_count():
    conn = db_module.get_db()
    n = conn.execute("SELECT COUNT(*) AS c FROM notifications WHERE is_read=0").fetchone()["c"]
    conn.close()
    return jsonify({"count": n})


@notifications_bp.route("/recent.json")
@login_required
def recent():
    conn = db_module.get_db()
    notes = conn.execute(
        "SELECT id, type, title, message, is_read, created_at FROM notifications "
        "ORDER BY created_at DESC LIMIT 8"
    ).fetchall()
    conn.close()
    return jsonify([dict(n) for n in notes])


@notifications_bp.route("/mark-read/<note_id>", methods=["POST"])
@login_required
def mark_read(note_id):
    conn = db_module.get_db()
    conn.execute("UPDATE notifications SET is_read=1 WHERE id=?", (note_id,))
    conn.commit()
    conn.close()
    if request.headers.get("X-Requested-With") == "fetch":
        return jsonify({"ok": True})
    return redirect(url_for("notifications_bp.index"))


@notifications_bp.route("/mark-all-read", methods=["POST"])
@login_required
def mark_all_read():
    conn = db_module.get_db()
    conn.execute("UPDATE notifications SET is_read=1")
    conn.commit()
    conn.close()
    return redirect(url_for("notifications_bp.index"))
