"""
PCT CRM — Application entry point
Pest Control Technics Customer & Operations Management System
Run: python app.py
"""
import os
from datetime import timedelta
from pathlib import Path

from flask import Flask, session, g, redirect, url_for

from config import Config
import db as db_module
from auth import current_user


def create_app():
    app = Flask(__name__)
    app.config.from_object(Config)
    app.permanent_session_lifetime = timedelta(
        hours=Config.PERMANENT_SESSION_LIFETIME_HOURS
    )

    db_module.init_db()

    # ---- Blueprints -----------------------------------------------------
    from blueprints.auth import auth_bp
    from blueprints.dashboard import dashboard_bp
    from blueprints.clients import clients_bp
    from blueprints.scheduling import scheduling_bp
    from blueprints.jobcards import jobcards_bp
    from blueprints.invoices import invoices_bp
    from blueprints.payments import payments_bp
    from blueprints.amc import amc_bp
    from blueprints.inventory import inventory_bp
    from blueprints.reports import reports_bp
    from blueprints.notifications import notifications_bp
    from blueprints.complaints import complaints_bp
    from blueprints.feedback import feedback_bp
    from blueprints.settings import settings_bp
    from blueprints.api import api_bp
    from blueprints.leads import leads_bp
    from blueprints.quotations import quotations_bp
    from blueprints.search import search_bp

    app.register_blueprint(auth_bp, url_prefix="/auth")
    app.register_blueprint(dashboard_bp)
    app.register_blueprint(clients_bp, url_prefix="/clients")
    app.register_blueprint(scheduling_bp, url_prefix="/scheduling")
    app.register_blueprint(jobcards_bp, url_prefix="/jobcards")
    app.register_blueprint(invoices_bp, url_prefix="/invoices")
    app.register_blueprint(payments_bp, url_prefix="/payments")
    app.register_blueprint(amc_bp, url_prefix="/amc")
    app.register_blueprint(inventory_bp, url_prefix="/inventory")
    app.register_blueprint(reports_bp, url_prefix="/reports")
    app.register_blueprint(notifications_bp, url_prefix="/notifications")
    app.register_blueprint(complaints_bp, url_prefix="/complaints")
    app.register_blueprint(feedback_bp, url_prefix="/feedback")
    app.register_blueprint(settings_bp, url_prefix="/settings")
    app.register_blueprint(api_bp, url_prefix="/api")
    app.register_blueprint(leads_bp, url_prefix="/leads")
    app.register_blueprint(quotations_bp, url_prefix="/quotations")
    app.register_blueprint(search_bp, url_prefix="/search")

    # ---- Security headers -------------------------------------------------
    @app.after_request
    def set_security_headers(response):
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "SAMEORIGIN"
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; "
            "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com "
            "https://cdn.jsdelivr.net https://unpkg.com; "
            "font-src 'self' https://fonts.gstatic.com; "
            "script-src 'self' 'unsafe-inline' https://cdn.jsdelivr.net "
            "https://unpkg.com; "
            "worker-src 'self' blob:; "
            "object-src 'self' blob:; "
            "frame-src 'self' blob:; "
            "img-src 'self' data: blob:;"
        )
        return response

    # ---- Template globals --------------------------------------------------
    from csrf import get_csrf_token, validate_csrf_request

    def static_url(filename):
        """url_for('static', ...) with a ?v=<mtime> cache-buster appended
        automatically, so a browser that cached an old CSS/JS file from a
        previous version picks up the new one on the very next visit —
        no hard refresh required, and nothing to remember to bump by hand
        each time a static file changes."""
        from flask import url_for
        url = url_for("static", filename=filename)
        try:
            path = Path(app.static_folder) / filename
            version = int(path.stat().st_mtime)
        except OSError:
            return url
        return f"{url}?v={version}"

    @app.context_processor
    def inject_globals():
        return {
            "current_user": current_user(),
            "company": db_module.get_all_settings(),
            "csrf_token": get_csrf_token,
            "static_url": static_url,
        }

    @app.before_request
    def csrf_protect():
        validate_csrf_request()

    # ---- Template filters ----------------------------------------------------
    from utils import parse_json as _parse_json, fmt_currency as _fmt_currency

    @app.template_filter("from_json")
    def from_json_filter(value):
        return _parse_json(value, [])

    @app.template_filter("inr")
    def inr_filter(value):
        return _fmt_currency(value)

    @app.route("/")
    def index():
        if "user_id" in session:
            return redirect(url_for("dashboard_bp.dashboard"))
        return redirect(url_for("auth_bp.login"))

    # ---- Error pages --------------------------------------------------------
    @app.errorhandler(400)
    def bad_request(e):
        description = getattr(e, "description", "Bad request.")
        return (f"<h1>400 — Bad Request</h1><p>{description}</p>"
                "<a href='javascript:history.back()'>Go back</a>"), 400

    @app.errorhandler(403)
    def forbidden(e):
        return ("<h1>403 — Forbidden</h1><p>You don't have permission to view "
                "this page.</p><a href='/'>Go home</a>"), 403

    @app.errorhandler(404)
    def not_found(e):
        return ("<h1>404 — Not Found</h1><a href='/'>Go home</a>"), 404

    # ---- Background scheduler (visit reminders, AMC/overdue checks) -------
    # Only start once: in the reloader's child process, or immediately when
    # the Werkzeug auto-reloader is not in use at all (e.g. debug=False).
    debug_flag = os.environ.get("FLASK_DEBUG", "true").lower() == "true"
    if os.environ.get("WERKZEUG_RUN_MAIN") == "true" or not debug_flag:
        from scheduler import start_scheduler
        start_scheduler(app)

    return app


app = create_app()

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 3000))
    debug = os.environ.get("FLASK_DEBUG", "true").lower() == "true"
    app.run(host="0.0.0.0", port=port, debug=debug)
