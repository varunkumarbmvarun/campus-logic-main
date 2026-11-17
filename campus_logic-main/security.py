"""Authentication, role checks, CSRF validation, and audit helpers."""

import secrets
from functools import wraps

from flask import abort, flash, g, redirect, request, session, url_for

from database import connect


ADMIN_ROLES = {"super_admin", "college_admin"}
ROLE_LABELS = {
    "super_admin": "Super administrator",
    "college_admin": "College administrator",
    "hod": "HOD",
    "faculty": "Faculty",
    "student": "Student",
    "finance": "Accounts / finance",
}


def install_security(app):
    @app.before_request
    def authenticate_and_validate_csrf():
        if request.endpoint == "static":
            return None
        if "csrf_token" not in session:
            session["csrf_token"] = secrets.token_urlsafe(32)
        if request.endpoint == "login":
            if request.method == "POST" and not app.testing:
                _validate_csrf()
            return None
        user_id = session.get("user_id")
        if not user_id:
            if request.path.startswith("/api/"):
                return {"error": "Authentication required."}, 401
            return redirect(url_for("login", next=request.full_path))
        with connect() as connection:
            row = connection.execute(
                "SELECT id, username, role, student_id, professor_id, department_id "
                "FROM users WHERE id=? AND active=1",
                (user_id,),
            ).fetchone()
        if row is None:
            session.clear()
            return redirect(url_for("login"))
        g.user = dict(row)
        if request.method == "POST" and not app.testing:
            _validate_csrf()
        return None


def _validate_csrf():
    expected = session.get("csrf_token", "")
    supplied = request.headers.get("X-CSRF-Token") or request.form.get("csrf_token", "")
    if not expected or not secrets.compare_digest(expected, supplied):
        abort(400, description="The form security token is missing or expired. Refresh and retry.")


def roles_required(*roles):
    def decorate(view):
        @wraps(view)
        def wrapped(*args, **kwargs):
            user = getattr(g, "user", None)
            if user is None:
                return redirect(url_for("login"))
            if user["role"] not in roles:
                abort(403)
            return view(*args, **kwargs)

        return wrapped

    return decorate


def log_audit(user_id, action, entity, record_id="", details=""):
    with connect() as connection:
        connection.execute(
            "INSERT INTO audit_log (user_id, action, entity, record_id, details) "
            "VALUES (?, ?, ?, ?, ?)",
            (user_id, action, entity, str(record_id), details[:1000]),
        )
