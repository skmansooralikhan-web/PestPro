"""
PCT CRM — shared helper functions used across blueprints.
"""
import json
from datetime import date, datetime

import db as db_module


def normalize_phone(phone: str) -> str:
    """Strips everything but digits and keeps just the last 10, so
    "+91 98765 43210", "091-9876543210", and "9876543210" all normalize
    to the same value for duplicate-detection comparisons — covering the
    country code prefix and the older leading-0 STD-style format alike."""
    digits = "".join(c for c in (phone or "") if c.isdigit())
    return digits[-10:] if len(digits) >= 10 else digits


def parse_json(value, default=None):
    if not value:
        return default if default is not None else []
    try:
        return json.loads(value)
    except (ValueError, TypeError):
        return default if default is not None else []


def to_json(value) -> str:
    return json.dumps(value or [])


def financial_year_label(d: date) -> str:
    """April-March Indian financial year, e.g. 2026-27."""
    if d.month >= 4:
        return f"{d.year}-{str(d.year + 1)[-2:]}"
    return f"{d.year - 1}-{str(d.year)[-2:]}"


def next_invoice_number(conn) -> str:
    """PCT/YYYY-YY/NNNN — resets each financial year (April 1)."""
    prefix = db_module.get_setting("invoicePrefix", "PCT")
    today = date.today()
    fy = financial_year_label(today)
    fy_key = f"invoiceCounter:{fy}"

    row = conn.execute("SELECT value FROM settings WHERE key=?", (fy_key,)).fetchone()
    counter = int(row["value"]) + 1 if row else 1
    conn.execute(
        "INSERT INTO settings (key, value) VALUES (?,?) "
        "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
        (fy_key, str(counter)),
    )
    return f"{prefix}/{fy}/{counter:04d}"


def next_contract_number(conn, client_area: str = "") -> str:
    today = date.today()
    fy = financial_year_label(today)
    row = conn.execute(
        "SELECT value FROM settings WHERE key=?", (f"amcCounter:{fy}",)
    ).fetchone()
    counter = int(row["value"]) + 1 if row else 1
    conn.execute(
        "INSERT INTO settings (key, value) VALUES (?,?) "
        "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
        (f"amcCounter:{fy}", str(counter)),
    )
    return f"AMC/{fy}/{counter:04d}"


_ONES = ["", "One", "Two", "Three", "Four", "Five", "Six", "Seven", "Eight",
          "Nine", "Ten", "Eleven", "Twelve", "Thirteen", "Fourteen", "Fifteen",
          "Sixteen", "Seventeen", "Eighteen", "Nineteen"]
_TENS = ["", "", "Twenty", "Thirty", "Forty", "Fifty", "Sixty", "Seventy",
         "Eighty", "Ninety"]


def _two_digit_words(n: int) -> str:
    if n < 20:
        return _ONES[n]
    return (_TENS[n // 10] + (f" {_ONES[n % 10]}" if n % 10 else "")).strip()


def _three_digit_words(n: int) -> str:
    if n >= 100:
        rest = n % 100
        return (f"{_ONES[n // 100]} Hundred" +
                (f" {_two_digit_words(rest)}" if rest else ""))
    return _two_digit_words(n)


def amount_in_words(amount: float) -> str:
    """Indian numbering system: Rupees ... Lakh ... Thousand ... Crore."""
    amount = round(float(amount or 0), 2)
    rupees = int(amount)
    paise = round((amount - rupees) * 100)

    if rupees == 0:
        words = "Zero"
    else:
        parts = []
        crore, rupees = divmod(rupees, 10_000_000)
        lakh, rupees = divmod(rupees, 100_000)
        thousand, rupees = divmod(rupees, 1000)
        hundred = rupees

        if crore:
            parts.append(f"{_three_digit_words(crore)} Crore")
        if lakh:
            parts.append(f"{_three_digit_words(lakh)} Lakh")
        if thousand:
            parts.append(f"{_three_digit_words(thousand)} Thousand")
        if hundred:
            parts.append(_three_digit_words(hundred))
        words = " ".join(parts) if parts else "Zero"

    result = f"Rupees {words} Only"
    if paise:
        result = f"Rupees {words} and {_two_digit_words(paise)} Paise Only"
    return result


def fmt_currency(amount) -> str:
    """Indian-style comma grouping, e.g. 1,23,456.00"""
    try:
        amount = float(amount or 0)
    except (TypeError, ValueError):
        amount = 0.0
    neg = amount < 0
    amount = abs(amount)
    s = f"{amount:.2f}"
    whole, frac = s.split(".")
    if len(whole) > 3:
        last3 = whole[-3:]
        rest = whole[:-3]
        groups = []
        while len(rest) > 2:
            groups.insert(0, rest[-2:])
            rest = rest[:-2]
        if rest:
            groups.insert(0, rest)
        whole = ",".join(groups) + "," + last3
    result = f"₹{whole}.{frac}"
    return f"-{result}" if neg else result


def parse_date(s: str):
    if not s:
        return None
    try:
        return datetime.strptime(s, "%Y-%m-%d").date()
    except ValueError:
        return None


def next_quote_number(conn) -> str:
    """QT/YYYY-YY/NNNN — resets each financial year (April 1), like invoices."""
    today = date.today()
    fy = financial_year_label(today)
    fy_key = f"quoteCounter:{fy}"
    row = conn.execute("SELECT value FROM settings WHERE key=?", (fy_key,)).fetchone()
    counter = int(row["value"]) + 1 if row else 1
    conn.execute(
        "INSERT INTO settings (key, value) VALUES (?,?) "
        "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
        (fy_key, str(counter)),
    )
    return f"QT/{fy}/{counter:04d}"


def haversine_km(lat1, lng1, lat2, lng2) -> float:
    """Great-circle distance between two lat/lng points, in kilometres."""
    import math
    r = 6371.0
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlambda = math.radians(lng2 - lng1)
    a = (math.sin(dphi / 2) ** 2
         + math.cos(phi1) * math.cos(phi2) * math.sin(dlambda / 2) ** 2)
    return 2 * r * math.asin(min(1, a ** 0.5))


def optimize_route(stops: list) -> dict:
    """Order a list of stops to minimise total straight-line travel distance.

    `stops` is a list of dicts, each with at least 'id', 'lat', 'lng'. Stops
    missing lat/lng are left in their original relative order and appended
    after the optimised, geocoded stops (nothing to optimise against).

    Uses nearest-neighbour construction followed by 2-opt local-search
    improvement — no external mapping API required, since it only needs
    straight-line (haversine) distance between points already on file.
    Returns {'order': [stop, ...], 'total_km': float, 'skipped': [stop, ...]}.
    """
    located = [s for s in stops if s.get("lat") is not None and s.get("lng") is not None]
    skipped = [s for s in stops if s not in located]
    if len(located) < 2:
        return {"order": located + skipped, "total_km": 0.0, "skipped": skipped}

    n = len(located)
    dist = [[0.0] * n for _ in range(n)]
    for i in range(n):
        for j in range(i + 1, n):
            d = haversine_km(located[i]["lat"], located[i]["lng"],
                              located[j]["lat"], located[j]["lng"])
            dist[i][j] = dist[j][i] = d

    # Nearest-neighbour construction, starting from the first stop given.
    unvisited = set(range(1, n))
    route = [0]
    while unvisited:
        last = route[-1]
        nxt = min(unvisited, key=lambda j: dist[last][j])
        route.append(nxt)
        unvisited.remove(nxt)

    def route_length(r):
        return sum(dist[r[i]][r[i + 1]] for i in range(len(r) - 1))

    # 2-opt improvement: repeatedly reverse segments if it shortens the route.
    improved = True
    while improved:
        improved = False
        for i in range(1, n - 1):
            for j in range(i + 1, n):
                if j - i == 1:
                    continue
                new_route = route[:i] + route[i:j][::-1] + route[j:]
                if route_length(new_route) < route_length(route) - 1e-9:
                    route = new_route
                    improved = True

    ordered = [located[i] for i in route]
    return {"order": ordered + skipped, "total_km": round(route_length(route), 1),
            "skipped": skipped}


def geocode_address(address: str, api_key: str):
    """Best-effort geocoding via the Google Maps Geocoding API.

    Returns (lat, lng) on success, or None if no API key is configured, the
    address can't be resolved, or the request fails for any reason (network,
    quota, bad key) — callers should treat this as optional enrichment, not
    something to depend on.
    """
    if not api_key or not address:
        return None
    try:
        import requests
        resp = requests.get(
            "https://maps.googleapis.com/maps/api/geocode/json",
            params={"address": address, "key": api_key}, timeout=8,
        )
        data = resp.json()
        if data.get("status") == "OK" and data.get("results"):
            loc = data["results"][0]["geometry"]["location"]
            return (loc["lat"], loc["lng"])
    except Exception:
        pass
    return None


def create_notification(conn, ntype: str, title: str, message: str,
                         client_id: str = None, visit_id: str = None):
    conn.execute(
        "INSERT INTO notifications (id, type, title, message, client_id, visit_id, "
        "is_read, channel, created_at) VALUES (?,?,?,?,?,?,0,'in_app',?)",
        (db_module.new_id(), ntype, title, message, client_id, visit_id,
         db_module.now_iso()),
    )


def render_confirm_page(warning_message, action_url, confirm_field, cancel_url):
    """Renders a page asking the user to confirm a soft warning (scheduling
    conflict, technician on leave, an invoice totalling ₹0, etc.) with
    everything they just submitted carried over as hidden fields — so
    "yes, proceed anyway" actually resubmits what they entered instead of
    losing it and forcing a full re-entry, which is what a plain
    redirect-back-to-the-form would do."""
    from flask import request, render_template
    # Drop any existing value for confirm_field from what's being carried
    # over — if the original form happened to already have a hidden field
    # by that name (e.g. left over from an older version of a form), a
    # duplicate would let the original's value win over the "on" being
    # set below, since Werkzeug's form.get() returns the first match for
    # a repeated key.
    hidden_fields = [(k, v) for k, v in request.form.items(multi=True) if k != confirm_field]
    return render_template("confirm_action.html", warning_message=warning_message,
                            action_url=action_url, confirm_field=confirm_field,
                            hidden_fields=hidden_fields, cancel_url=cancel_url)
