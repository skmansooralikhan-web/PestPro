"""
PCT CRM — CSRF protection.

A lightweight, dependency-free CSRF implementation (per spec Section 11:
"CSRF protection: Flask-WTF or custom token per form"). A random token is
stored in the signed session on first access and injected into every page
via a <meta> tag; app.js copies it into every POST form as a hidden field,
and into an X-CSRFToken header for the one JSON-body AJAX call (drag-drop
rescheduling). app.py's before_request hook rejects any state-changing
request whose token doesn't match the session's.
"""
import secrets

from flask import session, request, abort


def get_csrf_token() -> str:
    """Return this session's CSRF token, generating one on first call."""
    token = session.get("csrf_token")
    if not token:
        token = secrets.token_hex(32)
        session["csrf_token"] = token
    return token


def validate_csrf_request():
    """Abort with 400 if a state-changing request lacks a valid CSRF token."""
    if request.method not in ("POST", "PUT", "PATCH", "DELETE"):
        return
    submitted = request.form.get("csrf_token") or request.headers.get("X-CSRFToken")
    expected = session.get("csrf_token")
    if not expected or not submitted or not secrets.compare_digest(submitted, expected):
        abort(400, description="Your session expired or the form was out of date. "
                                "Please go back, refresh the page, and try again.")
