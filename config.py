"""
PCT CRM — Configuration
Pest Control Technics, Hyderabad
"""
import os
import secrets
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent

# DATA_DIR is overridable via an environment variable so a cloud deployment
# can point it at a mounted persistent volume (e.g. Railway Volumes) —
# without one, anything written here (the SQLite file when not using
# Postgres, uploaded photos/MSDS/logos, DB backups) is wiped on every
# redeploy, since most cloud platforms' local filesystem is ephemeral.
# Local/offline single-machine use is unaffected: it defaults to ./data
# exactly as before.
DATA_DIR = Path(os.environ.get("DATA_DIR", str(BASE_DIR / "data")))
DB_PATH = DATA_DIR / "pct_crm.db"          # only used when DATABASE_URL isn't set
SECRET_KEY_PATH = DATA_DIR / ".secret_key"  # only used when SECRET_KEY isn't set
PHOTOS_DIR = DATA_DIR / "photos"
MSDS_DIR = DATA_DIR / "msds"
LOGOS_DIR = DATA_DIR / "logos"
BACKUPS_DIR = DATA_DIR / "backups"
TECH_PHOTOS_DIR = DATA_DIR / "tech_photos"

for d in (DATA_DIR, PHOTOS_DIR, MSDS_DIR, LOGOS_DIR, BACKUPS_DIR, TECH_PHOTOS_DIR):
    d.mkdir(parents=True, exist_ok=True)


def get_or_create_secret_key() -> str:
    """Load the Flask session-signing key.

    Prefers a SECRET_KEY environment variable — set one in production
    (Railway, etc.) so sessions survive a redeploy instead of invalidating
    every logged-in user each time. Falls back to a file generated on
    first run, which is fine for local/offline use but won't persist
    across deploys on a platform with an ephemeral filesystem.
    """
    env_key = os.environ.get("SECRET_KEY", "").strip()
    if env_key:
        return env_key
    if SECRET_KEY_PATH.exists():
        return SECRET_KEY_PATH.read_text().strip()
    key = secrets.token_hex(32)
    SECRET_KEY_PATH.write_text(key)
    try:
        os.chmod(SECRET_KEY_PATH, 0o600)
    except OSError:
        pass  # chmod may not be supported on some platforms (e.g. Windows)
    return key


class Config:
    SECRET_KEY = get_or_create_secret_key()
    SESSION_COOKIE_HTTPONLY = True
    SESSION_COOKIE_SAMESITE = "Strict"
    SESSION_COOKIE_SECURE = os.environ.get("HTTPS", "false").lower() == "true"
    PERMANENT_SESSION_LIFETIME_HOURS = 8
    MAX_CONTENT_LENGTH = 12 * 1024 * 1024  # 12 MB per request
    ALLOWED_UPLOAD_EXTENSIONS = {"jpg", "jpeg", "png", "pdf"}
    MAX_FILE_SIZE = 5 * 1024 * 1024  # 5 MB per file

    COMPANY_DEFAULTS = {
        "companyName": "PEST CONTROL TECHNICS",
        "companyAddress": "3-4-607, 1st Floor, NARAYANGUDA, Hyderabad - 500 029",
        "companyPhone": "+91 87 1212 5656",
        "companyPhone2": "+91 98 4943 5412",
        "companyEmail": "info@pesttech.in",
        "companyWebsite": "www.pesttech.in",
        "companyTagline": "Creating a Pest free environment since 1989",
        "gstNumber": "",
        "panNumber": "",
        "msmeNumber": "",
        "cgstRate": "9",
        "sgstRate": "9",
        "hsnCode": "998531",
        "invoicePrefix": "PCT",
        "invoiceDueDays": "30",
        "bankDetails": "",
        "invoiceFooter": "Thank you for your business",
        "theme": "light",
        "whatsappApiKey": "",
        "whatsappPhoneId": "",
        "logoFile": "company-logo-default.png",
        "memberLogoFile": "member-logo-default.png",
        "mapsApiKey": "",
        "smtpServer": "",
        "smtpPort": "587",
        "smtpUsername": "",
        "smtpPassword": "",
        "notifyVisitReminder": "1",
        "notifyAmcExpiry": "1",
        "notifyPaymentDue": "1",
    }

    DEFAULT_SERVICES = [
        "General Pest Control", "Cockroach Treatment", "Termite Treatment",
        "Rodent Control", "Mosquito Fogging", "Bed Bug Treatment", "Sanitization",
        "Wood Borer Treatment", "Snake Control Treatment", "Pre-Construction Termite",
        "Post-Construction Termite",
    ]

    DEFAULT_INVENTORY_CATEGORIES = [
        "Insecticide", "Rodenticide", "Fumigant", "Repellent", "Disinfectant",
        "Equipment", "PPE", "Other",
    ]

    DEFAULT_AREAS = [
        "Banjara Hills", "Jubilee Hills", "Kondapur", "Madhapur", "HITEC City",
        "Ameerpet", "Kukatpally", "Dilsukhnagar", "Secunderabad", "Gachibowli",
        "Somajiguda", "Begumpet", "Narayanguda", "Miyapur", "LB Nagar",
        "Uppal", "Malakpet", "Himayatnagar", "Abids", "Punjagutta",
    ]
