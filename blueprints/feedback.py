from flask import Blueprint, render_template, request, redirect, url_for, flash, abort

import db as db_module
from auth import login_required

feedback_bp = Blueprint("feedback_bp", __name__)


@feedback_bp.route("/")
@login_required
def index():
    conn = db_module.get_db()
    rows = conn.execute(
        "SELECT f.*, c.name AS client_name FROM feedback f "
        "JOIN clients c ON c.id = f.client_id ORDER BY f.submitted_at DESC LIMIT 200"
    ).fetchall()
    avg = conn.execute("SELECT AVG(rating) AS r FROM feedback").fetchone()["r"]
    conn.close()
    return render_template("feedback/index.html", rows=rows,
                            avg=round(avg, 2) if avg else None)


@feedback_bp.route("/submit/<token>", methods=["GET", "POST"])
def submit(token):
    """Public page (no login) reached via a WhatsApp link after visit completion."""
    conn = db_module.get_db()
    visit = conn.execute(
        "SELECT v.*, c.name AS client_name FROM visit_schedules v "
        "JOIN clients c ON c.id = v.client_id WHERE v.id = ?", (token,)
    ).fetchone()
    if not visit:
        conn.close()
        abort(404)

    already = conn.execute("SELECT id FROM feedback WHERE visit_id=?", (token,)).fetchone()

    if request.method == "POST" and not already:
        conn.execute(
            "INSERT INTO feedback (id, client_id, visit_id, tech_name, rating, comment, "
            "token, submitted_at) VALUES (?,?,?,?,?,?,?,?)",
            (db_module.new_id(), visit["client_id"], token, visit["tech_name"],
             int(request.form["rating"]), request.form.get("comment"), token,
             db_module.now_iso()),
        )
        # Roll the client's average rating
        avg = conn.execute(
            "SELECT AVG(rating) AS r FROM feedback WHERE client_id=?", (visit["client_id"],)
        ).fetchone()["r"]
        conn.execute("UPDATE clients SET rating=? WHERE id=?", (avg, visit["client_id"]))
        conn.commit()
        conn.close()
        return render_template("feedback/thanks.html", client_name=visit["client_name"])

    conn.close()
    return render_template("feedback/public.html", visit=visit, already=already)
