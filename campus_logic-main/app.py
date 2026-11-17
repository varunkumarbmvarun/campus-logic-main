"""Flask entry point for the College Knowledge Base."""

import json
import math
import os
import secrets
import sqlite3

from flask import Flask, abort, flash, g, jsonify, redirect, render_template, request, session, url_for
from werkzeug.security import check_password_hash, generate_password_hash

from database import connect, ensure_semester_five_core_enrollments, initialize_database, refresh_record_facts
from inference_rules import RULES
from fol_engine import ForwardChainer
from knowledge_base import (
    KnowledgeBaseError,
    add_fact,
    inference_snapshot,
    query_history,
    remove_fact,
    save_query,
)
from query_processor import UnsupportedQuery, build_query, evaluate_query
from security import ADMIN_ROLES, ROLE_LABELS, install_security, log_audit, roles_required


app = Flask(__name__)
app.config["SECRET_KEY"] = os.environ.get("SECRET_KEY") or secrets.token_hex(32)
app.config["MAX_CONTENT_LENGTH"] = 8 * 1024 * 1024
_configured_ice_servers = os.environ.get("WEBRTC_ICE_SERVERS")
if _configured_ice_servers:
    try:
        app.config["WEBRTC_ICE_SERVERS"] = json.loads(_configured_ice_servers)
    except json.JSONDecodeError as exc:
        raise RuntimeError("WEBRTC_ICE_SERVERS must be a JSON array of ICE server objects.") from exc
    if (
        not isinstance(app.config["WEBRTC_ICE_SERVERS"], list)
        or not app.config["WEBRTC_ICE_SERVERS"]
        or any(not isinstance(server, dict) or not server.get("urls") for server in app.config["WEBRTC_ICE_SERVERS"])
    ):
        raise RuntimeError("WEBRTC_ICE_SERVERS must be a non-empty JSON array of ICE server objects.")
else:
    app.config["WEBRTC_ICE_SERVERS"] = [{"urls": "stun:stun.l.google.com:19302"}]
initialize_database()
install_security(app)


ENTITY_CONFIG = {
    "students": {
        "title": "Students",
        "singular": "student",
        "table": "students",
        "fields": [
            {"name": "name", "label": "Full name", "required": True},
            {"name": "registration_no", "label": "USN / Registration number"},
            {"name": "admission_no", "label": "Admission number"},
            {"name": "email", "label": "Email", "type": "email"},
            {"name": "mobile", "label": "Mobile"},
            {"name": "photo_path", "label": "Profile photograph", "type": "file"},
            {"name": "department_id", "label": "Department", "type": "department"},
            {"name": "date_of_birth", "label": "Date of birth", "type": "date"},
            {"name": "gender", "label": "Gender"},
            {"name": "blood_group", "label": "Blood group"},
            {"name": "permanent_address", "label": "Permanent address", "type": "textarea"},
            {"name": "current_address", "label": "Current address", "type": "textarea"},
            {"name": "guardian_name", "label": "Parent / guardian"},
            {"name": "guardian_contact", "label": "Guardian contact"},
            {"name": "admission_year", "label": "Admission year", "type": "number"},
            {"name": "degree", "label": "Course / degree"},
            {"name": "semester", "label": "Semester", "type": "select", "options": [str(value) for value in range(1, 9)]},
            {"name": "section", "label": "Section"},
            {"name": "batch", "label": "Batch"},
            {"name": "academic_year", "label": "Academic year"},
            {"name": "roll_number", "label": "Roll number"},
            {"name": "class_teacher_id", "label": "Class teacher", "type": "professor"},
            {"name": "cgpa", "label": "CGPA", "type": "decimal", "min": "0", "max": "10", "step": "0.01"},
            {"name": "sgpa", "label": "SGPA", "type": "decimal", "min": "0", "max": "10", "step": "0.01"},
            {"name": "backlog_subjects", "label": "Backlog subjects (codes)", "type": "textarea"},
            {"name": "academic_status", "label": "Academic status", "type": "select", "options": ["Active", "Graduated", "Suspended", "Discontinued"]},
        ],
        "columns": ["name", "registration_no", "email", "department"],
        "sql": """SELECT s.id, s.name, s.registration_no, s.email, COALESCE(d.code, '—') AS department
                  FROM students s LEFT JOIN departments d ON d.id=s.department_id
                  ORDER BY s.name COLLATE NOCASE""",
    },
    "professors": {
        "title": "Professors",
        "singular": "professor",
        "table": "professors",
        "fields": [
            {"name": "name", "label": "Full name", "required": True},
            {"name": "employee_id", "label": "Employee ID"},
            {"name": "email", "label": "Email", "type": "email"},
            {"name": "mobile", "label": "Mobile"},
            {"name": "photo_path", "label": "Profile photograph", "type": "file"},
            {"name": "designation", "label": "Designation"},
        ],
        "columns": ["name", "employee_id", "email"],
        "sql": "SELECT id, name, employee_id, email FROM professors ORDER BY name COLLATE NOCASE",
    },
    "courses": {
        "title": "Courses",
        "singular": "course",
        "table": "courses",
        "fields": [
            {"name": "code", "label": "Course code", "required": True},
            {"name": "title", "label": "Course title", "required": True},
            {"name": "semester", "label": "Semester", "type": "select", "options": [str(value) for value in range(1, 9)]},
            {"name": "description", "label": "Description", "type": "textarea"},
        ],
        "columns": ["code", "title", "semester", "description"],
        "sql": "SELECT id, code, title, semester, description FROM courses ORDER BY semester, code COLLATE NOCASE",
    },
    "departments": {
        "title": "Departments",
        "singular": "department",
        "table": "departments",
        "fields": [
            {"name": "code", "label": "Department code", "required": True},
            {"name": "name", "label": "Department name", "required": True},
            {"name": "description", "label": "Description", "type": "textarea"},
            {"name": "hod_name", "label": "HOD full name"},
            {"name": "hod_employee_id", "label": "HOD employee ID"},
            {"name": "hod_photo_path", "label": "HOD photograph", "type": "file"},
            {"name": "hod_email", "label": "HOD email", "type": "email"},
            {"name": "hod_contact", "label": "HOD contact"},
            {"name": "office_location", "label": "Office location"},
            {"name": "established_year", "label": "Established year", "type": "number"},
        ],
        "columns": ["code", "name", "hod_name", "hod_email", "office_location"],
        "sql": "SELECT id, code, name, hod_name, hod_email, office_location FROM departments ORDER BY code COLLATE NOCASE",
    },
}

RELATION_CONFIG = {
    "enrollment": {
        "table": "enrollments",
        "left": ("student_id", "students", "name"),
        "right": ("course_id", "courses", "code"),
        "label": "Student enrollment",
        "sql": """SELECT s.name AS left_name, c.code AS right_name, e.student_id AS left_id, e.course_id AS right_id
                  FROM enrollments e JOIN students s ON s.id=e.student_id
                  JOIN courses c ON c.id=e.course_id ORDER BY s.name, c.code""",
    },
    "teaching": {
        "table": "teaching_assignments",
        "left": ("professor_id", "professors", "name"),
        "right": ("course_id", "courses", "code"),
        "label": "Teaching assignment",
        "sql": """SELECT p.name AS left_name, c.code AS right_name, t.professor_id AS left_id, t.course_id AS right_id
                  FROM teaching_assignments t JOIN professors p ON p.id=t.professor_id
                  JOIN courses c ON c.id=t.course_id ORDER BY p.name, c.code""",
    },
    "membership": {
        "table": "department_memberships",
        "left": ("professor_id", "professors", "name"),
        "right": ("department_id", "departments", "code"),
        "label": "Professor department",
        "sql": """SELECT p.name AS left_name, d.code AS right_name, m.professor_id AS left_id, m.department_id AS right_id
                  FROM department_memberships m JOIN professors p ON p.id=m.professor_id
                  JOIN departments d ON d.id=m.department_id ORDER BY p.name, d.code""",
    },
    "offering": {
        "table": "course_offerings",
        "left": ("course_id", "courses", "code"),
        "right": ("department_id", "departments", "code"),
        "label": "Course offering",
        "sql": """SELECT c.code AS left_name, d.code AS right_name, o.course_id AS left_id, o.department_id AS right_id
                  FROM course_offerings o JOIN courses c ON c.id=o.course_id
                  JOIN departments d ON d.id=o.department_id ORDER BY c.code, d.code""",
    },
}


def _entity_options(connection):
    return {
        name: [dict(row) for row in connection.execute(query)]
        for name, query in {
            "students": "SELECT id, name AS label FROM students ORDER BY name",
            "professors": "SELECT id, name AS label FROM professors ORDER BY name",
            "courses": "SELECT id, code AS label FROM courses ORDER BY code",
            "departments": "SELECT id, code AS label FROM departments ORDER BY code",
        }.items()
    }


def _render_query_result(query_text):
    _, engine, facts = inference_snapshot()
    if g.user["role"] in {"hod", "faculty"}:
        department_ids = set()
        course_codes = set()
        subject_codes = set()
        professor_names = set()
        student_names = set()
        assignment_titles = set()
        with connect() as connection:
            if g.user["role"] == "hod":
                department_ids.add(g.user["department_id"])
                department = connection.execute(
                    "SELECT code FROM departments WHERE id=?", (g.user["department_id"],)
                ).fetchone()
                department_codes = {department["code"]} if department else set()
                student_names = {
                    row["name"] for row in connection.execute(
                        "SELECT name FROM students WHERE department_id=?", (g.user["department_id"],)
                    )
                }
                professor_names = {
                    row["name"] for row in connection.execute(
                        "SELECT p.name FROM professors p JOIN department_memberships m "
                        "ON m.professor_id=p.id WHERE m.department_id=?",
                        (g.user["department_id"],),
                    )
                }
                subject_codes = {
                    row["code"] for row in connection.execute(
                        "SELECT code FROM subjects WHERE department_id=?", (g.user["department_id"],)
                    )
                }
                course_codes = {
                    row["code"] for row in connection.execute(
                        "SELECT c.code FROM courses c JOIN course_offerings o ON o.course_id=c.id "
                        "WHERE o.department_id=?",
                        (g.user["department_id"],),
                    )
                }
                assignment_titles = {
                    row["title"] for row in connection.execute(
                        """SELECT a.title FROM assignments a JOIN subjects s ON s.id=a.subject_id
                           WHERE s.department_id=?""", (g.user["department_id"],)
                    )
                }
            else:
                department_codes = set()
                professor = connection.execute(
                    "SELECT name FROM professors WHERE id=?", (g.user["professor_id"],)
                ).fetchone()
                if professor:
                    professor_names.add(professor["name"])
                course_codes = {
                    row["code"] for row in connection.execute(
                        "SELECT c.code FROM teaching_assignments t JOIN courses c ON c.id=t.course_id "
                        "WHERE t.professor_id=?",
                        (g.user["professor_id"],),
                    )
                }
                subject_codes = {
                    row["code"] for row in connection.execute(
                        "SELECT code FROM subjects WHERE faculty_id=?", (g.user["professor_id"],)
                    )
                }
                assignment_titles = {
                    row["title"] for row in connection.execute(
                        "SELECT a.title FROM assignments a JOIN subjects s ON s.id=a.subject_id WHERE s.faculty_id=?",
                        (g.user["professor_id"],),
                    )
                }
                student_names = {
                    row["name"] for row in connection.execute(
                        """SELECT DISTINCT st.name FROM students st
                           JOIN (SELECT student_id,subject_id FROM subject_enrollments
                                 UNION SELECT e.student_id, s.id FROM enrollments e
                                 JOIN courses c ON c.id=e.course_id
                                 JOIN subjects s ON s.code=c.code) enrollment
                           ON enrollment.student_id=st.id
                           LEFT JOIN subjects su ON su.id=enrollment.subject_id
                           LEFT JOIN courses co ON co.code IN (
                             SELECT code FROM subjects WHERE faculty_id=?
                           ) WHERE su.faculty_id=? OR co.code IN (
                             SELECT code FROM courses c JOIN teaching_assignments t ON t.course_id=c.id
                             WHERE t.professor_id=?
                           )""",
                        (g.user["professor_id"],g.user["professor_id"],g.user["professor_id"]),
                    )
                }
            professor_names.update(
                row["name"] for row in connection.execute(
                    """SELECT DISTINCT p.name FROM professors p
                       LEFT JOIN teaching_assignments t ON t.professor_id=p.id
                       LEFT JOIN courses c ON c.id=t.course_id
                       LEFT JOIN subjects s ON s.faculty_id=p.id
                       WHERE (c.code IN ({}) OR s.code IN ({}))""".format(
                        ",".join("?" for _ in course_codes) or "NULL",
                        ",".join("?" for _ in subject_codes) or "NULL",
                    ),
                    tuple(course_codes) + tuple(subject_codes),
                )
            ) if course_codes or subject_codes else None
        department_codes = locals().get("department_codes", set())

        def visible(fact):
            args = fact.arguments
            predicate = fact.predicate
            if predicate in {"Student", "LowAttendance", "PendingFees", "PendingSubmission", "AttendancePercentage", "FeeBalance"}:
                return args[0] in student_names and (
                    predicate != "PendingSubmission" or args[1] in assignment_titles
                )
            if predicate in {"AssignmentAssigned", "NoSubmission"}:
                return args[0] in student_names and args[1] in assignment_titles
            if predicate in {"Professor", "HOD", "FacultyOf"}:
                return args[0] in professor_names and (
                    len(args) < 2 or not department_codes or args[1] in department_codes
                )
            if predicate in {"Course", "Subject"}:
                return args[0] in course_codes | subject_codes
            if predicate == "Department":
                return args[0] in department_codes
            if predicate in {"Enrolled", "Studies"}:
                return args[0] in student_names and args[1] in course_codes | subject_codes
            if predicate in {"Teaches"}:
                return args[0] in professor_names and args[1] in course_codes | subject_codes
            if predicate in {"OfferedBy", "CourseOf"}:
                return args[0] in course_codes | subject_codes and args[1] in department_codes
            if predicate in {"BelongsTo", "AssociatedWith"}:
                return args[0] in student_names | professor_names and args[1] in department_codes
            if predicate == "AssignedHOD":
                return args[1] in department_codes
            return False

        facts = [fact for fact in facts if visible(fact)]
        engine = ForwardChainer(facts)
    result = evaluate_query(query_text, facts, engine)
    save_query(query_text, json.dumps(result, ensure_ascii=False))
    return result


@app.context_processor
def inject_navigation():
    current_user = getattr(g, "user", None)
    unread_count = 0
    if current_user:
        with connect() as connection:
            student = connection.execute(
                "SELECT department_id,semester FROM students WHERE id=?",
                (current_user.get("student_id"),),
            ).fetchone() if current_user.get("student_id") else None
            department_id = current_user.get("department_id") or (
                student["department_id"] if student else None
            )
            if current_user["role"] == "faculty" and not department_id:
                membership = connection.execute(
                    """SELECT department_id FROM department_memberships
                       WHERE professor_id=? ORDER BY department_id LIMIT 1""",
                    (current_user.get("professor_id"),),
                ).fetchone()
                department_id = membership["department_id"] if membership else None
            semester = student["semester"] if student else None
            account_id = current_user["id"]
            role_audience = (
                "student" if current_user["role"] == "student"
                else "faculty" if current_user["role"] in {"faculty", "hod"}
                else current_user["role"]
            )
            unread_count = connection.execute(
                """SELECT COUNT(*) FROM notifications n
                   LEFT JOIN notification_reads r ON r.notification_id=n.id AND r.user_id=?
                   WHERE r.user_id IS NULL AND (n.expires_at IS NULL OR n.expires_at>=date('now'))
                   AND (n.target_audience IN ('Everyone',?) OR
                     (n.target_audience='All students' AND ?='student') OR
                     (n.target_audience='All faculty' AND ?='faculty') OR
                     (n.target_audience='Department' AND n.department_id=?) OR
                     (n.target_audience='Semester' AND n.semester=? AND
                      (n.department_id IS NULL OR n.department_id=?)) OR
                     (n.target_audience='Individual student' AND n.target_user_id=? AND ?='student') OR
                     (n.target_audience='Individual faculty' AND n.target_user_id=? AND ?='faculty'))""",
                (
                    current_user["id"], role_audience, role_audience, role_audience,
                    department_id, semester, department_id, account_id, role_audience,
                    account_id, role_audience,
                ),
            ).fetchone()[0]
    return {
        "entity_config": ENTITY_CONFIG,
        "current_user": current_user,
        "csrf_token": session.get("csrf_token", ""),
        "role_labels": ROLE_LABELS,
        "admin_roles": ADMIN_ROLES,
        "unread_count": unread_count,
        "department_code": (
            connect_department_code(current_user.get("department_id"))
            if current_user and current_user.get("department_id")
            else None
        ),
    }


def connect_department_code(department_id):
    with connect() as connection:
        row = connection.execute(
            "SELECT code FROM departments WHERE id=?", (department_id,)
        ).fetchone()
    return row["code"] if row else None


@app.route("/login", methods=["GET", "POST"])
def login():
    if session.get("user_id"):
        return redirect(url_for("dashboard"))
    error = None
    with connect() as connection:
        has_users = connection.execute("SELECT COUNT(*) FROM users").fetchone()[0] > 0
    if request.method == "POST":
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "")
        with connect() as connection:
            user = connection.execute(
                "SELECT id, password_hash, active FROM users WHERE username=?",
                (username,),
            ).fetchone()
        if user and user["active"] and check_password_hash(user["password_hash"], password):
            session.clear()
            session["user_id"] = user["id"]
            session["csrf_token"] = os.urandom(32).hex()
            with connect() as connection:
                row = connection.execute(
                    "SELECT id, username, role, student_id, professor_id, department_id "
                    "FROM users WHERE id=?",
                    (user["id"],),
                ).fetchone()
            g.user = dict(row)
            log_audit(user["id"], "login", "user", user["id"])
            next_url = request.args.get("next", "")
            if not next_url.startswith("/") or next_url.startswith("//"):
                next_url = url_for("dashboard")
            return redirect(next_url)
        error = "Username or password is incorrect."
    return render_template("login.html", error=error, has_users=has_users)


@app.post("/logout")
def logout():
    user = getattr(g, "user", None)
    if user:
        log_audit(user["id"], "logout", "user", user["id"])
        with connect() as connection:
            connection.execute(
                "UPDATE users SET last_seen_at=NULL, presence_state='offline' WHERE id=?",
                (user["id"],),
            )
    session.clear()
    return redirect(url_for("login"))


@app.route("/admin/users", methods=["GET", "POST"])
@roles_required("super_admin", "college_admin")
def manage_users():
    if request.method == "POST":
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "")
        role = request.form.get("role", "")
        student_id = request.form.get("student_id", type=int) or None
        professor_id = request.form.get("professor_id", type=int) or None
        department_id = request.form.get("department_id", type=int) or None
        valid_roles = set(ROLE_LABELS) if g.user["role"] == "super_admin" else set(ROLE_LABELS) - {"super_admin"}
        if not username or len(password) < 12 or role not in valid_roles:
            flash("Enter a username, a password of at least 12 characters, and a permitted role.", "error")
            return redirect(url_for("manage_users"))
        if role == "student" and not student_id:
            flash("Link a student account to a student record.", "error")
            return redirect(url_for("manage_users"))
        if role != "student" and student_id:
            flash("Only student accounts may be linked to a student record.", "error")
            return redirect(url_for("manage_users"))
        if role == "faculty" and not professor_id:
            flash("Link a faculty account to a professor record.", "error")
            return redirect(url_for("manage_users"))
        if role != "faculty" and professor_id:
            flash("Only faculty accounts may be linked to a professor record.", "error")
            return redirect(url_for("manage_users"))
        if role == "hod" and not department_id:
            flash("Assign the HOD account to a department.", "error")
            return redirect(url_for("manage_users"))
        if role != "hod" and department_id:
            flash("Only HOD accounts may be assigned a department scope.", "error")
            return redirect(url_for("manage_users"))
        with connect() as connection:
            try:
                cursor = connection.execute(
                    "INSERT INTO users (username, password_hash, role, student_id, professor_id, department_id) "
                    "VALUES (?, ?, ?, ?, ?, ?)",
                    (
                        username,
                        generate_password_hash(password),
                        role,
                        student_id,
                        professor_id,
                        department_id,
                    ),
                )
            except sqlite3.IntegrityError:
                flash("Username or linked profile is already assigned, or does not exist.", "error")
            else:
                log_audit(g.user["id"], "create", "user", cursor.lastrowid, role)
                flash("User account created.", "success")
        return redirect(url_for("manage_users"))
    with connect() as connection:
        users = [dict(row) for row in connection.execute(
            """SELECT u.id, u.username, u.role, u.active, u.created_at,
                      s.name AS student_name, p.name AS professor_name, d.code AS department_code
               FROM users u LEFT JOIN students s ON s.id=u.student_id
               LEFT JOIN professors p ON p.id=u.professor_id
               LEFT JOIN departments d ON d.id=u.department_id ORDER BY u.username"""
        )]
        options = _entity_options(connection)
    allowed_roles = ROLE_LABELS if g.user["role"] == "super_admin" else {
        role: label for role, label in ROLE_LABELS.items() if role != "super_admin"
    }
    return render_template(
        "users.html", users=users, options=options, roles=allowed_roles
    )


@app.post("/admin/users/<int:user_id>/toggle")
@roles_required("super_admin")
def toggle_user(user_id):
    if user_id == g.user["id"]:
        flash("You cannot disable your own account.", "error")
        return redirect(url_for("manage_users"))
    with connect() as connection:
        user = connection.execute(
            "SELECT id,role,active FROM users WHERE id=?", (user_id,)
        ).fetchone()
        if not user:
            abort(404)
        if user["role"] == "super_admin" and user["active"]:
            active_super_admins = connection.execute(
                "SELECT COUNT(*) FROM users WHERE role='super_admin' AND active=1"
            ).fetchone()[0]
            if active_super_admins <= 1:
                flash("At least one active super administrator must remain.", "error")
                return redirect(url_for("manage_users"))
        active = 0 if user["active"] else 1
        connection.execute("UPDATE users SET active=? WHERE id=?", (active,user_id))
        log_audit(g.user["id"], "enable" if active else "disable", "user", user_id)
    flash("User account updated.", "success")
    return redirect(url_for("manage_users"))


@app.route("/account/password", methods=["GET", "POST"])
@roles_required(*ROLE_LABELS.keys())
def change_password():
    if request.method == "POST":
        old_password = request.form.get("old_password", "")
        new_password = request.form.get("new_password", "")
        confirm_password = request.form.get("confirm_password", "")
        with connect() as connection:
            row = connection.execute(
                "SELECT password_hash FROM users WHERE id=?", (g.user["id"],)
            ).fetchone()
            if not row or not check_password_hash(row["password_hash"], old_password):
                flash("Current password did not match.", "error")
            elif len(new_password) < 12:
                flash("New password must contain at least 12 characters.", "error")
            elif new_password != confirm_password:
                flash("New password confirmation does not match.", "error")
            else:
                connection.execute(
                    "UPDATE users SET password_hash=? WHERE id=?",
                    (generate_password_hash(new_password),g.user["id"]),
                )
                log_audit(g.user["id"], "change_password", "user", g.user["id"])
                flash("Password changed.", "success")
                return redirect(url_for("dashboard"))
    return render_template("change_password.html")


@app.template_filter("from_json")
def from_json_filter(value):
    try:
        return json.loads(value)
    except (TypeError, ValueError):
        return {}


@app.get("/")
def dashboard():
    if g.user["role"] == "student":
        return redirect(url_for("college.student_portal"))
    if g.user["role"] == "hod" and g.user["department_id"]:
        return redirect(url_for("college.department_profile", department_id=g.user["department_id"]))
    if g.user["role"] == "faculty":
        return redirect(url_for("college.faculty_portal"))
    if g.user["role"] == "finance":
        return redirect(url_for("college.fees"))
    with connect() as connection:
        stats = {
            "students": connection.execute("SELECT COUNT(*) FROM students").fetchone()[0],
            "professors": connection.execute("SELECT COUNT(*) FROM professors").fetchone()[0],
            "courses": connection.execute("SELECT COUNT(*) FROM courses").fetchone()[0],
            "departments": connection.execute("SELECT COUNT(*) FROM departments").fetchone()[0],
            "rules": connection.execute("SELECT COUNT(*) FROM inference_rules").fetchone()[0],
            "subjects": connection.execute("SELECT COUNT(*) FROM subjects").fetchone()[0],
            "assignments": connection.execute("SELECT COUNT(*) FROM assignments").fetchone()[0],
            "pending_submissions": connection.execute(
                """SELECT COUNT(*) FROM assignments a JOIN subject_enrollments se ON se.subject_id=a.subject_id
                   LEFT JOIN submissions sub ON sub.assignment_id=a.id AND sub.student_id=se.student_id
                   WHERE sub.id IS NULL"""
            ).fetchone()[0],
            "average_marks": connection.execute(
                "SELECT ROUND(AVG(100.0*marks/max_marks),1) FROM internal_marks"
            ).fetchone()[0] or 0,
            "fees_collected": connection.execute(
                "SELECT COALESCE(SUM(amount),0) FROM payments WHERE status='Recorded'"
            ).fetchone()[0],
            "transactions": connection.execute("SELECT COUNT(*) FROM payments").fetchone()[0],
            "pending_fees": connection.execute(
                """SELECT COALESCE(SUM(balance),0) FROM (
                   SELECT (f.tuition+f.examination+f.laboratory+f.library+f.transport+f.hostel+f.other-f.concession)
                     - COALESCE(SUM(CASE WHEN p.status='Recorded' THEN p.amount ELSE 0 END),0) AS balance
                   FROM fees f LEFT JOIN payments p ON p.fee_id=f.id GROUP BY f.id HAVING balance>0)"""
            ).fetchone()[0],
            "overdue_fees": connection.execute(
                """SELECT COALESCE(SUM(balance),0) FROM (
                   SELECT f.due_date,
                     (f.tuition+f.examination+f.laboratory+f.library+f.transport+f.hostel+f.other-f.concession)
                     - COALESCE(SUM(CASE WHEN p.status='Recorded' THEN p.amount ELSE 0 END),0) AS balance
                   FROM fees f LEFT JOIN payments p ON p.fee_id=f.id GROUP BY f.id
                   HAVING balance>0 AND f.due_date<date('now'))"""
            ).fetchone()[0],
        }
    _, _, all_facts = inference_snapshot()
    stats["facts"] = len(all_facts)
    stats["low_attendance"] = sum(fact.predicate == "LowAttendance" for fact in all_facts)
    with connect() as connection:
        announcements = [dict(row) for row in connection.execute(
            """SELECT title,category,priority,published_at FROM notifications
               WHERE expires_at IS NULL OR expires_at>=date('now')
               ORDER BY CASE priority WHEN 'Urgent' THEN 0 WHEN 'High' THEN 1 ELSE 2 END,
                        published_at DESC LIMIT 5"""
        )]
    return render_template(
        "dashboard.html",
        stats=stats,
        rules=RULES,
        history=query_history(5),
        announcements=announcements,
    )


@app.route("/manage/<kind>", methods=["GET", "POST"])
@roles_required("super_admin", "college_admin")
def manage_entity(kind):
    config = ENTITY_CONFIG.get(kind)
    if config is None:
        return redirect(url_for("dashboard"))
    if request.method == "POST":
        record_id = request.form.get("record_id", "").strip()
        values = {}
        for field in config["fields"]:
            if field.get("type") == "file":
                continue
            value = request.form.get(field["name"], "").strip()
            if field.get("required") and not value:
                flash(f"{field['label']} is required.", "error")
                return redirect(url_for("manage_entity", kind=kind))
            if field.get("type") in {"number", "decimal"}:
                try:
                    number = (float(value) if field["type"] == "decimal" else int(value)) if value else None
                    if number is not None and (
                        (field["type"] == "decimal" and not math.isfinite(number))
                        or
                        ("min" in field and number < float(field["min"]))
                        or ("max" in field and number > float(field["max"]))
                    ):
                        raise ValueError
                    values[field["name"]] = number
                except ValueError:
                    flash(f"{field['label']} must be a whole number.", "error")
                    return redirect(url_for("manage_entity", kind=kind))
            else:
                values[field["name"]] = value if value else (
                    "" if field["name"] in {"email", "description", "backlog_subjects"} else None
                )
                if field["name"] == "academic_status" and not value:
                    values[field["name"]] = "Active"
        photo_fields = [field for field in config["fields"] if field.get("type") == "file"]
        if photo_fields:
            from college_modules import save_secure_upload
            for field in photo_fields:
                try:
                    uploaded = save_secure_upload(request.files.get(field["name"]))
                except ValueError as exc:
                    flash(str(exc), "error")
                    return redirect(url_for("manage_entity", kind=kind))
                if uploaded:
                    values[field["name"]] = uploaded
        if "department_id" in values:
            if values["department_id"]:
                try:
                    values["department_id"] = int(values["department_id"])
                except ValueError:
                    flash("Choose a valid department.", "error")
                    return redirect(url_for("manage_entity", kind=kind))
        if kind in {"students", "courses"} and values.get("semester") not in {None, ""}:
            if str(values["semester"]) not in {str(value) for value in range(1, 9)}:
                flash("Choose a semester from 1 through 8.", "error")
                return redirect(url_for("manage_entity", kind=kind))
        if kind == "students" and values.get("semester") == "5" and (
            not values.get("department_id") or not values.get("academic_year")
        ):
            flash("Assign the student's department and academic year before setting semester 5.", "error")
            return redirect(url_for("manage_entity", kind=kind))
        with connect() as connection:
            try:
                if record_id:
                    assignments = ", ".join(f"{name}=?" for name in values)
                    connection.execute(
                        f"UPDATE {config['table']} SET {assignments} WHERE id=?",
                        (*values.values(), record_id),
                    )
                    saved_record_id = int(record_id)
                else:
                    fields = ", ".join(values)
                    placeholders = ", ".join("?" for _ in values)
                    cursor = connection.execute(
                        f"INSERT INTO {config['table']} ({fields}) VALUES ({placeholders})",
                        tuple(values.values()),
                    )
                    saved_record_id = cursor.lastrowid
                auto_enrolled = 0
                if kind == "students":
                    auto_enrolled = ensure_semester_five_core_enrollments(connection, saved_record_id)
                refresh_record_facts(connection)
                log_audit(g.user["id"], "update" if record_id else "create", kind, record_id or "", values.get("name", values.get("code", "")))
                if auto_enrolled:
                    flash(
                        f"Student saved and enrolled in {auto_enrolled} semester-5 required subject(s).",
                        "success",
                    )
                else:
                    flash(f"{config['singular'].capitalize()} {'updated' if record_id else 'added'}.", "success")
            except sqlite3.IntegrityError:
                app.logger.exception("Unable to save %s record", kind)
                flash("A record with that unique name or code already exists.", "error")
        return redirect(url_for("manage_entity", kind=kind))

    with connect() as connection:
        rows = [dict(row) for row in connection.execute(config["sql"])]
        options = _entity_options(connection)
        edit_id = request.args.get("edit", type=int)
        edit_record = None
        if edit_id:
            edit_record = connection.execute(
                f"SELECT * FROM {config['table']} WHERE id=?", (edit_id,)
            ).fetchone()
            edit_record = dict(edit_record) if edit_record else None
    return render_template(
        "manage.html",
        kind=kind,
        config=config,
        rows=rows,
        options=options,
        edit_record=edit_record,
    )


@app.post("/manage/<kind>/<int:record_id>/delete")
@roles_required("super_admin", "college_admin")
def delete_entity(kind, record_id):
    config = ENTITY_CONFIG.get(kind)
    if config is None:
        return redirect(url_for("dashboard"))
    with connect() as connection:
        connection.execute(f"DELETE FROM {config['table']} WHERE id=?", (record_id,))
        refresh_record_facts(connection)
    flash(f"{config['singular'].capitalize()} deleted.", "success")
    return redirect(url_for("manage_entity", kind=kind))


@app.route("/relationships", methods=["GET"])
@roles_required("super_admin", "college_admin", "hod")
def relationships():
    with connect() as connection:
        options = _entity_options(connection)
        relations = {
            key: [dict(row) for row in connection.execute(config["sql"])]
            for key, config in RELATION_CONFIG.items()
        }
    return render_template("relationships.html", options=options, relations=relations)


@app.post("/relationships/<kind>")
@roles_required("super_admin", "college_admin", "hod")
def add_relationship(kind):
    config = RELATION_CONFIG.get(kind)
    if config is None:
        return redirect(url_for("relationships"))
    left_name, right_name = config["left"][0], config["right"][0]
    left_id = request.form.get(left_name, type=int)
    right_id = request.form.get(right_name, type=int)
    if not left_id or not right_id:
        flash("Choose both related records.", "error")
        return redirect(url_for("relationships"))
    with connect() as connection:
        try:
            connection.execute(
                f"INSERT INTO {config['table']} ({left_name}, {right_name}) VALUES (?, ?)",
                (left_id, right_id),
            )
            refresh_record_facts(connection)
        except sqlite3.IntegrityError:
            flash("That relationship already exists, or one of its records is unavailable.", "error")
        else:
            flash(f"{config['label']} saved.", "success")
    return redirect(url_for("relationships"))


@app.post("/relationships/<kind>/<int:left_id>/<int:right_id>/delete")
@roles_required("super_admin", "college_admin", "hod")
def delete_relationship(kind, left_id, right_id):
    config = RELATION_CONFIG.get(kind)
    if config is None:
        return redirect(url_for("relationships"))
    left_name, right_name = config["left"][0], config["right"][0]
    with connect() as connection:
        connection.execute(
            f"DELETE FROM {config['table']} WHERE {left_name}=? AND {right_name}=?",
            (left_id, right_id),
        )
        refresh_record_facts(connection)
    flash(f"{config['label']} removed.", "success")
    return redirect(url_for("relationships"))


@app.route("/query", methods=["GET", "POST"])
@roles_required("super_admin", "college_admin", "hod", "faculty")
def query_page():
    result = None
    query_text = ""
    error = None
    if request.method == "POST":
        query_text = request.form.get("query", "").strip()
        if request.form.get("query_type"):
            try:
                query_text = build_query(
                    request.form["query_type"],
                    {
                        "value": request.form.get("value", ""),
                        "person": request.form.get("person", ""),
                        "department": request.form.get("department", ""),
                    },
                )
            except UnsupportedQuery as exc:
                error = str(exc)
        if not error:
            try:
                result = _render_query_result(query_text)
            except UnsupportedQuery as exc:
                error = str(exc)
            except (KeyError, IndexError, TypeError) as exc:
                app.logger.exception("Query evaluation failed")
                error = "The query could not be evaluated. Check the names and try again."
    with connect() as connection:
        options = _entity_options(connection)
    return render_template(
        "query.html",
        result=result,
        error=error,
        query_text=query_text,
        options=options,
    )


@app.post("/api/query")
@roles_required("super_admin", "college_admin", "hod", "faculty")
def query_api():
    payload = request.get_json(silent=True) or {}
    if not isinstance(payload, dict):
        return jsonify({"error": "The request body must be a JSON object."}), 400
    try:
        query_text = payload.get("query", "")
        if not isinstance(query_text, str):
            raise UnsupportedQuery("Query must be provided as text.")
        query_text = query_text.strip()
        if payload.get("query_type"):
            query_text = build_query(payload["query_type"], payload)
        result = _render_query_result(query_text)
        return jsonify(result)
    except UnsupportedQuery as exc:
        return jsonify({"error": str(exc)}), 400


@app.route("/facts", methods=["GET", "POST"])
@roles_required("super_admin", "college_admin")
def facts_page():
    if request.method == "POST":
        action = request.form.get("action")
        try:
            if action == "add":
                add_fact(
                    request.form.get("predicate"),
                    request.form.get("arg1"),
                    request.form.get("arg2"),
                )
                flash("Logical fact added to the knowledge base.", "success")
            elif action == "delete":
                remove_fact(request.form.get("fact_id", type=int))
                flash("Manual fact removed.", "success")
            else:
                raise KnowledgeBaseError("Choose a valid fact operation.")
        except KnowledgeBaseError as exc:
            flash(str(exc), "error")
        return redirect(url_for("facts_page"))

    base_facts, engine, all_facts = inference_snapshot()
    base_keys = {fact.key for fact in engine.base_facts}
    derived_facts = [fact for fact in all_facts if fact.source == "derived" and fact.key not in base_keys]
    return render_template(
        "facts.html",
        base_facts=base_facts,
        derived_facts=derived_facts,
        rules=RULES,
        existential=engine.existential_status(),
    )


@app.get("/history")
@roles_required("super_admin", "college_admin")
def history_page():
    return render_template("history.html", history=query_history(100))


@app.errorhandler(500)
def handle_server_error(error):
    app.logger.exception("Unhandled application error", exc_info=error)
    return render_template("error.html"), 500


from college_modules import bp as college_blueprint

app.register_blueprint(college_blueprint)


if __name__ == "__main__":
    app.run()
