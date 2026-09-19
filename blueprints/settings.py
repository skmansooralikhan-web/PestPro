import base64
import io
from datetime import date

from flask import (Blueprint, render_template, request, redirect, url_for,
                    flash, session, abort, send_file)

import db as db_module
from auth import login_required, admin_required, office_required, hash_password
from utils import parse_json, to_json
from config import LOGOS_DIR, BACKUPS_DIR, DB_PATH, Config, TECH_PHOTOS_DIR

settings_bp = Blueprint("settings_bp", __name__)


@settings_bp.route("/", methods=["GET", "POST"])
@login_required
@admin_required
def index():
    if request.method == "POST":
        for key in Config.COMPANY_DEFAULTS.keys():
            if key in request.form:
                db_module.set_setting(key, request.form[key])
        # checkboxes only appear in form data when checked
        for key in ("notifyVisitReminder", "notifyAmcExpiry", "notifyPaymentDue"):
            db_module.set_setting(key, "1" if request.form.get(key) == "on" else "0")

        services = [s.strip() for s in request.form.get("serviceCatalog", "").split(",") if s.strip()]
        if services:
            db_module.set_setting("serviceCatalog", to_json(services))
        areas = [a.strip() for a in request.form.get("areaList", "").split(",") if a.strip()]
        if areas:
            db_module.set_setting("areaList", to_json(areas))

        logo = request.files.get("logo")
        if logo and logo.filename:
            ext = logo.filename.rsplit(".", 1)[-1].lower()
            if ext in ("jpg", "jpeg", "png"):
                # Remove any previous logo under a different extension first,
                # so switching from a .png to a .jpg doesn't leave both on
                # disk with only one referenced by the setting — but never
                # delete the built-in default asset this way; only a prior
                # user upload should ever be cleaned up here.
                old = db_module.get_setting("logoFile")
                if old and old != f"company_logo.{ext}" and old != Config.COMPANY_DEFAULTS.get("logoFile"):
                    (LOGOS_DIR / old).unlink(missing_ok=True)
                dest = LOGOS_DIR / f"company_logo.{ext}"
                logo.save(dest)
                db_module.set_setting("logoFile", dest.name)

        member_logo = request.files.get("member_logo")
        if member_logo and member_logo.filename:
            ext = member_logo.filename.rsplit(".", 1)[-1].lower()
            if ext in ("jpg", "jpeg", "png"):
                old = db_module.get_setting("memberLogoFile")
                if old and old != f"member_logo.{ext}" and old != Config.COMPANY_DEFAULTS.get("memberLogoFile"):
                    (LOGOS_DIR / old).unlink(missing_ok=True)
                dest = LOGOS_DIR / f"member_logo.{ext}"
                member_logo.save(dest)
                db_module.set_setting("memberLogoFile", dest.name)

        db_module.log_audit(session["username"], "SETTINGS_UPDATED", "settings")
        flash("Settings saved.", "success")
        return redirect(url_for("settings_bp.index"))

    company = db_module.get_all_settings()
    services_catalog = parse_json(company.get("serviceCatalog"), [])
    areas = parse_json(company.get("areaList"), [])
    return render_template("settings/index.html", company=company,
                            services_catalog=", ".join(services_catalog),
                            areas=", ".join(areas))


@settings_bp.route("/logo/<slot>/preview")
@login_required
def logo_preview(slot):
    setting_key = {"company": "logoFile", "member": "memberLogoFile"}.get(slot)
    if not setting_key:
        abort(404)
    filename = db_module.get_setting(setting_key)
    if not filename:
        abort(404)
    path = LOGOS_DIR / filename
    if not path.exists():
        abort(404)
    return send_file(path)


@settings_bp.route("/logo/<slot>/remove", methods=["POST"])
@login_required
@admin_required
def remove_logo(slot):
    setting_key = {"company": "logoFile", "member": "memberLogoFile"}.get(slot)
    if not setting_key:
        abort(404)
    filename = db_module.get_setting(setting_key)
    if filename:
        # The two default logo files ship with the app so a fresh install
        # already has branding configured — never delete those from disk,
        # only a user-uploaded replacement. Otherwise "Remove" on the
        # default would permanently destroy the built-in asset, and it
        # could never come back even by reinstalling.
        if filename not in (Config.COMPANY_DEFAULTS.get("logoFile"),
                             Config.COMPANY_DEFAULTS.get("memberLogoFile")):
            (LOGOS_DIR / filename).unlink(missing_ok=True)
        conn = db_module.get_db()
        conn.execute("DELETE FROM settings WHERE key=?", (setting_key,))
        conn.commit()
        conn.close()
        db_module.log_audit(session["username"], "LOGO_REMOVED", "settings",
                             new_value=slot)
    flash(f"{'Company' if slot == 'company' else 'Member'} logo removed.", "success")
    return redirect(url_for("settings_bp.index"))


@settings_bp.route("/users")
@login_required
@admin_required
def users():
    conn = db_module.get_db()
    all_users = conn.execute("SELECT * FROM users ORDER BY role, username").fetchall()
    conn.close()
    return render_template("settings/users.html", users=all_users)


@settings_bp.route("/users/new", methods=["GET", "POST"])
@login_required
@admin_required
def new_user():
    if request.method == "POST":
        conn = db_module.get_db()
        username = request.form["username"].strip().lower()
        if not username.replace("_", "").isalnum():
            conn.close()
            flash("Username must be lowercase alphanumeric with underscores only.", "error")
            return redirect(url_for("settings_bp.new_user"))
        existing = conn.execute("SELECT id FROM users WHERE username=?", (username,)).fetchone()
        if existing:
            conn.close()
            flash("That username is already taken.", "error")
            return redirect(url_for("settings_bp.new_user"))
        password = request.form["password"]
        if len(password) < 8:
            conn.close()
            flash("Password must be at least 8 characters.", "error")
            return redirect(url_for("settings_bp.new_user"))

        uid = db_module.new_id()
        ts = db_module.now_iso()
        conn.execute(
            "INSERT INTO users (id, username, password_hash, role, full_name, phone, "
            "email, disabled, must_change_password, created_at, updated_at) "
            "VALUES (?,?,?,?,?,?,?,0,1,?,?)",
            (uid, username, hash_password(password), request.form["role"],
             request.form.get("full_name"), request.form.get("phone"),
             request.form.get("email"), ts, ts),
        )
        conn.commit()
        db_module.log_audit(session["username"], "USER_CREATED", "users", uid,
                             new_value=username)
        conn.close()
        flash(f"User \"{username}\" created.", "success")
        return redirect(url_for("settings_bp.users"))

    return render_template("settings/user_form.html")


@settings_bp.route("/users/<user_id>/toggle-disabled", methods=["POST"])
@login_required
@admin_required
def toggle_disabled(user_id):
    conn = db_module.get_db()
    user = conn.execute("SELECT * FROM users WHERE id=?", (user_id,)).fetchone()
    if not user:
        conn.close()
        abort(404)
    if user["id"] == session["user_id"]:
        conn.close()
        flash("You can't disable your own account.", "error")
        return redirect(url_for("settings_bp.users"))
    if user["role"] == "admin" and not user["disabled"]:
        active_admins = conn.execute(
            "SELECT COUNT(*) AS c FROM users WHERE role='admin' AND disabled=0"
        ).fetchone()["c"]
        if active_admins <= 1:
            conn.close()
            flash("Cannot disable the last active admin account.", "error")
            return redirect(url_for("settings_bp.users"))
    new_state = 0 if user["disabled"] else 1
    conn.execute("UPDATE users SET disabled=?, updated_at=? WHERE id=?",
                 (new_state, db_module.now_iso(), user_id))
    conn.commit()
    conn.close()
    db_module.log_audit(session["username"], "USER_DISABLED_TOGGLED", "users", user_id,
                         new_value=str(new_state))
    flash("User updated.", "success")
    return redirect(url_for("settings_bp.users"))


@settings_bp.route("/users/<user_id>/reset-password", methods=["POST"])
@login_required
@admin_required
def reset_password(user_id):
    conn = db_module.get_db()
    user = conn.execute("SELECT * FROM users WHERE id=?", (user_id,)).fetchone()
    if not user:
        conn.close()
        abort(404)
    new_password = request.form["new_password"]
    if len(new_password) < 8:
        conn.close()
        flash("Password must be at least 8 characters.", "error")
        return redirect(url_for("settings_bp.users"))
    conn.execute(
        "UPDATE users SET password_hash=?, must_change_password=1, updated_at=? WHERE id=?",
        (hash_password(new_password), db_module.now_iso(), user_id),
    )
    conn.commit()
    conn.close()
    db_module.log_audit(session["username"], "PASSWORD_RESET_BY_ADMIN", "users", user_id)
    flash(f"Password reset for {user['username']}. They'll be asked to change "
          f"it on next login.", "success")
    return redirect(url_for("settings_bp.users"))


@settings_bp.route("/users/<user_id>/phone-login", methods=["POST"])
@login_required
@admin_required
def set_phone_login(user_id):
    """Sets up (or updates) a technician's tap-photo-then-PIN login — the
    fields that matter for someone unfamiliar with typing on a phone
    keyboard, kept separate from the full edit-user form since it's
    normally a one-time setup done together with handing them the
    device."""
    conn = db_module.get_db()
    user = conn.execute("SELECT * FROM users WHERE id=?", (user_id,)).fetchone()
    if not user:
        conn.close()
        abort(404)
    if user["role"] != "technician":
        conn.close()
        flash("Photo/PIN login is only for technician accounts.", "error")
        return redirect(url_for("settings_bp.users"))

    pin = request.form.get("pin", "")
    if not pin.isdigit() or len(pin) != 4:
        conn.close()
        flash("PIN must be exactly 4 digits.", "error")
        return redirect(url_for("settings_bp.users"))

    photo_filename = user["photo"]
    photo = request.files.get("photo")
    if photo and photo.filename:
        ext = photo.filename.rsplit(".", 1)[-1].lower()
        if ext in ("jpg", "jpeg", "png"):
            if photo_filename:
                (TECH_PHOTOS_DIR / photo_filename).unlink(missing_ok=True)
            photo_filename = f"{user_id}.{ext}"
            photo.save(TECH_PHOTOS_DIR / photo_filename)

    conn.execute(
        "UPDATE users SET pin_hash=?, photo=?, updated_at=? WHERE id=?",
        (hash_password(pin), photo_filename, db_module.now_iso(), user_id),
    )
    conn.commit()
    conn.close()
    db_module.log_audit(session["username"], "PHONE_LOGIN_SET", "users", user_id,
                         new_value=user["username"])
    flash(f"Phone login set up for {user['username']}. They can now sign in by "
          f"tapping their photo at the login page.", "success")
    return redirect(url_for("settings_bp.users"))


@settings_bp.route("/backup")
@login_required
@admin_required
def backup():
    if db_module.IS_POSTGRES:
        flash("This deployment runs on PostgreSQL, not a local SQLite file — "
              "use your database provider's backup/export tools (e.g. "
              "Railway's Postgres backups, or `pg_dump`) instead.", "error")
        return redirect(url_for("settings_bp.index"))
    if not DB_PATH.exists():
        abort(404)
    ts = date.today().isoformat()
    db_module.log_audit(session["username"], "MANUAL_BACKUP_DOWNLOADED", "settings")
    return send_file(DB_PATH, as_attachment=True,
                      download_name=f"pct_crm_backup_{ts}.db")


@settings_bp.route("/audit-log")
@login_required
@admin_required
def audit_log():
    conn = db_module.get_db()
    page = int(request.args.get("page", 1))
    per_page = 50
    total = conn.execute("SELECT COUNT(*) AS c FROM audit_logs").fetchone()["c"]
    logs = conn.execute(
        "SELECT * FROM audit_logs ORDER BY created_at DESC LIMIT ? OFFSET ?",
        (per_page, (page - 1) * per_page)
    ).fetchall()
    conn.close()
    return render_template("settings/audit_log.html", logs=logs, page=page,
                            total_pages=max(1, (total + per_page - 1) // per_page))
