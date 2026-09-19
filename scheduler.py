"""
PCT CRM — Background scheduled jobs (APScheduler)
Runs inside the same process as the Flask app (fine for a single-machine
shop deployment, per spec). Jobs create in-app notifications; actual
WhatsApp/email dispatch is wired through notifications_service and is a
no-op until API keys are configured in Settings.
"""
import shutil
from datetime import datetime, date, timedelta

from apscheduler.schedulers.background import BackgroundScheduler

import db as db_module
from config import DB_PATH, BACKUPS_DIR


def check_visit_reminders(app):
    """Notify for tomorrow's scheduled visits."""
    with app.app_context():
        from notifications_service import notify_visit_reminder

        conn = db_module.get_db()
        tomorrow = (date.today() + timedelta(days=1)).isoformat()
        visits = conn.execute(
            "SELECT v.id, v.client_id, v.tech_name, c.name AS client_name, "
            "c.phone AS client_phone "
            "FROM visit_schedules v JOIN clients c ON c.id = v.client_id "
            "WHERE v.scheduled_date = ? AND v.status = 'Scheduled'",
            (tomorrow,),
        ).fetchall()
        for v in visits:
            exists = conn.execute(
                "SELECT id FROM notifications WHERE type='visit_reminder' "
                "AND visit_id=?", (v["id"],),
            ).fetchone()
            if exists:
                continue
            sent = notify_visit_reminder(v["client_phone"], v["client_name"], tomorrow)
            conn.execute(
                "INSERT INTO notifications (id, type, title, message, client_id, "
                "visit_id, is_read, channel, sent_at, created_at) "
                "VALUES (?,?,?,?,?,?,0,?,?,?)",
                (db_module.new_id(), "visit_reminder", "Visit tomorrow",
                 f"Visit for {v['client_name']} is scheduled for tomorrow "
                 f"(tech: {v['tech_name'] or 'unassigned'}).",
                 v["client_id"], v["id"], "whatsapp" if sent else "in_app",
                 db_module.now_iso() if sent else None, db_module.now_iso()),
            )
        conn.commit()
        conn.close()


def check_amc_expiry(app):
    """Notify for AMC contracts expiring in 60 / 30 / 7 days."""
    with app.app_context():
        from notifications_service import notify_amc_expiry

        conn = db_module.get_db()
        today = date.today()
        contracts = conn.execute(
            "SELECT a.id, a.end_date, a.contract_number, c.name AS client_name, "
            "c.phone AS client_phone, c.email AS client_email, a.client_id "
            "FROM amc_contracts a JOIN clients c ON c.id = a.client_id "
            "WHERE a.status = 'Active'"
        ).fetchall()
        for c in contracts:
            try:
                end = datetime.strptime(c["end_date"], "%Y-%m-%d").date()
            except (ValueError, TypeError):
                continue
            days_left = (end - today).days
            if days_left in (60, 30, 7):
                urgency = "URGENT: " if days_left == 7 else ""
                sent = notify_amc_expiry(c["client_phone"], c["client_email"],
                                          c["contract_number"], days_left)
                conn.execute(
                    "INSERT INTO notifications (id, type, title, message, client_id, "
                    "is_read, channel, sent_at, created_at) VALUES (?,?,?,?,?,0,?,?,?)",
                    (db_module.new_id(), "amc_expiry",
                     f"{urgency}AMC expiring in {days_left} days",
                     f"AMC {c['contract_number']} for {c['client_name']} expires "
                     f"on {c['end_date']}.", c["client_id"],
                     "whatsapp" if sent else "in_app",
                     db_module.now_iso() if sent else None, db_module.now_iso()),
                )
        conn.commit()
        conn.close()


def check_overdue_invoices(app):
    """Flag invoices past due_date with a balance remaining."""
    with app.app_context():
        conn = db_module.get_db()
        today = date.today().isoformat()
        rows = conn.execute(
            "SELECT id, client_id, inv_number FROM invoices "
            "WHERE due_date < ? AND payment_status IN ('Unpaid','Partial') "
            "AND is_void = 0",
            (today,),
        ).fetchall()
        for r in rows:
            conn.execute(
                "UPDATE invoices SET payment_status='Overdue', updated_at=? WHERE id=?",
                (db_module.now_iso(), r["id"]),
            )
            exists = conn.execute(
                "SELECT id FROM notifications WHERE type='payment_due' "
                "AND client_id=? AND message LIKE ?",
                (r["client_id"], f"%{r['inv_number']}%"),
            ).fetchone()
            if not exists:
                conn.execute(
                    "INSERT INTO notifications (id, type, title, message, client_id, "
                    "is_read, channel, created_at) VALUES (?,?,?,?,?,0,?,?)",
                    (db_module.new_id(), "payment_due", "Invoice overdue",
                     f"Invoice {r['inv_number']} is now overdue.", r["client_id"],
                     "in_app", db_module.now_iso()),
                )
        conn.commit()
        conn.close()


def check_low_stock(app):
    with app.app_context():
        conn = db_module.get_db()
        rows = conn.execute(
            "SELECT id, name, current_stock, minimum_stock FROM inventory_items "
            "WHERE current_stock < minimum_stock"
        ).fetchall()
        for r in rows:
            exists = conn.execute(
                "SELECT id FROM notifications WHERE type='low_stock' "
                "AND message LIKE ? AND is_read = 0",
                (f"%{r['name']}%",),
            ).fetchone()
            if not exists:
                conn.execute(
                    "INSERT INTO notifications (id, type, title, message, is_read, "
                    "channel, created_at) VALUES (?,?,?,?,0,?,?)",
                    (db_module.new_id(), "low_stock", "Low stock alert",
                     f"{r['name']} is below minimum stock "
                     f"({r['current_stock']} < {r['minimum_stock']}).",
                     "in_app", db_module.now_iso()),
                )
        conn.commit()
        conn.close()


def run_backup(app):
    """Copies the local SQLite file — meaningless when running on Postgres
    (Railway/managed Postgres providers back up the actual database
    server-side; there's no local file here to copy), so this is a no-op
    in that mode."""
    if db_module.IS_POSTGRES:
        return
    with app.app_context():
        if not DB_PATH.exists():
            return
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        dest = BACKUPS_DIR / f"pct_crm_{stamp}.db"
        shutil.copy2(DB_PATH, dest)
        # Keep the most recent 30 backups only
        backups = sorted(BACKUPS_DIR.glob("pct_crm_*.db"))
        for old in backups[:-30]:
            old.unlink(missing_ok=True)


def run_amc_auto_invoicing(app):
    """Generate any AMC invoices that are due today (or overdue, if the app
    was offline past a billing date — loops per contract until caught up),
    and auto-schedule any due AMC visits the same way."""
    with app.app_context():
        from blueprints.amc import generate_recurring_invoice, generate_recurring_visit

        conn = db_module.get_db()
        contracts = conn.execute(
            "SELECT * FROM amc_contracts WHERE status='Active' AND auto_invoice=1"
        ).fetchall()
        for c in contracts:
            contract = c
            for _ in range(24):  # hard cap so a stuck contract can't loop forever
                inv_number = generate_recurring_invoice(conn, contract)
                if not inv_number:
                    break
                contract = conn.execute(
                    "SELECT * FROM amc_contracts WHERE id=?", (contract["id"],)
                ).fetchone()

        schedule_contracts = conn.execute(
            "SELECT * FROM amc_contracts WHERE status='Active' AND auto_schedule=1"
        ).fetchall()
        for c in schedule_contracts:
            contract = c
            for _ in range(24):
                prev_next = contract["next_visit_date"]
                generate_recurring_visit(conn, contract)
                contract = conn.execute(
                    "SELECT * FROM amc_contracts WHERE id=?", (contract["id"],)
                ).fetchone()
                # next_visit_date only changes when a cycle was actually due
                # and processed — unchanged means nothing more to do here.
                if contract["next_visit_date"] == prev_next:
                    break
        conn.commit()
        conn.close()


def check_stale_visit_status(app):
    """Flag visits over 24 hours past their scheduled date that still show
    Scheduled/In Progress — the office needs to confirm whether the job
    actually happened, since nobody submitted a job card for it."""
    with app.app_context():
        conn = db_module.get_db()
        cutoff = (date.today() - timedelta(days=1)).isoformat()
        visits = conn.execute(
            "SELECT v.id, v.client_id, v.scheduled_date, v.status, v.tech_name, "
            "c.name AS client_name FROM visit_schedules v "
            "JOIN clients c ON c.id = v.client_id "
            "WHERE v.scheduled_date <= ? AND v.status IN ('Scheduled', 'In Progress')",
            (cutoff,),
        ).fetchall()
        for v in visits:
            exists = conn.execute(
                "SELECT id FROM notifications WHERE type='visit_status_check' "
                "AND visit_id=? AND is_read=0", (v["id"],),
            ).fetchone()
            if exists:
                continue
            conn.execute(
                "INSERT INTO notifications (id, type, title, message, client_id, "
                "visit_id, is_read, channel, created_at) VALUES (?,?,?,?,?,?,0,?,?)",
                (db_module.new_id(), "visit_status_check", "Visit status needs checking",
                 f"Visit for {v['client_name']} on {v['scheduled_date']} "
                 f"({v['tech_name'] or 'unassigned'}) is over 24 hours old and "
                 f"still marked '{v['status']}' — confirm whether the job was "
                 f"completed or still needs doing.",
                 v["client_id"], v["id"], "in_app", db_module.now_iso()),
            )
        conn.commit()
        conn.close()


def run_daily_checks(app):
    check_visit_reminders(app)
    check_amc_expiry(app)
    check_overdue_invoices(app)
    check_low_stock(app)
    check_stale_visit_status(app)
    run_amc_auto_invoicing(app)


def start_scheduler(app):
    scheduler = BackgroundScheduler(daemon=True)
    scheduler.add_job(lambda: run_daily_checks(app), "cron", hour=8, minute=0,
                       id="daily_checks", replace_existing=True)
    scheduler.add_job(lambda: run_backup(app), "cron", hour=0, minute=0,
                       id="daily_backup", replace_existing=True)
    scheduler.start()
    app.scheduler = scheduler
    return scheduler
