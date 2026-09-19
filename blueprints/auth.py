from flask import (Blueprint, render_template, request, redirect, url_for,
                    session, flash)

import db as db_module
from auth import (verify_password, hash_password, is_locked_out,
                   register_failed_attempt, clear_failed_attempts,
                   is_ip_rate_limited, register_ip_attempt, login_required,
                   current_user)

auth_bp = Blueprint("auth_bp", __name__)


@auth_bp.route("/login", methods=["GET", "POST"])
def login():
    if "user_id" in session:
        return redirect(url_for("dashboard_bp.dashboard"))

    if request.method == "POST":
        username = request.form.get("username", "").strip().lower()
        password = request.form.get("password", "")
        remember = request.form.get("remember") == "on"
        ip = request.remote_addr or "unknown"

        if is_ip_rate_limited(ip):
            flash("Too many login attempts from this network. Please wait a "
                  "minute and try again.", "error")
            return render_template("login.html")

        if is_locked_out(username):
            flash("This account is temporarily locked after repeated failed "
                  "attempts. Try again in 30 minutes.", "error")
            register_ip_attempt(ip)
            return render_template("login.html")

        conn = db_module.get_db()
        user = conn.execute(
            "SELECT * FROM users WHERE username = ?", (username,)
        ).fetchone()
        conn.close()

        register_ip_attempt(ip)

        if not user or user["disabled"] or not verify_password(password, user["password_hash"]):
            register_failed_attempt(username)
            db_module.log_audit(username or "unknown", "LOGIN_FAILED", "users",
                                 ip_address=ip)
            flash("Invalid username or password.", "error")
            return render_template("login.html")

        clear_failed_attempts(username)
        session.permanent = remember
        session["user_id"] = user["id"]
        session["username"] = user["username"]
        session["role"] = user["role"]
        session["full_name"] = user["full_name"]
        db_module.log_audit(username, "LOGIN_SUCCESS", "users", ip_address=ip)

        if user["must_change_password"]:
            return redirect(url_for("auth_bp.change_password"))

        next_url = request.args.get("next") or url_for("dashboard_bp.dashboard")
        return redirect(next_url)

    return render_template("login.html")


@auth_bp.route("/tech-login")
def tech_login():
    """A photo-grid login for technicians who aren't comfortable typing a
    username and password on a phone keyboard — tap your own photo, then
    tap a 4-digit PIN on a large numpad. Only technicians who've had a
    PIN set up by an admin appear here at all, both so the grid isn't
    cluttered with accounts that can't use it and so this never becomes
    a way to discover which usernames exist."""
    if "user_id" in session:
        return redirect(url_for("dashboard_bp.dashboard"))
    conn = db_module.get_db()
    technicians = conn.execute(
        "SELECT id, full_name, username, photo FROM users "
        "WHERE role='technician' AND disabled=0 AND pin_hash IS NOT NULL "
        "ORDER BY full_name, username"
    ).fetchall()
    conn.close()
    return render_template("tech_login.html", technicians=technicians)


@auth_bp.route("/tech-login/<user_id>", methods=["GET", "POST"])
def tech_pin(user_id):
    if "user_id" in session:
        return redirect(url_for("dashboard_bp.dashboard"))
    conn = db_module.get_db()
    user = conn.execute(
        "SELECT * FROM users WHERE id=? AND role='technician' AND disabled=0 "
        "AND pin_hash IS NOT NULL", (user_id,)
    ).fetchone()
    conn.close()
    if not user:
        flash("That technician login isn't set up. Ask an admin to set a PIN.", "error")
        return redirect(url_for("auth_bp.tech_login"))

    if request.method == "POST":
        pin = request.form.get("pin", "")
        ip = request.remote_addr or "unknown"

        if is_ip_rate_limited(ip):
            flash("Too many attempts from this network. Please wait a minute and try again.", "error")
            return render_template("tech_pin.html", user=user)

        # Shares the exact same lockout bucket as the regular password
        # login, keyed by username — a PIN is much shorter than a
        # password, so it needs the same brute-force protection, not a
        # separate, more permissive one.
        if is_locked_out(user["username"]):
            flash("This account is temporarily locked after repeated failed attempts. "
                  "Try again in 30 minutes, or ask an admin for help.", "error")
            register_ip_attempt(ip)
            return render_template("tech_pin.html", user=user)

        register_ip_attempt(ip)

        if not pin.isdigit() or len(pin) != 4 or not verify_password(pin, user["pin_hash"]):
            register_failed_attempt(user["username"])
            db_module.log_audit(user["username"], "TECH_PIN_LOGIN_FAILED", "users", ip_address=ip)
            flash("That PIN didn't match. Try again.", "error")
            return render_template("tech_pin.html", user=user)

        clear_failed_attempts(user["username"])
        session.permanent = True
        session["user_id"] = user["id"]
        session["username"] = user["username"]
        session["role"] = user["role"]
        session["full_name"] = user["full_name"]
        db_module.log_audit(user["username"], "TECH_PIN_LOGIN_SUCCESS", "users", ip_address=ip)

        # Deliberately skip the must_change_password redirect that the
        # regular username/password login goes through — that flow is a
        # typed-password change form, which would immediately undo the
        # entire point of this login path for someone who came here
        # specifically to avoid typing on a phone keyboard. Their PIN is
        # what they'll actually use going forward; the underlying text
        # password (set by whoever configured this account) isn't
        # something they need to touch.
        return redirect(url_for("jobcards_bp.tech_home"))

    return render_template("tech_pin.html", user=user)


@auth_bp.route("/tech-photo/<user_id>")
def tech_photo(user_id):
    from flask import send_from_directory, abort
    from config import TECH_PHOTOS_DIR
    conn = db_module.get_db()
    user = conn.execute("SELECT photo FROM users WHERE id=?", (user_id,)).fetchone()
    conn.close()
    if not user or not user["photo"]:
        abort(404)
    return send_from_directory(TECH_PHOTOS_DIR, user["photo"])


@auth_bp.route("/logout")
def logout():
    user = current_user()
    if user:
        db_module.log_audit(user["username"], "LOGOUT", "users")
    session.clear()
    return redirect(url_for("auth_bp.login"))


@auth_bp.route("/change-password", methods=["GET", "POST"])
@login_required
def change_password():
    if request.method == "POST":
        current_pw = request.form.get("current_password", "")
        new_pw = request.form.get("new_password", "")
        confirm_pw = request.form.get("confirm_password", "")

        conn = db_module.get_db()
        user = conn.execute(
            "SELECT * FROM users WHERE id = ?", (session["user_id"],)
        ).fetchone()

        if not user or not verify_password(current_pw, user["password_hash"]):
            flash("Current password is incorrect.", "error")
        elif len(new_pw) < 8:
            flash("New password must be at least 8 characters.", "error")
        elif new_pw != confirm_pw:
            flash("New passwords do not match.", "error")
        else:
            conn.execute(
                "UPDATE users SET password_hash=?, must_change_password=0, "
                "updated_at=? WHERE id=?",
                (hash_password(new_pw), db_module.now_iso(), user["id"]),
            )
            conn.commit()
            conn.close()
            db_module.log_audit(session["username"], "PASSWORD_CHANGED", "users",
                                 record_id=user["id"])
            flash("Password updated.", "success")
            return redirect(url_for("dashboard_bp.dashboard"))
        conn.close()

    return render_template("change_password.html")


@auth_bp.route("/me")
@login_required
def me():
    return current_user()
