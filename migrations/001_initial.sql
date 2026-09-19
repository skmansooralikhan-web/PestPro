-- PCT CRM initial schema
-- Pest Control Technics Customer & Operations Management System

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
    status          TEXT DEFAULT 'Scheduled',
    visit_notes     TEXT,
    is_revisit      INTEGER DEFAULT 0,
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
    pests_found         TEXT,
    treatments_done     TEXT,
    chemicals_used      TEXT,
    observations        TEXT,
    recommendations     TEXT,
    followup_required   INTEGER DEFAULT 0,
    followup_reason     TEXT,
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

CREATE TABLE IF NOT EXISTS audit_logs (
    id          TEXT PRIMARY KEY,
    user        TEXT NOT NULL,
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
