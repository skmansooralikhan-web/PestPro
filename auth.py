"""
PCT CRM — Authentication
PBKDF2-HMAC-SHA256 password hashing + Flask session-based auth with
role decorators and simple in-memory rate limiting / lockout.
"""
import hashlib
import os
import time
from functools import wraps

from flask import session, redirect, url_for, request, abort, flash

PBKDF2_ITERATIONS = 100_000
SALT_BYTES = 16
KEY_LENGTH = 32

# In-memory throttling (per-process; resets on restart — fine for a small
# single-instance shop deployment as specified).
_failed_attempts = {}      # username -> [timestamps]
_ip_attempts = {}          # ip -> [timestamps]
LOCKOUT_THRESHOLD = 5
LOCKOUT_WINDOW_SECONDS = 15 * 60
LOCKOUT_DURATION_SECONDS = 30 * 60
IP_RATE_LIMIT = 10
IP_RATE_WINDOW_SECONDS = 60


def hash_password(password: str) -> str:
    salt = os.urandom(SALT_BYTES)
    dk = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt,
                              PBKDF2_ITERATIONS, dklen=KEY_LENGTH)
    return f"{salt.hex()}:{dk.hex()}"


def verify_password(password: str, stored_hash: str) -> bool:
    try:
        salt_hex, hash_hex = stored_hash.split(":")
    except (ValueError, AttributeError):
        return False
    salt = bytes.fromhex(salt_hex)
    dk = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt,
                              PBKDF2_ITERATIONS, dklen=KEY_LENGTH)
    return dk.hex() == hash_hex


def _prune(timestamps, window):
    cutoff = time.time() - window
    return [t for t in timestamps if t > cutoff]


def is_locked_out(username: str) -> bool:
    attempts = _prune(_failed_attempts.get(username, []), LOCKOUT_WINDOW_SECONDS)
    _failed_attempts[username] = attempts
    if len(attempts) < LOCKOUT_THRESHOLD:
        return False
    return (time.time() - attempts[-1]) < LOCKOUT_DURATION_SECONDS


def register_failed_attempt(username: str):
    _failed_attempts.setdefault(username, []).append(time.time())


def clear_failed_attempts(username: str):
    _failed_attempts.pop(username, None)


def is_ip_rate_limited(ip: str) -> bool:
    attempts = _prune(_ip_attempts.get(ip, []), IP_RATE_WINDOW_SECONDS)
    _ip_attempts[ip] = attempts
    return len(attempts) >= IP_RATE_LIMIT


def register_ip_attempt(ip: str):
    _ip_attempts.setdefault(ip, []).append(time.time())


def current_user():
    if "user_id" not in session:
        return None
    return {
        "id": session["user_id"],
        "username": session.get("username"),
        "role": session.get("role"),
        "full_name": session.get("full_name"),
    }


def login_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        if "user_id" not in session:
            return redirect(url_for("auth_bp.login", next=request.path))
        return view(*args, **kwargs)
    return wrapped


def roles_required(*roles):
    def decorator(view):
        @wraps(view)
        def wrapped(*args, **kwargs):
            if "user_id" not in session:
                return redirect(url_for("auth_bp.login", next=request.path))
            if session.get("role") not in roles:
                abort(403)
            return view(*args, **kwargs)
        return wrapped
    return decorator


def admin_required(view):
    return roles_required("admin")(view)


def office_required(view):
    """Admin or manager — i.e. not a technician."""
    return roles_required("admin", "manager")(view)
