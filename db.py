"""
PCT CRM — Database layer

Two backends, chosen automatically at import time:
  - PostgreSQL, when a DATABASE_URL environment variable is present — this
    is what Railway/Render/Heroku-style platforms inject automatically
    once a Postgres database is attached, so a cloud deployment gets a
    real persistent database instead of a local file that would be wiped
    on every redeploy.
  - SQLite (data/pct_crm.db), when DATABASE_URL is absent — unchanged
    local/offline single-machine behaviour, e.g. a shop PC with no
    internet dependency.

Every other module in the app calls get_db() and then uses exactly the
same conn.execute(sql, params).fetchone()/.fetchall()/.commit()/.close()
calling convention either way. _PGConnection/_PGCursor below adapt
psycopg2 (which needs %s placeholders and doesn't support sqlite3's
"conn.execute(...) returns a ready cursor" shortcut) to look and behave
like sqlite3.Connection, so none of the ~200 queries elsewhere in the
app needed to change for this to work against either backend.
"""
import os
import re
import uuid
from datetime import datetime

from config import Config, DB_PATH

DATABASE_URL = os.environ.get("DATABASE_URL", "").strip()
# Railway/Heroku-style platforms sometimes hand out "postgres://" — psycopg2
# accepts either, but normalising avoids surprises with other tooling.
if DATABASE_URL.startswith("postgres://"):
    DATABASE_URL = DATABASE_URL.replace("postgres://", "postgresql://", 1)
IS_POSTGRES = bool(DATABASE_URL)

if IS_POSTGRES:
    import psycopg2
    import psycopg2.extras
else:
    import sqlite3

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS users (
    id            TEXT PRIMARY KEY,
    username      TEXT UNIQUE NOT NULL,
    password_hash TEXT NOT NULL,
    role          TEXT NOT NULL,
    full_name     TEXT,
    phone         TEXT,
    email         TEXT,
    disabled      INTEGER DEFAULT 0,
    must_change_password INTEGER DEFAULT 0,
    pin_hash      TEXT,
    photo         TEXT,
    created_at    TEXT,
    updated_at    TEXT
);

CREATE TABLE IF NOT EXISTS clients (
    id              TEXT PRIMARY KEY,
    name            TEXT NOT NULL,
    client_type     TEXT,
    phone           TEXT NOT NULL,
    email           TEXT,
    area            TEXT,
    address         TEXT,
    gst_number      TEXT,
    gst_exempt      INTEGER DEFAULT 0,
    contact_person  TEXT,
    total_visits    INTEGER DEFAULT 4,
    visit_frequency TEXT DEFAULT 'Weekly',
    services        TEXT,
    preferred_days  TEXT,
    assigned_tech   TEXT,
    contract_amount REAL,
    notes           TEXT,
    followup_date   TEXT,
    followup_note   TEXT,
    status          TEXT DEFAULT 'Active',
    rating          REAL,
    latitude        REAL,
    longitude       REAL,
    created_at      TEXT,
    updated_at      TEXT
);

CREATE TABLE IF NOT EXISTS client_sites (
    id          TEXT PRIMARY KEY,
    client_id   TEXT REFERENCES clients(id),
    site_name   TEXT NOT NULL,
    address     TEXT,
    area        TEXT,
    contact     TEXT,
    notes       TEXT,
    created_at  TEXT
);

CREATE TABLE IF NOT EXISTS visit_schedules (
    id              TEXT PRIMARY KEY,
    client_id       TEXT REFERENCES clients(id),
    site_id         TEXT REFERENCES client_sites(id),
    scheduled_date  TEXT NOT NULL,
    visit_num       INTEGER,
    services        TEXT,
    tech_name       TEXT,
    time_slot       TEXT,
    duration_minutes INTEGER DEFAULT 60,
    status          TEXT DEFAULT 'Scheduled',
    visit_notes     TEXT,
    is_revisit      INTEGER DEFAULT 0,
    amc_contract_id TEXT,
    invoice_id      TEXT,
    created_at      TEXT,
    updated_at      TEXT,
    completed_at    TEXT
);

CREATE TABLE IF NOT EXISTS job_cards (
    id                  TEXT PRIMARY KEY,
    visit_id            TEXT REFERENCES visit_schedules(id),
    client_id           TEXT REFERENCES clients(id),
    tech_name           TEXT,
    start_time          TEXT,
    end_time            TEXT,
    visit_outcome       TEXT DEFAULT 'Completed',
    pests_found         TEXT,
    treatments_done     TEXT,
    chemicals_used      TEXT,
    observations        TEXT,
    recommendations     TEXT,
    followup_required   INTEGER DEFAULT 0,
    followup_reason     TEXT,
    followup_visit_id   TEXT,
    signature_waived        INTEGER DEFAULT 0,
    signature_waived_reason TEXT,
    client_signature    TEXT,
    checkin_lat         REAL,
    checkin_lng         REAL,
    checkout_lat        REAL,
    checkout_lng        REAL,
    photos              TEXT,
    created_at          TEXT,
    updated_at          TEXT
);

CREATE TABLE IF NOT EXISTS invoices (
    id                  TEXT PRIMARY KEY,
    inv_number          TEXT UNIQUE NOT NULL,
    invoice_date        TEXT NOT NULL,
    due_date            TEXT,
    client_id           TEXT REFERENCES clients(id),
    work_order_no       TEXT,
    service_period      TEXT,
    subtotal            REAL DEFAULT 0,
    cgst_rate           REAL DEFAULT 9,
    cgst_amount         REAL DEFAULT 0,
    sgst_rate           REAL DEFAULT 9,
    sgst_amount         REAL DEFAULT 0,
    total_amount        REAL DEFAULT 0,
    amount_paid         REAL DEFAULT 0,
    balance_due         REAL DEFAULT 0,
    payment_status      TEXT DEFAULT 'Unpaid',
    is_void             INTEGER DEFAULT 0,
    notes               TEXT,
    created_by          TEXT,
    created_at          TEXT,
    updated_at          TEXT
);

CREATE TABLE IF NOT EXISTS invoice_line_items (
    id          TEXT PRIMARY KEY,
    invoice_id  TEXT REFERENCES invoices(id),
    service     TEXT NOT NULL,
    description TEXT,
    quantity    REAL DEFAULT 1,
    unit_price  REAL NOT NULL,
    amount      REAL NOT NULL,
    hsn_sac     TEXT DEFAULT '998531'
);

CREATE TABLE IF NOT EXISTS payments (
    id              TEXT PRIMARY KEY,
    invoice_id      TEXT REFERENCES invoices(id),
    client_id       TEXT REFERENCES clients(id),
    payment_date    TEXT NOT NULL,
    amount          REAL NOT NULL,
    payment_mode    TEXT,
    reference_no    TEXT,
    notes           TEXT,
    received_by     TEXT,
    created_at      TEXT
);

CREATE TABLE IF NOT EXISTS amc_contracts (
    id              TEXT PRIMARY KEY,
    client_id       TEXT REFERENCES clients(id),
    contract_type   TEXT NOT NULL,
    contract_number TEXT UNIQUE,
    start_date      TEXT NOT NULL,
    end_date        TEXT NOT NULL,
    amount          REAL,
    visit_count     INTEGER,
    services        TEXT,
    warranty_expiry TEXT,
    notes           TEXT,
    status          TEXT DEFAULT 'Active',
    renewed_from    TEXT,
    billing_frequency  TEXT DEFAULT 'Annually',
    auto_invoice       INTEGER DEFAULT 0,
    next_invoice_date  TEXT,
    auto_schedule       INTEGER DEFAULT 0,
    next_visit_date     TEXT,
    visits_per_cycle    INTEGER DEFAULT 1,
    default_tech        TEXT,
    default_time_slot   TEXT,
    created_at      TEXT,
    updated_at      TEXT
);

CREATE TABLE IF NOT EXISTS inventory_items (
    id              TEXT PRIMARY KEY,
    name            TEXT NOT NULL,
    category        TEXT,
    unit            TEXT DEFAULT 'Litres',
    current_stock   REAL DEFAULT 0,
    minimum_stock   REAL DEFAULT 5,
    reorder_qty     REAL DEFAULT 10,
    unit_cost       REAL,
    supplier        TEXT,
    batch_number    TEXT,
    expiry_date     TEXT,
    hsn_code        TEXT,
    msds_file       TEXT,
    notes           TEXT,
    created_at      TEXT,
    updated_at      TEXT
);

CREATE TABLE IF NOT EXISTS inventory_transactions (
    id          TEXT PRIMARY KEY,
    item_id     TEXT REFERENCES inventory_items(id),
    txn_type    TEXT NOT NULL,
    quantity    REAL NOT NULL,
    balance     REAL NOT NULL,
    visit_id    TEXT,
    job_card_id TEXT,
    reference   TEXT,
    done_by     TEXT,
    created_at  TEXT
);

CREATE TABLE IF NOT EXISTS routes (
    id            TEXT PRIMARY KEY,
    name          TEXT NOT NULL,
    area          TEXT,
    assigned_tech TEXT,
    client_ids    TEXT,
    notes         TEXT,
    created_at    TEXT,
    updated_at    TEXT
);

CREATE TABLE IF NOT EXISTS complaints (
    id              TEXT PRIMARY KEY,
    client_id       TEXT REFERENCES clients(id),
    visit_id        TEXT REFERENCES visit_schedules(id),
    reported_date   TEXT NOT NULL,
    complaint_type  TEXT,
    description     TEXT NOT NULL,
    status          TEXT DEFAULT 'Open',
    assigned_to     TEXT,
    resolution      TEXT,
    resolved_date   TEXT,
    created_at      TEXT,
    updated_at      TEXT
);

CREATE TABLE IF NOT EXISTS feedback (
    id          TEXT PRIMARY KEY,
    client_id   TEXT REFERENCES clients(id),
    visit_id    TEXT REFERENCES visit_schedules(id),
    tech_name   TEXT,
    rating      INTEGER,
    comment     TEXT,
    token       TEXT,
    submitted_at TEXT
);

CREATE TABLE IF NOT EXISTS notifications (
    id          TEXT PRIMARY KEY,
    type        TEXT,
    title       TEXT NOT NULL,
    message     TEXT NOT NULL,
    client_id   TEXT,
    visit_id    TEXT,
    is_read     INTEGER DEFAULT 0,
    channel     TEXT DEFAULT 'in_app',
    sent_at     TEXT,
    created_at  TEXT
);

CREATE TABLE IF NOT EXISTS settings (
    key         TEXT PRIMARY KEY,
    value       TEXT
);

CREATE TABLE IF NOT EXISTS tech_leave (
    id          TEXT PRIMARY KEY,
    tech_name   TEXT NOT NULL,
    start_date  TEXT NOT NULL,
    end_date    TEXT NOT NULL,
    reason      TEXT,
    created_by  TEXT,
    created_at  TEXT
);

CREATE TABLE IF NOT EXISTS audit_logs (
    id          TEXT PRIMARY KEY,
    username    TEXT NOT NULL,
    action      TEXT NOT NULL,
    table_name  TEXT,
    record_id   TEXT,
    old_value   TEXT,
    new_value   TEXT,
    ip_address  TEXT,
    created_at  TEXT
);

CREATE TABLE IF NOT EXISTS leads (
    id                   TEXT PRIMARY KEY,
    name                 TEXT NOT NULL,
    phone                TEXT NOT NULL,
    email                TEXT,
    client_type          TEXT,
    area                 TEXT,
    address              TEXT,
    source               TEXT,
    stage                TEXT DEFAULT 'New',
    estimated_value      REAL,
    assigned_to          TEXT,
    notes                TEXT,
    lost_reason          TEXT,
    converted_client_id  TEXT REFERENCES clients(id),
    created_at           TEXT,
    updated_at           TEXT
);

CREATE TABLE IF NOT EXISTS quotations (
    id              TEXT PRIMARY KEY,
    quote_number    TEXT UNIQUE NOT NULL,
    lead_id         TEXT REFERENCES leads(id),
    client_id       TEXT REFERENCES clients(id),
    quote_date      TEXT NOT NULL,
    valid_until     TEXT,
    subtotal        REAL DEFAULT 0,
    cgst_rate       REAL DEFAULT 9,
    cgst_amount     REAL DEFAULT 0,
    sgst_rate       REAL DEFAULT 9,
    sgst_amount     REAL DEFAULT 0,
    total_amount    REAL DEFAULT 0,
    status          TEXT DEFAULT 'Draft',
    converted_invoice_id TEXT,
    notes           TEXT,
    created_by      TEXT,
    created_at      TEXT,
    updated_at      TEXT
);

CREATE TABLE IF NOT EXISTS quotation_line_items (
    id           TEXT PRIMARY KEY,
    quotation_id TEXT REFERENCES quotations(id),
    service      TEXT NOT NULL,
    description  TEXT,
    quantity     REAL DEFAULT 1,
    unit_price   REAL NOT NULL,
    amount       REAL NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_visits_date ON visit_schedules(scheduled_date);
CREATE INDEX IF NOT EXISTS idx_visits_client ON visit_schedules(client_id);
CREATE INDEX IF NOT EXISTS idx_invoices_client ON invoices(client_id);
CREATE INDEX IF NOT EXISTS idx_payments_invoice ON payments(invoice_id);
CREATE INDEX IF NOT EXISTS idx_jobcards_visit ON job_cards(visit_id);
CREATE INDEX IF NOT EXISTS idx_inv_txn_item ON inventory_transactions(item_id);
CREATE INDEX IF NOT EXISTS idx_clients_status ON clients(status);
CREATE INDEX IF NOT EXISTS idx_leads_stage ON leads(stage);
CREATE INDEX IF NOT EXISTS idx_quotations_lead ON quotations(lead_id);
CREATE INDEX IF NOT EXISTS idx_quotations_client ON quotations(client_id);
CREATE INDEX IF NOT EXISTS idx_tech_leave_tech ON tech_leave(tech_name);
CREATE INDEX IF NOT EXISTS idx_visit_amc ON visit_schedules(amc_contract_id);
CREATE INDEX IF NOT EXISTS idx_visit_invoice ON visit_schedules(invoice_id);
"""


_PLACEHOLDER_RE = re.compile(r"\?")


def _to_pg_placeholders(sql: str) -> str:
    """SQLite-style `?` params -> psycopg2-style `%s`. Safe because this
    app never uses a literal `?` character in SQL text, only as a bound-
    parameter placeholder."""
    return _PLACEHOLDER_RE.sub("%s", sql)


class _PGCursor:
    """Wraps a psycopg2 cursor so it can be used exactly like a sqlite3
    cursor: conn.execute(...).fetchone() / .fetchall()."""

    def __init__(self, cur):
        self._cur = cur

    def fetchone(self):
        return self._cur.fetchone()

    def fetchall(self):
        return self._cur.fetchall()

    @property
    def rowcount(self):
        return self._cur.rowcount

    def close(self):
        self._cur.close()


class _PGConnection:
    """Wraps a psycopg2 connection so it can be used exactly like a
    sqlite3.Connection. sqlite3.Connection.execute(sql, params) is a
    shortcut for con.cursor().execute(sql, params) that hands back the
    cursor directly, ready to fetch from — psycopg2 has no equivalent
    shortcut, so this recreates it. Rows come back as RealDictRow, a
    dict subclass supporting row["col"] and dict(row) exactly like
    sqlite3.Row does.
    """

    def __init__(self, conn):
        self._conn = conn

    def execute(self, sql, params=()):
        cur = self._conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        # Only pass params through when there actually are some — psycopg2
        # treats ANY non-None second argument as "parse %-placeholders in
        # this query", which misfires on a query with no real placeholders
        # but a literal % in its text (e.g. a LIKE pattern embedded
        # directly rather than bound as a parameter). No current query in
        # this app does that, but this keeps a future one from breaking.
        if params:
            cur.execute(_to_pg_placeholders(sql), tuple(params))
        else:
            cur.execute(sql)
        return _PGCursor(cur)

    def executescript(self, sql):
        cur = self._conn.cursor()
        cur.execute(sql)
        cur.close()

    def commit(self):
        self._conn.commit()

    def rollback(self):
        self._conn.rollback()

    def close(self):
        self._conn.close()


def get_db():
    """Open a new database connection with row access by column name."""
    if IS_POSTGRES:
        return _PGConnection(psycopg2.connect(DATABASE_URL))
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    # Wait up to 5s for a lock instead of failing immediately — the
    # background scheduler (recurring invoices, daily checks) and web
    # requests now both write to this file, so brief contention is
    # expected and should retry rather than surface as a 500.
    conn.execute("PRAGMA busy_timeout = 5000")
    return conn


def new_id() -> str:
    return uuid.uuid4().hex


def now_iso() -> str:
    return datetime.now().isoformat(timespec="seconds")


def ym_expr(column: str) -> str:
    """SQL fragment producing a 'YYYY-MM' string from a date/text column —
    SQLite's strftime() has no Postgres equivalent (and Postgres's
    to_char() has no SQLite equivalent), so callers that need to GROUP BY
    or compare year-month should build the fragment through this rather
    than hardcoding either backend's syntax."""
    if IS_POSTGRES:
        return f"to_char({column}::date, 'YYYY-MM')"
    return f"strftime('%Y-%m', {column})"


def current_ym_expr() -> str:
    """SQL fragment producing the current 'YYYY-MM', for comparisons
    against ym_expr() output without binding today's date as a parameter."""
    if IS_POSTGRES:
        return "to_char(CURRENT_DATE, 'YYYY-MM')"
    return "strftime('%Y-%m', 'now')"


def init_db():
    """Create all tables (idempotent) and seed default data on first run."""
    conn = get_db()
    # Table creation and column migration must happen before index
    # creation: SCHEMA_SQL's CREATE INDEX statements reference columns
    # that only exist after _migrate_schema() runs on an existing
    # database (CREATE TABLE IF NOT EXISTS is a no-op for a table that's
    # already there, so a column added since that table first shipped
    # genuinely isn't there yet at the point an index on it would try to
    # be created). Splitting the script this way — rather than just
    # running it all and letting a missing-column error abort partway
    # through — means one connection.executescript() can't fail because
    # an index statement outran a still-pending column migration.
    table_stmts, index_stmts = _split_schema_sql(SCHEMA_SQL)
    conn.executescript(table_stmts)
    conn.commit()
    _migrate_schema(conn)
    conn.executescript(index_stmts)
    conn.commit()
    _seed_if_empty(conn)
    conn.close()


def _split_schema_sql(sql):
    """Splits SCHEMA_SQL into (everything before the first CREATE INDEX,
    everything from the first CREATE INDEX onward) — a plain string split
    rather than a real SQL parser, but SCHEMA_SQL is entirely hand-written
    here with CREATE TABLE statements first and CREATE INDEX statements
    grouped at the end, so this is reliable for this specific file."""
    marker = "CREATE INDEX"
    idx = sql.find(marker)
    if idx == -1:
        return sql, ""
    return sql[:idx], sql[idx:]


# Columns added to tables that already shipped in earlier versions.
# CREATE TABLE IF NOT EXISTS only helps a brand-new database — it does
# nothing for an existing one, since the table it's checking for already
# exists. An entirely new table (like tech_leave) is still handled fine
# by that IF NOT EXISTS on its own; it's specifically a *new column on an
# existing table* that silently never gets added without this, meaning a
# real, already-running install with real data would start throwing
# "no such column" errors the moment a code path touched one of these,
# rather than getting the new field at all.
_SCHEMA_MIGRATIONS = [
    ("job_cards", "visit_outcome", "TEXT DEFAULT 'Completed'"),
    ("job_cards", "followup_visit_id", "TEXT"),
    ("job_cards", "signature_waived", "INTEGER DEFAULT 0"),
    ("job_cards", "signature_waived_reason", "TEXT"),
    ("visit_schedules", "duration_minutes", "INTEGER DEFAULT 60"),
    ("visit_schedules", "amc_contract_id", "TEXT"),
    ("visit_schedules", "invoice_id", "TEXT"),
    ("amc_contracts", "auto_schedule", "INTEGER DEFAULT 0"),
    ("amc_contracts", "next_visit_date", "TEXT"),
    ("amc_contracts", "visits_per_cycle", "INTEGER DEFAULT 1"),
    ("amc_contracts", "default_tech", "TEXT"),
    ("amc_contracts", "default_time_slot", "TEXT"),
    ("users", "pin_hash", "TEXT"),
    ("users", "photo", "TEXT"),
]


def _existing_columns(conn, table):
    if IS_POSTGRES:
        rows = conn.execute(
            "SELECT column_name FROM information_schema.columns WHERE table_name=?",
            (table,)
        ).fetchall()
        return {r["column_name"] for r in rows}
    rows = conn.execute(f"PRAGMA table_info({table})").fetchall()
    return {r["name"] for r in rows}


def _migrate_schema(conn):
    """Adds any columns from _SCHEMA_MIGRATIONS that are missing from an
    existing database — safe to run on every startup, since it only acts
    on columns that aren't already there."""
    for table, column, definition in _SCHEMA_MIGRATIONS:
        try:
            have = _existing_columns(conn, table)
        except Exception:
            continue  # table doesn't exist yet on this connection somehow — SCHEMA_SQL above will have made it
        if column in have:
            continue
        try:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")
            conn.commit()
        except Exception:
            # Another worker process racing to add the same column, or a
            # backend-specific quirk — either way, not fatal, and the next
            # startup (or the other process) will have already added it.
            pass


def _seed_if_empty(conn):
    from auth import hash_password

    cur = conn.execute("SELECT COUNT(*) AS c FROM users")
    if cur.fetchone()["c"] == 0:
        ts = now_iso()
        default_users = [
            ("admin", "admin123", "admin", "System Administrator"),
            ("manager", "manager123", "manager", "Office Manager"),
            ("tech", "tech123", "technician", "Field Technician"),
        ]
        for username, pwd, role, full_name in default_users:
            conn.execute(
                "INSERT INTO users (id, username, password_hash, role, full_name, "
                "disabled, must_change_password, created_at, updated_at) "
                "VALUES (?,?,?,?,?,0,1,?,?)",
                (new_id(), username, hash_password(pwd), role, full_name, ts, ts),
            )
        conn.commit()

    cur = conn.execute("SELECT COUNT(*) AS c FROM settings")
    if cur.fetchone()["c"] == 0:
        for k, v in Config.COMPANY_DEFAULTS.items():
            conn.execute("INSERT INTO settings (key, value) VALUES (?,?)", (k, v))
        import json
        conn.execute(
            "INSERT INTO settings (key, value) VALUES (?,?)",
            ("serviceCatalog", json.dumps(Config.DEFAULT_SERVICES)),
        )
        conn.execute(
            "INSERT INTO settings (key, value) VALUES (?,?)",
            ("areaList", json.dumps(Config.DEFAULT_AREAS)),
        )
        conn.execute(
            "INSERT INTO settings (key, value) VALUES (?,?)",
            ("invoiceCounter", "0"),
        )
        conn.commit()


def get_setting(key: str, default=None):
    conn = get_db()
    row = conn.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
    conn.close()
    return row["value"] if row else default


def get_all_settings() -> dict:
    conn = get_db()
    rows = conn.execute("SELECT key, value FROM settings").fetchall()
    conn.close()
    return {r["key"]: r["value"] for r in rows}


def set_setting(key: str, value: str):
    conn = get_db()
    conn.execute(
        "INSERT INTO settings (key, value) VALUES (?,?) "
        "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
        (key, value),
    )
    conn.commit()
    conn.close()


def log_audit(user: str, action: str, table_name: str = None, record_id: str = None,
              old_value: str = None, new_value: str = None, ip_address: str = None):
    conn = get_db()
    conn.execute(
        "INSERT INTO audit_logs (id, username, action, table_name, record_id, old_value, "
        "new_value, ip_address, created_at) VALUES (?,?,?,?,?,?,?,?,?)",
        (new_id(), user, action, table_name, record_id, old_value, new_value,
         ip_address, now_iso()),
    )
    conn.commit()
    conn.close()
