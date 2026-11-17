"""Role-protected academic, attendance, finance, and communication workflows."""

import csv
import io
import json
import math
import secrets
import sqlite3
from datetime import date, datetime
from pathlib import Path

from flask import (
    Blueprint,
    abort,
    current_app,
    flash,
    g,
    jsonify,
    make_response,
    redirect,
    render_template,
    request,
    send_file,
    url_for,
)
from werkzeug.utils import secure_filename

from database import connect, ensure_semester_five_core_enrollments, refresh_record_facts
from knowledge_base import inference_snapshot
from security import ADMIN_ROLES, log_audit, roles_required
from vtu_curriculum import CURRICULA, semester_five_subjects


bp = Blueprint("college", __name__)
UPLOAD_FOLDER = Path(__file__).resolve().parent / "instance" / "uploads"
ALLOWED_UPLOADS = {".pdf", ".doc", ".docx", ".txt", ".png", ".jpg", ".jpeg"}


def _options(connection):
    return {
        "students": [dict(row) for row in connection.execute(
            """SELECT st.id, st.name, st.registration_no, st.semester, st.academic_year,
                      d.code AS department_code
               FROM students st LEFT JOIN departments d ON d.id=st.department_id
               ORDER BY st.name"""
        )],
        "professors": [dict(row) for row in connection.execute("SELECT id, name FROM professors ORDER BY name")],
        "departments": [dict(row) for row in connection.execute("SELECT id, code, name FROM departments ORDER BY code")],
        "subjects": [dict(row) for row in connection.execute(
            """SELECT s.id, s.code, s.name, s.semester, s.academic_year, s.category,
                      d.code AS department_code
               FROM subjects s JOIN departments d ON d.id=s.department_id ORDER BY s.code"""
        )],
        "sections": [row["section"] for row in connection.execute(
            "SELECT DISTINCT section FROM students WHERE section IS NOT NULL AND section<>'' ORDER BY section"
        )],
        "accounts": [dict(row) for row in connection.execute(
            "SELECT id,username,role,student_id,professor_id FROM users WHERE active=1 ORDER BY username"
        )],
    }


def _scope_options(connection, options):
    role = g.user["role"]
    if role == "student":
        student_id = g.user["student_id"]
        options["students"] = [row for row in options["students"] if row["id"] == student_id]
        options["subjects"] = [row for row in options["subjects"] if connection.execute(
            "SELECT 1 FROM subject_enrollments WHERE student_id=? AND subject_id=?",
            (student_id,row["id"]),
        ).fetchone()]
    elif role == "hod":
        department_id = g.user["department_id"]
        options["students"] = [row for row in options["students"] if connection.execute(
            "SELECT 1 FROM students WHERE id=? AND department_id=?",
            (row["id"],department_id),
        ).fetchone()]
        options["subjects"] = [row for row in options["subjects"] if connection.execute(
            "SELECT 1 FROM subjects WHERE id=? AND department_id=?",
            (row["id"],department_id),
        ).fetchone()]
        options["professors"] = [row for row in options["professors"] if connection.execute(
            "SELECT 1 FROM department_memberships WHERE professor_id=? AND department_id=?",
            (row["id"],department_id),
        ).fetchone()]
    elif role == "faculty":
        professor_id = g.user["professor_id"]
        options["subjects"] = [row for row in options["subjects"] if connection.execute(
            "SELECT 1 FROM subjects WHERE id=? AND faculty_id=?",
            (row["id"],professor_id),
        ).fetchone()]
        options["students"] = [row for row in options["students"] if connection.execute(
            """SELECT 1 FROM subject_enrollments se JOIN subjects s ON s.id=se.subject_id
               WHERE se.student_id=? AND s.faculty_id=? LIMIT 1""",
            (row["id"],professor_id),
        ).fetchone()]
    return options


def _subject_scope(connection, subject_id):
    subject = connection.execute(
        """SELECT id, department_id, faculty_id, semester, academic_year,
                  internal_max, category, elective_group
           FROM subjects WHERE id=?""",
        (subject_id,),
    ).fetchone()
    if not subject:
        abort(404)
    if g.user["role"] == "faculty" and subject["faculty_id"] != g.user["professor_id"]:
        abort(403)
    if g.user["role"] == "hod" and subject["department_id"] != g.user["department_id"]:
        abort(403)
    if g.user["role"] == "student" and not connection.execute(
        "SELECT 1 FROM subject_enrollments WHERE subject_id=? AND student_id=?",
        (subject_id,g.user["student_id"]),
    ).fetchone():
        abort(403)
    return subject


def _student_scope(student_id):
    if g.user["role"] == "student" and student_id != g.user["student_id"]:
        abort(403)


def _connection_contact(connection, contact_user_id):
    if contact_user_id == g.user["id"]:
        return None

    current_contact_enabled = connection.execute(
        "SELECT contact_enabled FROM users WHERE id=? AND active=1",
        (g.user["id"],),
    ).fetchone()
    if not current_contact_enabled or not current_contact_enabled["contact_enabled"]:
        return None

    peer = connection.execute(
        """SELECT u.id, u.role, u.student_id, u.professor_id, u.department_id,
                  u.username, u.contact_enabled, p.name AS professor_name, s.name AS student_name
           FROM users u
           LEFT JOIN professors p ON p.id=u.professor_id
           LEFT JOIN students s ON s.id=u.student_id
           WHERE u.id=? AND u.active=1 AND u.contact_enabled=1""",
        (contact_user_id,),
    ).fetchone()
    if not peer:
        return None

    role = g.user["role"]
    allowed = False
    if role in {"super_admin", "college_admin"}:
        allowed = peer["role"] == "faculty"
    elif role == "hod":
        allowed = peer["role"] == "faculty" and connection.execute(
            """SELECT 1 FROM department_memberships
               WHERE professor_id=? AND department_id=?""",
            (peer["professor_id"], g.user["department_id"]),
        ).fetchone() is not None
    elif role == "student":
        allowed = peer["role"] == "faculty" and g.user["student_id"] is not None and connection.execute(
            """SELECT 1 FROM subject_enrollments se
               JOIN subjects sub ON sub.id=se.subject_id
               WHERE se.student_id=? AND sub.faculty_id=?
               UNION
               SELECT 1 FROM enrollments e
               JOIN teaching_assignments ta ON ta.course_id=e.course_id
               WHERE e.student_id=? AND ta.professor_id=? LIMIT 1""",
            (
                g.user["student_id"], peer["professor_id"],
                g.user["student_id"], peer["professor_id"],
            ),
        ).fetchone() is not None
    elif role == "faculty":
        allowed = peer["role"] == "student" and g.user["professor_id"] is not None and connection.execute(
            """SELECT 1 FROM subject_enrollments se
               JOIN subjects sub ON sub.id=se.subject_id
               WHERE se.student_id=? AND sub.faculty_id=?
               UNION
               SELECT 1 FROM enrollments e
               JOIN teaching_assignments ta ON ta.course_id=e.course_id
               WHERE e.student_id=? AND ta.professor_id=? LIMIT 1""",
            (
                peer["student_id"], g.user["professor_id"],
                peer["student_id"], g.user["professor_id"],
            ),
        ).fetchone() is not None
    if not allowed:
        return None

    return {
        "id": peer["id"],
        "role": peer["role"],
        "name": peer["professor_name"] or peer["student_name"] or peer["username"],
        "profile_id": peer["professor_id"] if peer["role"] == "faculty" else peer["student_id"],
        "presence": connection.execute(
            """SELECT CASE
                 WHEN last_seen_at >= datetime('now','-90 seconds') THEN presence_state
                 WHEN last_seen_at >= datetime('now','-10 minutes') THEN 'away'
                 ELSE 'offline' END
               FROM users WHERE id=?""",
            (contact_user_id,),
        ).fetchone()[0],
    }


def _connection_contacts(connection):
    role = g.user["role"]
    if role in {"super_admin", "college_admin"}:
        candidate_ids = [
            row["id"] for row in connection.execute(
                """SELECT id FROM users WHERE active=1 AND contact_enabled=1
                   AND role='faculty' AND id<>? ORDER BY username""",
                (g.user["id"],),
            )
        ]
    elif role == "hod":
        candidate_ids = [
            row["id"] for row in connection.execute(
                """SELECT DISTINCT u.id FROM users u
                   JOIN department_memberships m ON m.professor_id=u.professor_id
                   WHERE u.active=1 AND u.contact_enabled=1 AND u.role='faculty'
                     AND u.id<>? AND m.department_id=?
                   ORDER BY u.id""",
                (g.user["id"], g.user["department_id"]),
            )
        ]
    elif role == "student" and g.user["student_id"]:
        candidate_ids = [
            row["id"] for row in connection.execute(
                """SELECT DISTINCT u.id FROM users u
                   JOIN subjects sub ON sub.faculty_id=u.professor_id
                   JOIN subject_enrollments se ON se.subject_id=sub.id
                WHERE u.active=1 AND u.contact_enabled=1 AND u.role='faculty'
                  AND u.id<>? AND se.student_id=?
                UNION
                SELECT DISTINCT u.id FROM users u
                JOIN teaching_assignments ta ON ta.professor_id=u.professor_id
                JOIN enrollments e ON e.course_id=ta.course_id
                WHERE u.active=1 AND u.contact_enabled=1 AND u.role='faculty'
                  AND u.id<>? AND e.student_id=?
                   ORDER BY u.id""",
                (g.user["id"], g.user["student_id"], g.user["id"], g.user["student_id"]),
            )
        ]
    elif role == "faculty" and g.user["professor_id"]:
        candidate_ids = [
            row["id"] for row in connection.execute(
                """SELECT DISTINCT u.id FROM users u
                   JOIN subject_enrollments se ON se.student_id=u.student_id
                   JOIN subjects sub ON sub.id=se.subject_id
                   WHERE u.active=1 AND u.contact_enabled=1 AND u.role='student'
                  AND u.id<>? AND sub.faculty_id=?
                UNION
                SELECT DISTINCT u.id FROM users u
                JOIN enrollments e ON e.student_id=u.student_id
                JOIN teaching_assignments ta ON ta.course_id=e.course_id
                WHERE u.active=1 AND u.contact_enabled=1 AND u.role='student'
                  AND u.id<>? AND ta.professor_id=?
                   ORDER BY u.id""",
                (g.user["id"], g.user["professor_id"], g.user["id"], g.user["professor_id"]),
            )
        ]
    else:
        candidate_ids = []

    contacts = []
    for contact_user_id in candidate_ids:
        contact = _connection_contact(connection, contact_user_id)
        if contact:
            contacts.append(contact)
    contacts.sort(key=lambda contact: contact["name"].casefold())
    return contacts


def _fee_totals(connection, fee_id):
    fee = connection.execute(
        "SELECT * FROM fees WHERE id=?", (fee_id,)
    ).fetchone()
    if fee is None:
        abort(404)
    total = sum(
        float(fee[column] or 0)
        for column in ("tuition", "examination", "laboratory", "library", "transport", "hostel", "other")
    )
    total = max(0.0, total - float(fee["concession"] or 0))
    paid = connection.execute(
        "SELECT COALESCE(SUM(amount),0) FROM payments WHERE fee_id=? AND status='Recorded'",
        (fee_id,),
    ).fetchone()[0]
    return fee, total, float(paid), max(0.0, total - float(paid))


@bp.get("/academics/subjects")
@roles_required("super_admin", "college_admin", "hod", "faculty", "student")
def subjects():
    if request.method == "POST":
        abort(405)
    with connect() as connection:
        options = _scope_options(connection, _options(connection))
        conditions, params = [], []
        if g.user["role"] == "student":
            conditions.append("EXISTS (SELECT 1 FROM subject_enrollments se WHERE se.subject_id=s.id AND se.student_id=?)")
            params.append(g.user["student_id"])
        elif g.user["role"] == "hod":
            conditions.append("s.department_id=?")
            params.append(g.user["department_id"])
        elif g.user["role"] == "faculty":
            conditions.append("s.faculty_id=?")
            params.append(g.user["professor_id"])
        where = " WHERE " + " AND ".join(conditions) if conditions else ""
        subject_rows = [dict(row) for row in connection.execute(
            """SELECT s.*, d.code AS department_code, p.name AS faculty_name
               FROM subjects s JOIN departments d ON d.id=s.department_id
               LEFT JOIN professors p ON p.id=s.faculty_id""" + where + " ORDER BY s.code",
            params,
        )]
    return render_template(
        "college_module.html",
        module="subjects",
        options=options,
        rows=subject_rows,
        curricula=CURRICULA,
    )


@bp.post("/academics/subjects")
@roles_required("super_admin", "college_admin", "hod")
def create_subject():
    code = request.form.get("code", "").strip().upper()
    name = request.form.get("name", "").strip()
    department_id = request.form.get("department_id", type=int)
    semester = request.form.get("semester", type=int)
    academic_year = request.form.get("academic_year", "").strip()
    faculty_id = request.form.get("faculty_id", type=int) or None
    category = request.form.get("category", "Core").strip()
    elective_group = request.form.get("elective_group", "").strip()
    try:
        credits = float(request.form.get("credits", "0"))
        internal_max = float(request.form.get("internal_max", "100"))
        external_max = float(request.form.get("external_max", "100"))
        if (
            not code or not name or not academic_year or not department_id or not semester
            or not 1 <= semester <= 8
            or not all(math.isfinite(value) for value in (credits,internal_max,external_max))
            or credits < 0 or internal_max <= 0 or external_max <= 0
            or category not in {"Core", "Elective", "Lab", "Project"}
            or elective_group and category != "Elective"
        ):
            raise ValueError
    except ValueError:
        flash("Provide a subject code, name, department, semester (1-8), academic year, and valid marks/credits.", "error")
        return redirect(url_for("college.subjects"))
    with connect() as connection:
        if g.user["role"] == "hod" and department_id != g.user["department_id"]:
            abort(403)
        if not connection.execute("SELECT 1 FROM departments WHERE id=?", (department_id,)).fetchone():
            flash("Choose an existing department.", "error")
            return redirect(url_for("college.subjects"))
        if faculty_id and not connection.execute(
            """SELECT 1 FROM department_memberships
               WHERE professor_id=? AND department_id=?""",
            (faculty_id,department_id),
        ).fetchone():
            flash("The assigned faculty member must belong to this department.", "error")
            return redirect(url_for("college.subjects"))
        try:
            cursor = connection.execute(
                """INSERT INTO subjects (code,name,department_id,semester,academic_year,category,elective_group,is_required,credits,
                   internal_max,external_max,faculty_id,outcomes,description)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    code, name, department_id, semester, academic_year,
                    category or "Core",
                    elective_group,
                    0 if category == "Elective" else 1,
                    credits, internal_max, external_max, faculty_id,
                    request.form.get("outcomes", "").strip(),
                    request.form.get("description", "").strip(),
                ),
            )
            if semester == 5 and category == "Core":
                fifth_semester_students = connection.execute(
                    """SELECT id FROM students
                       WHERE department_id=? AND semester=5 AND academic_year=?""",
                    (department_id, academic_year),
                ).fetchall()
                for student in fifth_semester_students:
                    ensure_semester_five_core_enrollments(connection, student["id"])
            refresh_record_facts(connection)
        except sqlite3.IntegrityError as exc:
            if "UNIQUE" not in str(exc):
                raise
            flash("That subject code already exists for the academic year.", "error")
        else:
            log_audit(g.user["id"], "create", "subject", cursor.lastrowid, code)
            flash("Subject added.", "success")
    return redirect(url_for("college.subjects"))


@bp.post("/academics/subjects/import-vtu")
@roles_required("super_admin", "college_admin", "hod")
def import_vtu_semester_five():
    branch_key = request.form.get("branch_key", "").strip()
    curriculum = CURRICULA.get(branch_key)
    department_id = (
        g.user["department_id"]
        if g.user["role"] == "hod"
        else request.form.get("department_id", type=int)
    )
    academic_year = request.form.get("academic_year", "").strip()
    if not curriculum or not department_id or not academic_year:
        flash("Choose a verified VTU branch, department, and academic year.", "error")
        return redirect(url_for("college.subjects"))
    with connect() as connection:
        if not connection.execute(
            "SELECT 1 FROM departments WHERE id=?", (department_id,)
        ).fetchone():
            flash("Choose an existing department.", "error")
            return redirect(url_for("college.subjects"))
        imported = 0
        subjects = semester_five_subjects(branch_key)
        for subject in subjects:
            cursor = connection.execute(
                """INSERT OR IGNORE INTO subjects
                   (code,name,department_id,semester,academic_year,category,
                    elective_group,is_required)
                   VALUES (?,?,?,5,?,?,?,?)""",
                (
                    subject["code"],
                    subject["name"],
                    department_id,
                    academic_year,
                    subject["category"],
                    subject["elective_group"],
                    int(subject["is_required"]),
                ),
            )
            imported += cursor.rowcount
        students = connection.execute(
            """SELECT id FROM students
               WHERE department_id=? AND semester=5 AND academic_year=?""",
            (department_id, academic_year),
        ).fetchall()
        for student in students:
            ensure_semester_five_core_enrollments(connection, student["id"])
        refresh_record_facts(connection)
        log_audit(
            g.user["id"],
            "import",
            "VTU 2022 semester-5 curriculum",
            None,
            f"{branch_key}; department={department_id}; year={academic_year}; imported={imported}",
        )
    if imported:
        flash(
            f"Imported {imported} VTU Semester 5 subjects; existing records were left unchanged.",
            "success",
        )
    else:
        flash("All subjects for that branch and academic year already exist.", "success")
    return redirect(url_for("college.subjects"))


@bp.post("/academics/subjects/enroll")
@roles_required("super_admin", "college_admin", "hod")
def enroll_subject():
    student_id = request.form.get("student_id", type=int)
    subject_id = request.form.get("subject_id", type=int)
    if not student_id or not subject_id:
        flash("Choose both a student and subject.", "error")
        return redirect(url_for("college.subjects"))
    with connect() as connection:
        subject = _subject_scope(connection, subject_id)
        if g.user["role"] == "hod" and subject["department_id"] != g.user["department_id"]:
            abort(403)
        student = connection.execute(
            "SELECT department_id, semester, academic_year FROM students WHERE id=?",
            (student_id,),
        ).fetchone()
        if not student:
            flash("Choose an existing student.", "error")
            return redirect(url_for("college.subjects"))
        if (
            student["department_id"] != subject["department_id"]
            or student["semester"] is not None and student["semester"] != subject["semester"]
            or student["academic_year"] and student["academic_year"] != subject["academic_year"]
        ):
            flash("The student's department, semester, and academic year must match the subject.", "error")
            return redirect(url_for("college.subjects"))
        if g.user["role"] == "hod" and not connection.execute(
            "SELECT 1 FROM students WHERE id=? AND department_id=?",
            (student_id,subject["department_id"]),
        ).fetchone():
            abort(403)
        if subject["category"] == "Elective" and subject["elective_group"]:
            already_assigned = connection.execute(
                """SELECT 1 FROM subject_enrollments se
                   JOIN subjects existing ON existing.id=se.subject_id
                   WHERE se.student_id=? AND existing.id<>?
                     AND existing.department_id=? AND existing.semester=?
                     AND existing.academic_year=? AND existing.elective_group=?
                   LIMIT 1""",
                (
                    student_id, subject_id, subject["department_id"],
                    subject["semester"], subject["academic_year"],
                    subject["elective_group"],
                ),
            ).fetchone()
            if already_assigned:
                flash("This student already has an assigned subject in that elective group.", "error")
                return redirect(url_for("college.subjects"))
        cursor = connection.execute(
            "INSERT OR IGNORE INTO subject_enrollments (student_id,subject_id) VALUES (?,?)",
            (student_id, subject_id),
        )
        refresh_record_facts(connection)
        if cursor.rowcount:
            log_audit(g.user["id"], "enroll", "subject", subject_id, str(student_id))
            flash("Student enrolled in subject.", "success")
        else:
            flash("Student is already enrolled.", "error")
    return redirect(url_for("college.subjects"))


@bp.route("/academics/marks", methods=["GET", "POST"])
@roles_required("super_admin", "college_admin", "hod", "faculty", "student")
def marks():
    if request.method == "POST":
        if g.user["role"] == "student":
            abort(403)
        student_id = request.form.get("student_id", type=int)
        subject_id = request.form.get("subject_id", type=int)
        assessment = request.form.get("assessment", "")
        try:
            mark_value = float(request.form.get("marks", ""))
            max_marks = float(request.form.get("max_marks", ""))
            if (
                not student_id or not subject_id
                or assessment not in {"IA1", "IA2", "IA3", "Assignment", "Quiz", "Practical", "Lab"}
                or not math.isfinite(mark_value) or not math.isfinite(max_marks)
                or max_marks <= 0 or mark_value < 0 or mark_value > max_marks
            ):
                raise ValueError
        except ValueError:
            flash("Select a student, subject, assessment, and valid marks within the maximum.", "error")
            return redirect(url_for("college.marks"))
        with connect() as connection:
            subject = _subject_scope(connection, subject_id)
            if max_marks > subject["internal_max"]:
                flash("Maximum marks cannot exceed this subject's internal assessment maximum.", "error")
                return redirect(url_for("college.marks"))
            if not connection.execute(
                "SELECT 1 FROM subject_enrollments WHERE student_id=? AND subject_id=?",
                (student_id,subject_id),
            ).fetchone():
                abort(403)
            cursor = connection.execute(
                """INSERT INTO internal_marks (student_id,subject_id,assessment,marks,max_marks,updated_by)
                   VALUES (?,?,?,?,?,?) ON CONFLICT(student_id,subject_id,assessment) DO UPDATE SET
                   marks=excluded.marks,max_marks=excluded.max_marks,updated_by=excluded.updated_by,
                   updated_at=CURRENT_TIMESTAMP""",
                (student_id, subject_id, assessment, mark_value, max_marks, g.user["id"]),
            )
            refresh_record_facts(connection)
            log_audit(g.user["id"], "upsert", "internal_marks", cursor.lastrowid, f"{assessment}:{mark_value}/{max_marks}")
            flash("Marks saved.", "success")
        return redirect(url_for("college.marks"))
    with connect() as connection:
        options = _scope_options(connection, _options(connection))
        clauses, params = [], []
        if g.user["role"] == "student":
            clauses.append("m.student_id=?")
            params.append(g.user["student_id"])
        if g.user["role"] == "faculty":
            clauses.append("s.faculty_id=?")
            params.append(g.user["professor_id"])
        if g.user["role"] == "hod":
            clauses.append("s.department_id=?")
            params.append(g.user["department_id"])
        where = " WHERE " + " AND ".join(clauses) if clauses else ""
        mark_rows = [dict(row) for row in connection.execute(
            """SELECT m.*, st.name AS student_name, st.registration_no, s.code AS subject_code,
                      s.name AS subject_name
               FROM internal_marks m JOIN students st ON st.id=m.student_id
               JOIN subjects s ON s.id=m.subject_id""" + where + " ORDER BY s.code,st.name,m.assessment",
            params,
        )]
    return render_template("college_module.html", module="marks", options=options, rows=mark_rows)


@bp.route("/academics/assignments", methods=["GET", "POST"])
@roles_required("super_admin", "college_admin", "hod", "faculty", "student")
def assignments():
    if request.method == "POST":
        if g.user["role"] == "student":
            abort(403)
        subject_id = request.form.get("subject_id", type=int)
        title = request.form.get("title", "").strip()
        deadline = request.form.get("deadline", "").strip()
        try:
            max_marks = float(request.form.get("max_marks", "100"))
            datetime.fromisoformat(deadline)
            if not subject_id or not title or not math.isfinite(max_marks) or max_marks <= 0:
                raise ValueError
        except ValueError:
            flash("Provide a subject, title, valid deadline, and positive maximum marks.", "error")
            return redirect(url_for("college.assignments"))
        with connect() as connection:
            _subject_scope(connection, subject_id)
            try:
                file_name = _save_upload("document")
            except ValueError as exc:
                flash(str(exc), "error")
                return redirect(url_for("college.assignments"))
            cursor = connection.execute(
                "INSERT INTO assignments (subject_id,title,description,document_name,deadline,max_marks,created_by) "
                "VALUES (?,?,?,?,?,?,?)",
                (subject_id,title,request.form.get("description","").strip(),file_name,deadline,max_marks,g.user["id"]),
            )
            refresh_record_facts(connection)
            log_audit(g.user["id"], "create", "assignment", cursor.lastrowid, title)
            flash("Assignment published.", "success")
        return redirect(url_for("college.assignments"))
    with connect() as connection:
        options = _scope_options(connection, _options(connection))
        conditions, params = [], []
        if g.user["role"] == "student":
            conditions.append("EXISTS (SELECT 1 FROM subject_enrollments se WHERE se.subject_id=a.subject_id AND se.student_id=?)")
            params.append(g.user["student_id"])
        if g.user["role"] == "faculty":
            conditions.append("s.faculty_id=?")
            params.append(g.user["professor_id"])
        if g.user["role"] == "hod":
            conditions.append("s.department_id=?")
            params.append(g.user["department_id"])
        where = " WHERE " + " AND ".join(conditions) if conditions else ""
        rows = [dict(row) for row in connection.execute(
            """SELECT a.*,s.code AS subject_code,s.name AS subject_name,
                      (SELECT COUNT(*) FROM submissions sub WHERE sub.assignment_id=a.id) AS submission_count
               FROM assignments a JOIN subjects s ON s.id=a.subject_id""" + where + " ORDER BY a.deadline DESC",
            params,
        )]
        submissions_where = ""
        submissions_params = []
        if g.user["role"] == "faculty":
            submissions_where = " WHERE s.faculty_id=?"
            submissions_params.append(g.user["professor_id"])
        elif g.user["role"] == "hod":
            submissions_where = " WHERE s.department_id=?"
            submissions_params.append(g.user["department_id"])
        submissions = [dict(row) for row in connection.execute(
            """SELECT sub.*,st.name AS student_name,st.registration_no,a.title,s.code AS subject_code
               FROM submissions sub JOIN students st ON st.id=sub.student_id
               JOIN assignments a ON a.id=sub.assignment_id JOIN subjects s ON s.id=a.subject_id"""
            + submissions_where + " ORDER BY sub.submitted_at DESC",
            submissions_params,
        )] if g.user["role"] != "student" else []
    return render_template("college_module.html", module="assignments", options=options, rows=rows, submissions=submissions)


@bp.post("/academics/assignments/<int:assignment_id>/submit")
@roles_required("student")
def submit_assignment(assignment_id):
    with connect() as connection:
        assignment = connection.execute(
            "SELECT id,deadline FROM assignments WHERE id=?", (assignment_id,)
        ).fetchone()
        if not assignment:
            abort(404)
        if not connection.execute(
            "SELECT 1 FROM subject_enrollments WHERE student_id=? AND subject_id=(SELECT subject_id FROM assignments WHERE id=?)",
            (g.user["student_id"], assignment_id),
        ).fetchone():
            abort(403)
        try:
            file_name = _save_upload("submission")
        except ValueError as exc:
            flash(str(exc), "error")
            return redirect(url_for("college.assignments"))
        if not file_name:
            flash("Choose a submission document.", "error")
            return redirect(url_for("college.assignments"))
        status = "Late Submission" if datetime.now() > datetime.fromisoformat(assignment["deadline"]) else "Submitted"
        connection.execute(
            """INSERT INTO submissions (assignment_id,student_id,file_name,status)
               VALUES (?,?,?,?) ON CONFLICT(assignment_id,student_id) DO UPDATE SET
               file_name=excluded.file_name,submitted_at=CURRENT_TIMESTAMP,status=excluded.status,
               marks=NULL,feedback='',evaluated_by=NULL""",
            (assignment_id,g.user["student_id"],file_name,status),
        )
        refresh_record_facts(connection)
        log_audit(g.user["id"], "submit", "assignment", assignment_id, file_name)
    flash("Submission received.", "success")
    return redirect(url_for("college.assignments"))


@bp.post("/academics/submissions/<int:submission_id>/evaluate")
@roles_required("super_admin", "college_admin", "hod", "faculty")
def evaluate_submission(submission_id):
    try:
        marks_value = float(request.form.get("marks", ""))
    except ValueError:
        flash("Enter valid marks.", "error")
        return redirect(url_for("college.assignments"))
    with connect() as connection:
        submission = connection.execute(
            """SELECT sub.*,a.max_marks,s.faculty_id,s.department_id
               FROM submissions sub JOIN assignments a ON a.id=sub.assignment_id
               JOIN subjects s ON s.id=a.subject_id WHERE sub.id=?""",
            (submission_id,),
        ).fetchone()
        if not submission:
            abort(404)
        if g.user["role"] == "faculty" and submission["faculty_id"] != g.user["professor_id"]:
            abort(403)
        if g.user["role"] == "hod" and submission["department_id"] != g.user["department_id"]:
            abort(403)
        if not math.isfinite(marks_value) or marks_value < 0 or marks_value > submission["max_marks"]:
            flash("Marks must be within the assignment maximum.", "error")
            return redirect(url_for("college.assignments"))
        connection.execute(
            "UPDATE submissions SET marks=?,feedback=?,status='Evaluated',evaluated_by=? WHERE id=?",
            (marks_value,request.form.get("feedback","").strip(),g.user["id"],submission_id),
        )
        log_audit(g.user["id"], "evaluate", "submission", submission_id, str(marks_value))
        flash("Submission evaluated.", "success")
    return redirect(url_for("college.assignments"))


@bp.post("/academics/submissions/<int:submission_id>/start-review")
@roles_required("super_admin", "college_admin", "hod", "faculty")
def start_submission_review(submission_id):
    with connect() as connection:
        submission = connection.execute(
            """SELECT sub.status,s.faculty_id,s.department_id FROM submissions sub
               JOIN assignments a ON a.id=sub.assignment_id
               JOIN subjects s ON s.id=a.subject_id WHERE sub.id=?""",
            (submission_id,),
        ).fetchone()
        if not submission:
            abort(404)
        if g.user["role"] == "faculty" and submission["faculty_id"] != g.user["professor_id"]:
            abort(403)
        if g.user["role"] == "hod" and submission["department_id"] != g.user["department_id"]:
            abort(403)
        if submission["status"] not in {"Submitted", "Late Submission"}:
            flash("Only unreviewed submissions can enter evaluation.", "error")
            return redirect(url_for("college.assignments"))
        connection.execute(
            "UPDATE submissions SET status='Under Evaluation' WHERE id=?",
            (submission_id,),
        )
        log_audit(g.user["id"], "start_review", "submission", submission_id)
    flash("Submission moved to evaluation.", "success")
    return redirect(url_for("college.assignments"))


@bp.route("/academics/attendance", methods=["GET", "POST"])
@roles_required("super_admin", "college_admin", "hod", "faculty", "student")
def attendance():
    if request.method == "POST":
        if g.user["role"] == "student":
            abort(403)
        student_id = request.form.get("student_id", type=int)
        subject_id = request.form.get("subject_id", type=int)
        attendance_date = request.form.get("attendance_date", "")
        status = request.form.get("status", "")
        period = request.form.get("period", type=int) or 1
        if status not in {"Present", "Absent", "On Duty"} or not student_id or not subject_id or not 1 <= period <= 16:
            flash("Choose a student, subject, and valid attendance status.", "error")
            return redirect(url_for("college.attendance"))
        try:
            date.fromisoformat(attendance_date)
        except ValueError:
            flash("Enter a valid attendance date.", "error")
            return redirect(url_for("college.attendance"))
        with connect() as connection:
            _subject_scope(connection, subject_id)
            if not connection.execute(
                "SELECT 1 FROM subject_enrollments WHERE student_id=? AND subject_id=?",
                (student_id,subject_id),
            ).fetchone():
                abort(403)
            cursor = connection.execute(
                """INSERT INTO attendance (student_id,subject_id,attendance_date,period,status,marked_by)
                   VALUES (?,?,?,?,?,?) ON CONFLICT(student_id,subject_id,attendance_date,period)
                   DO UPDATE SET status=excluded.status,marked_by=excluded.marked_by""",
                (student_id,subject_id,attendance_date,period,status,g.user["id"]),
            )
            refresh_record_facts(connection)
            log_audit(g.user["id"], "mark", "attendance", cursor.lastrowid, status)
        flash("Attendance saved.", "success")
        return redirect(url_for("college.attendance"))
    with connect() as connection:
        options = _scope_options(connection, _options(connection))
        threshold = connection.execute(
            "SELECT setting_value FROM app_settings WHERE setting_key='attendance_threshold'"
        ).fetchone()[0]
        conditions, params = [], []
        if g.user["role"] == "student":
            conditions.append("a.student_id=?")
            params.append(g.user["student_id"])
        if g.user["role"] == "faculty":
            conditions.append("su.faculty_id=?")
            params.append(g.user["professor_id"])
        if g.user["role"] == "hod":
            conditions.append("su.department_id=?")
            params.append(g.user["department_id"])
        where = " WHERE " + " AND ".join(conditions) if conditions else ""
        rows = [dict(row) for row in connection.execute(
            """SELECT a.*,st.name AS student_name,st.registration_no,su.code AS subject_code
               FROM attendance a JOIN students st ON st.id=a.student_id
               JOIN subjects su ON su.id=a.subject_id""" + where + " ORDER BY a.attendance_date DESC,st.name",
            params,
        )]
        rows = _with_attendance_percentages(connection, rows)
    return render_template("college_module.html", module="attendance", options=options, rows=rows, threshold=threshold)


def _with_attendance_percentages(connection, rows):
    for row in rows:
        stats = connection.execute(
            """SELECT COUNT(*) AS conducted,
                      SUM(CASE WHEN status IN ('Present','On Duty') THEN 1 ELSE 0 END) AS attended
               FROM attendance WHERE student_id=? AND subject_id=?""",
            (row["student_id"],row["subject_id"]),
        ).fetchone()
        row["percentage"] = round(100 * (stats["attended"] or 0) / stats["conducted"], 1) if stats["conducted"] else 0
    return rows


@bp.post("/academics/attendance/threshold")
@roles_required("super_admin", "college_admin")
def set_attendance_threshold():
    try:
        threshold = float(request.form.get("threshold", ""))
        if not 0 < threshold <= 100:
            raise ValueError
    except ValueError:
        flash("Attendance threshold must be between 0 and 100.", "error")
        return redirect(url_for("college.attendance"))
    with connect() as connection:
        connection.execute(
            "UPDATE app_settings SET setting_value=? WHERE setting_key='attendance_threshold'",
            (str(threshold),),
        )
        log_audit(g.user["id"], "update", "setting", "attendance_threshold", str(threshold))
    flash("Attendance threshold updated.", "success")
    return redirect(url_for("college.attendance"))


@bp.route("/academics/faculty-attendance", methods=["GET", "POST"])
@roles_required("super_admin", "college_admin", "hod")
def faculty_attendance():
    if request.method == "POST":
        professor_id = request.form.get("professor_id", type=int)
        attendance_date = request.form.get("attendance_date", "")
        status = request.form.get("status", "")
        if not professor_id or status not in {"Present", "Absent", "On Duty", "Leave"}:
            flash("Select a faculty member and valid attendance status.", "error")
            return redirect(url_for("college.faculty_attendance"))
        with connect() as connection:
            if not connection.execute("SELECT 1 FROM professors WHERE id=?", (professor_id,)).fetchone():
                flash("Choose an existing faculty member.", "error")
                return redirect(url_for("college.faculty_attendance"))
            if g.user["role"] == "hod" and not connection.execute(
                """SELECT 1 FROM department_memberships
                   WHERE professor_id=? AND department_id=?""",
                (professor_id,g.user["department_id"]),
            ).fetchone():
                abort(403)
            check_in, check_out = request.form.get("check_in", ""), request.form.get("check_out", "")
            try:
                date.fromisoformat(attendance_date)
                if bool(check_in) != bool(check_out):
                    raise ValueError
                if check_in and check_out:
                    start_time = datetime.strptime(check_in, "%H:%M")
                    end_time = datetime.strptime(check_out, "%H:%M")
                    if end_time <= start_time:
                        raise ValueError
            except ValueError:
                flash("Enter a valid date and check-out time later than check-in.", "error")
                return redirect(url_for("college.faculty_attendance"))
            connection.execute(
                """INSERT INTO faculty_attendance (professor_id,attendance_date,check_in,check_out,status,leave_note)
                   VALUES (?,?,?,?,?,?) ON CONFLICT(professor_id,attendance_date) DO UPDATE SET
                   check_in=excluded.check_in,check_out=excluded.check_out,status=excluded.status,
                   leave_note=excluded.leave_note""",
                (
                    professor_id, attendance_date, request.form.get("check_in") or None,
                    request.form.get("check_out") or None, status, request.form.get("leave_note","").strip(),
                ),
            )
        flash("Faculty attendance saved.", "success")
        return redirect(url_for("college.faculty_attendance"))
    with connect() as connection:
        options = _scope_options(connection, _options(connection))
        conditions, params = [], []
        if g.user["role"] == "hod":
            conditions.append("m.department_id=?")
            params.append(g.user["department_id"])
        where = " WHERE " + " AND ".join(conditions) if conditions else ""
        if g.user["role"] == "hod":
            options["professors"] = [row for row in options["professors"] if connection.execute(
                "SELECT 1 FROM department_memberships WHERE professor_id=? AND department_id=?",
                (row["id"],g.user["department_id"]),
            ).fetchone()]
        rows = [dict(row) for row in connection.execute(
            """SELECT fa.*,p.name AS professor_name,p.employee_id,
                      CASE WHEN check_in IS NOT NULL AND check_out IS NOT NULL
                           THEN ROUND((julianday('2000-01-01 '||check_out)-julianday('2000-01-01 '||check_in))*24,2)
                           ELSE NULL END AS hours
               FROM faculty_attendance fa JOIN professors p ON p.id=fa.professor_id
               LEFT JOIN department_memberships m ON m.professor_id=p.id"""
            + where + " ORDER BY fa.attendance_date DESC",
            params,
        )]
    return render_template("college_module.html", module="faculty_attendance", options=options, rows=rows)


@bp.route("/finance/fees", methods=["GET", "POST"])
@roles_required("super_admin", "college_admin", "finance", "student")
def fees():
    if request.method == "POST":
        student_id = request.form.get("student_id", type=int)
        semester = request.form.get("semester", type=int)
        academic_year = request.form.get("academic_year", "").strip()
        due_date = request.form.get("due_date", "")
        columns = ("tuition","examination","laboratory","library","transport","hostel","other","concession")
        try:
            values = [float(request.form.get(name, "0") or 0) for name in columns]
            if (
                not student_id or not semester or not academic_year
                or not 1 <= semester <= 12
                or any(not math.isfinite(value) or value < 0 for value in values)
                or values[-1] > sum(values[:-1])
            ):
                raise ValueError
            date.fromisoformat(due_date)
        except ValueError:
            flash("Provide a student, semester, academic year, valid due date, and non-negative fee amounts.", "error")
            return redirect(url_for("college.fees"))
        with connect() as connection:
            if g.user["role"] == "student":
                abort(403)
            if not connection.execute("SELECT 1 FROM students WHERE id=?", (student_id,)).fetchone():
                flash("Choose an existing student.", "error")
                return redirect(url_for("college.fees"))
            columns_sql = ",".join(columns)
            placeholders = ",".join("?" for _ in columns)
            connection.execute(
                f"INSERT INTO fees (student_id,semester,academic_year,due_date,{columns_sql},created_by) "
                f"VALUES (?,?,?, ?,{placeholders},?) ON CONFLICT(student_id,semester,academic_year) DO UPDATE SET "
                + ",".join(f"{name}=excluded.{name}" for name in columns)
                + ",due_date=excluded.due_date",
                (student_id,semester,academic_year,due_date,*values,g.user["id"]),
            )
            refresh_record_facts(connection)
            log_audit(g.user["id"], "upsert", "fees", student_id, f"{academic_year} semester {semester}")
        flash("Fee structure saved.", "success")
        return redirect(url_for("college.fees"))
    with connect() as connection:
        options = _options(connection)
        clauses, params = [], []
        if g.user["role"] == "student":
            clauses.append("f.student_id=?")
            params.append(g.user["student_id"])
        if g.user["role"] == "hod":
            clauses.append("st.department_id=?")
            params.append(g.user["department_id"])
        where = " WHERE " + " AND ".join(clauses) if clauses else ""
        rows = [dict(row) for row in connection.execute(
            """SELECT f.*,st.name AS student_name,st.registration_no,d.code AS department_code,
                      COALESCE(SUM(CASE WHEN p.status='Recorded' THEN p.amount ELSE 0 END),0) AS paid,
                      (f.tuition+f.examination+f.laboratory+f.library+f.transport+f.hostel+f.other-f.concession)
                      - COALESCE(SUM(CASE WHEN p.status='Recorded' THEN p.amount ELSE 0 END),0) AS balance
               FROM fees f JOIN students st ON st.id=f.student_id
               LEFT JOIN departments d ON d.id=st.department_id LEFT JOIN payments p ON p.fee_id=f.id"""
            + where + " GROUP BY f.id ORDER BY f.due_date",
            params,
        )]
        for row in rows:
            row["total"] = sum(row[field] or 0 for field in ("tuition","examination","laboratory","library","transport","hostel","other")) - (row["concession"] or 0)
            row["status_label"] = "Waived" if row["total"] <= 0 else "Paid" if row["balance"] <= 0 else "Overdue" if row["due_date"] < date.today().isoformat() else "Partially Paid" if row["paid"] > 0 else "Pending"
    return render_template("college_module.html", module="fees", options=options, rows=rows)


@bp.route("/finance/payments", methods=["GET", "POST"])
@roles_required("super_admin", "college_admin", "finance", "student")
def payments():
    if request.method == "POST":
        if g.user["role"] == "student":
            abort(403)
        fee_id = request.form.get("fee_id", type=int)
        method = request.form.get("payment_method", "")
        reference = request.form.get("reference_number", "").strip()
        try:
            amount = float(request.form.get("amount", ""))
            date.fromisoformat(request.form.get("payment_date", ""))
            if not math.isfinite(amount) or amount <= 0 or method not in {"Cash","UPI","Bank Transfer","Card","Online Payment"}:
                raise ValueError
        except ValueError:
            flash("Enter a positive amount, valid payment method, and date.", "error")
            return redirect(url_for("college.payments"))
        status = "Pending Verification" if method == "Online Payment" else "Recorded"
        with connect() as connection:
            fee, total, paid, balance = _fee_totals(connection, fee_id)
            if amount > balance:
                flash("Payment is greater than the outstanding fee balance.", "error")
                return redirect(url_for("college.payments"))
            transaction_id = "CL-" + secrets.token_hex(8).upper()
            cursor = connection.execute(
                """INSERT INTO payments (transaction_id,fee_id,amount,payment_method,reference_number,payment_date,status,recorded_by)
                   VALUES (?,?,?,?,?,?,?,?)""",
                (transaction_id,fee_id,amount,method,reference,request.form["payment_date"],status,g.user["id"]),
            )
            refresh_record_facts(connection)
            log_audit(g.user["id"], "record", "payment", cursor.lastrowid, f"{transaction_id}:{status}")
        if status == "Pending Verification":
            flash("Online payment is pending gateway verification; it has not been credited.", "success")
        else:
            flash("Manual finance payment recorded.", "success")
        return redirect(url_for("college.payments"))
    with connect() as connection:
        options = _options(connection)
        fees_rows = [dict(row) for row in connection.execute(
            """SELECT f.id,st.name AS student_name,st.registration_no,f.semester,f.academic_year,
                      (f.tuition+f.examination+f.laboratory+f.library+f.transport+f.hostel+f.other-f.concession)
                      - COALESCE(SUM(CASE WHEN p.status='Recorded' THEN p.amount ELSE 0 END),0) AS balance
               FROM fees f JOIN students st ON st.id=f.student_id
               LEFT JOIN payments p ON p.fee_id=f.id
               GROUP BY f.id HAVING balance>0 ORDER BY st.name"""
        )]
        clauses, params = [], []
        if g.user["role"] == "student":
            clauses.append("f.student_id=?")
            params.append(g.user["student_id"])
        if g.user["role"] == "hod":
            clauses.append("st.department_id=?")
            params.append(g.user["department_id"])
        where = " WHERE " + " AND ".join(clauses) if clauses else ""
        rows = [dict(row) for row in connection.execute(
            """SELECT p.*,st.name AS student_name,st.registration_no,f.semester,f.academic_year
               FROM payments p JOIN fees f ON f.id=p.fee_id JOIN students st ON st.id=f.student_id"""
            + where + " ORDER BY p.payment_date DESC",
            params,
        )]
    return render_template("college_module.html", module="payments", options=options, rows=rows, fee_options=fees_rows)


@bp.post("/finance/payments/<int:payment_id>/verify")
@roles_required("super_admin", "college_admin", "finance")
def verify_payment(payment_id):
    with connect() as connection:
        payment = connection.execute(
            "SELECT id,fee_id,amount,status FROM payments WHERE id=?", (payment_id,)
        ).fetchone()
        if not payment:
            abort(404)
        if payment["status"] != "Pending Verification":
            flash("Only pending online payments can be verified.", "error")
            return redirect(url_for("college.payments"))
        _, _, _, balance = _fee_totals(connection, payment["fee_id"])
        if payment["amount"] > balance:
            flash("The payment exceeds the remaining fee balance and cannot be verified.", "error")
            return redirect(url_for("college.payments"))
        connection.execute("UPDATE payments SET status='Recorded' WHERE id=?", (payment_id,))
        refresh_record_facts(connection)
        log_audit(g.user["id"], "verify", "payment", payment_id)
    flash("Payment verified and credited to the fee balance.", "success")
    return redirect(url_for("college.payments"))


@bp.post("/finance/payments/<int:payment_id>/reject")
@roles_required("super_admin", "college_admin", "finance")
def reject_payment(payment_id):
    with connect() as connection:
        payment = connection.execute(
            "SELECT status FROM payments WHERE id=?", (payment_id,)
        ).fetchone()
        if not payment:
            abort(404)
        if payment["status"] != "Pending Verification":
            flash("Only pending online payments can be rejected.", "error")
            return redirect(url_for("college.payments"))
        connection.execute("UPDATE payments SET status='Failed' WHERE id=?", (payment_id,))
        refresh_record_facts(connection)
        log_audit(g.user["id"], "reject", "payment", payment_id)
    flash("Payment marked as failed.", "success")
    return redirect(url_for("college.payments"))


@bp.route("/announcements", methods=["GET", "POST"])
@roles_required("super_admin", "college_admin", "hod", "faculty", "student", "finance")
def announcements():
    if request.method == "POST":
        if g.user["role"] not in ADMIN_ROLES | {"hod", "faculty"}:
            abort(403)
        title = request.form.get("title", "").strip()
        description = request.form.get("description", "").strip()
        target_audience = request.form.get("target_audience", "")
        category = request.form.get("category", "General")
        priority = request.form.get("priority", "Normal")
        if (
            not title or not description
            or target_audience not in {
                "Everyone", "All students", "All faculty", "Department", "Semester",
                "Individual student", "Individual faculty",
            }
            or category not in {"General", "Academic", "Event", "Examination"}
            or priority not in {"Low", "Normal", "High", "Urgent"}
        ):
            flash("Provide valid announcement details, audience, category, and priority.", "error")
            return redirect(url_for("college.announcements"))
        if g.user["role"] == "hod" and target_audience not in {
            "Department", "Semester", "Individual student", "Individual faculty",
        }:
            abort(403)
        department_id = request.form.get("department_id", type=int) or None
        if g.user["role"] == "hod":
            department_id = g.user["department_id"]
        semester = request.form.get("semester", type=int)
        target_user_id = request.form.get("target_user_id", type=int)
        expires_at = request.form.get("expires_at") or None
        if target_audience == "Semester" and (semester is None or not 1 <= semester <= 12):
            flash("Choose a valid semester for a semester-targeted notice.", "error")
            return redirect(url_for("college.announcements"))
        if target_audience == "Department" and not department_id:
            flash("Choose a department for a department-targeted notice.", "error")
            return redirect(url_for("college.announcements"))
        if target_audience in {"Individual student", "Individual faculty"}:
            expected_role = "student" if target_audience == "Individual student" else "faculty"
            if not target_user_id:
                flash("Choose an individual account for this announcement.", "error")
                return redirect(url_for("college.announcements"))
        else:
            target_user_id = None
            expected_role = None
        if expires_at:
            try:
                date.fromisoformat(expires_at)
            except ValueError:
                flash("Enter a valid announcement expiry date.", "error")
                return redirect(url_for("college.announcements"))
        with connect() as connection:
            if department_id and not connection.execute(
                "SELECT 1 FROM departments WHERE id=?", (department_id,)
            ).fetchone():
                flash("Choose an existing department.", "error")
                return redirect(url_for("college.announcements"))
            if expected_role:
                target = connection.execute(
                    "SELECT role,student_id,professor_id FROM users WHERE id=? AND active=1",
                    (target_user_id,),
                ).fetchone()
                if not target or target["role"] != expected_role:
                    flash("The selected individual account does not match the audience.", "error")
                    return redirect(url_for("college.announcements"))
                if g.user["role"] == "hod":
                    if expected_role == "student":
                        in_department = connection.execute(
                            "SELECT 1 FROM students WHERE id=? AND department_id=?",
                            (target["student_id"],g.user["department_id"]),
                        ).fetchone()
                    else:
                        in_department = connection.execute(
                            """SELECT 1 FROM department_memberships
                               WHERE professor_id=? AND department_id=?""",
                            (target["professor_id"],g.user["department_id"]),
                        ).fetchone()
                    if not in_department:
                        abort(403)
            if g.user["role"] == "faculty":
                if target_audience not in {"Individual student", "All students"}:
                    abort(403)
                if target_audience == "Individual student" and not connection.execute(
                    """SELECT 1 FROM subject_enrollments se JOIN subjects s ON s.id=se.subject_id
                       JOIN users u ON u.student_id=se.student_id
                       WHERE u.id=? AND s.faculty_id=? LIMIT 1""",
                    (target_user_id,g.user["professor_id"]),
                ).fetchone():
                    abort(403)
            try:
                attachment_name = save_secure_upload(request.files.get("attachment"))
            except ValueError as exc:
                flash(str(exc), "error")
                return redirect(url_for("college.announcements"))
            cursor = connection.execute(
                """INSERT INTO notifications (title,description,category,target_audience,department_id,
                   semester,priority,expires_at,posted_by,target_user_id,attachment_name)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    title,description,category,target_audience,department_id,
                    semester,priority,expires_at,g.user["id"],target_user_id,attachment_name,
                ),
            )
            log_audit(g.user["id"], "publish", "notification", cursor.lastrowid, title)
        flash("Announcement published.", "success")
        return redirect(url_for("college.announcements"))
    with connect() as connection:
        options = _options(connection)
        user = g.user
        if user["role"] == "student":
            student = connection.execute("SELECT department_id,semester FROM students WHERE id=?", (user["student_id"],)).fetchone()
            department_id = student["department_id"] if student else None
            semester = student["semester"] if student else None
            audience = "student"
        elif user["role"] in {"faculty", "hod"}:
            department_id, semester, audience = user["department_id"], None, "faculty"
            if user["role"] == "faculty" and not department_id:
                membership = connection.execute(
                    """SELECT department_id FROM department_memberships
                       WHERE professor_id=? ORDER BY department_id LIMIT 1""",
                    (user["professor_id"],),
                ).fetchone()
                department_id = membership["department_id"] if membership else None
        else:
            department_id, semester, audience = user["department_id"], None, user["role"]
        rows = [dict(row) for row in connection.execute(
            """SELECT n.*,u.username AS posted_by,
                      CASE WHEN nr.user_id IS NULL THEN 0 ELSE 1 END AS is_read
               FROM notifications n LEFT JOIN users u ON u.id=n.posted_by
               LEFT JOIN notification_reads nr ON nr.notification_id=n.id AND nr.user_id=?
               WHERE (n.expires_at IS NULL OR n.expires_at>=date('now'))
                 AND (n.target_audience IN ('Everyone',?) OR
                      (n.target_audience='All students' AND ?='student') OR
                      (n.target_audience='All faculty' AND ? IN ('faculty','hod')) OR
                      (n.target_audience='Department' AND n.department_id=?) OR
                      (n.target_audience='Semester' AND n.semester=? AND
                       (n.department_id IS NULL OR n.department_id=?)) OR
                      (n.target_audience='Individual student' AND ?=n.target_user_id AND ?='student') OR
                      (n.target_audience='Individual faculty' AND ?=n.target_user_id AND ?='faculty'))
               ORDER BY CASE n.priority WHEN 'Urgent' THEN 0 WHEN 'High' THEN 1 ELSE 2 END,n.published_at DESC""",
            (
                user["id"], audience, audience, audience, department_id, semester, department_id,
                user["id"], audience, user["id"], audience,
            ),
        )]
        if user["role"] == "hod":
            options["departments"] = [
                row for row in options["departments"] if row["id"] == user["department_id"]
            ]
            options["accounts"] = [
                row for row in options["accounts"]
                if (
                    row["role"] == "student" and connection.execute(
                        "SELECT 1 FROM students WHERE id=? AND department_id=?",
                        (row["student_id"],user["department_id"]),
                    ).fetchone()
                ) or (
                    row["role"] == "faculty" and connection.execute(
                        "SELECT 1 FROM department_memberships WHERE professor_id=? AND department_id=?",
                        (row["professor_id"],user["department_id"]),
                    ).fetchone()
                )
            ]
        elif user["role"] == "faculty":
            options["accounts"] = [
                row for row in options["accounts"]
                if row["role"] == "student" and connection.execute(
                    """SELECT 1 FROM subject_enrollments se JOIN subjects s ON s.id=se.subject_id
                       WHERE se.student_id=? AND s.faculty_id=? LIMIT 1""",
                    (row["student_id"],user["professor_id"]),
                ).fetchone()
            ]
    return render_template("college_module.html", module="announcements", options=options, rows=rows)


@bp.get("/announcements/<int:notification_id>/attachment")
@roles_required("super_admin", "college_admin", "hod", "faculty", "student", "finance")
def download_announcement_attachment(notification_id):
    with connect() as connection:
        user = g.user
        if user["role"] == "student":
            student = connection.execute(
                "SELECT department_id,semester FROM students WHERE id=?",
                (user["student_id"],),
            ).fetchone()
            department_id = student["department_id"] if student else None
            semester = student["semester"] if student else None
            audience = "student"
        elif user["role"] in {"faculty", "hod"}:
            department_id = user["department_id"]
            if user["role"] == "faculty" and not department_id:
                membership = connection.execute(
                    "SELECT department_id FROM department_memberships WHERE professor_id=? ORDER BY department_id LIMIT 1",
                    (user["professor_id"],),
                ).fetchone()
                department_id = membership["department_id"] if membership else None
            semester, audience = None, "faculty"
        else:
            department_id, semester, audience = user["department_id"], None, user["role"]
        row = connection.execute(
            """SELECT attachment_name FROM notifications n
               WHERE n.id=? AND (n.expires_at IS NULL OR n.expires_at>=date('now'))
                 AND (n.target_audience IN ('Everyone',?) OR
                      (n.target_audience='All students' AND ?='student') OR
                      (n.target_audience='All faculty' AND ?='faculty') OR
                      (n.target_audience='Department' AND
                       (n.department_id=? OR (?='faculty' AND EXISTS (
                          SELECT 1 FROM department_memberships m
                          WHERE m.department_id=n.department_id AND m.professor_id=?)))) OR
                      (n.target_audience='Semester' AND n.semester=? AND
                       (n.department_id IS NULL OR n.department_id=?)) OR
                      (n.target_audience='Individual student' AND ?=n.target_user_id AND ?='student') OR
                      (n.target_audience='Individual faculty' AND ?=n.target_user_id AND ?='faculty'))""",
            (
                notification_id,audience,audience,audience,department_id,audience,
                user.get("professor_id"),semester,department_id,user.get("id"),audience,
                user.get("id"),audience,
            ),
        ).fetchone()
        if not row or not row["attachment_name"]:
            abort(404)
        name = secure_filename(row["attachment_name"])
        path = (UPLOAD_FOLDER / name).resolve()
        if path.parent != UPLOAD_FOLDER.resolve() or not path.is_file():
            abort(404)
    return send_file(path, as_attachment=True, download_name=name)


@bp.post("/announcements/<int:notification_id>/read")
@roles_required("super_admin", "college_admin", "hod", "faculty", "student", "finance")
def mark_notification_read(notification_id):
    with connect() as connection:
        if not connection.execute("SELECT 1 FROM notifications WHERE id=?", (notification_id,)).fetchone():
            abort(404)
        connection.execute(
            "INSERT OR IGNORE INTO notification_reads (notification_id,user_id) VALUES (?,?)",
            (notification_id,g.user["id"]),
        )
    return redirect(url_for("college.announcements"))


@bp.route("/academics/timetable", methods=["GET", "POST"])
@roles_required("super_admin", "college_admin", "hod", "faculty", "student")
def timetable():
    if request.method == "POST":
        if g.user["role"] not in ADMIN_ROLES | {"hod"}:
            abort(403)
        subject_id = request.form.get("subject_id", type=int)
        section = request.form.get("section", "").strip()
        day = request.form.get("day_of_week", type=int)
        period = request.form.get("period", type=int)
        start, end = request.form.get("start_time",""), request.form.get("end_time","")
        classroom = request.form.get("classroom", "").strip()
        faculty_id = request.form.get("faculty_id", type=int) or None
        if not all((subject_id,section,period,start,end,classroom)) or day is None:
            flash("Complete all timetable fields.", "error")
            return redirect(url_for("college.timetable"))
        try:
            parsed_start = datetime.strptime(start, "%H:%M")
            parsed_end = datetime.strptime(end, "%H:%M")
            if not 0 <= day <= 6 or not 1 <= period <= 16 or parsed_end <= parsed_start:
                raise ValueError
        except ValueError:
            flash("Use a valid weekday, period, and end time later than start time.", "error")
            return redirect(url_for("college.timetable"))
        with connect() as connection:
            subject = _subject_scope(connection, subject_id)
            if g.user["role"] == "hod" and subject["department_id"] != g.user["department_id"]:
                abort(403)
            faculty_id = faculty_id or subject["faculty_id"]
            if faculty_id and not connection.execute(
                """SELECT 1 FROM department_memberships
                   WHERE professor_id=? AND department_id=?""",
                (faculty_id,subject["department_id"]),
            ).fetchone():
                abort(400, description="The selected faculty member does not belong to this department.")
            conflict = connection.execute(
                """SELECT 1 FROM timetable t JOIN subjects s ON s.id=t.subject_id
                   WHERE t.day_of_week=? AND t.start_time<? AND t.end_time>? AND
                     (t.classroom=? OR
                      (t.section=? AND s.department_id=? AND s.semester=?) OR
                      (? IS NOT NULL AND t.faculty_id=?)) LIMIT 1""",
                (
                    day,end,start,classroom,section,subject["department_id"],subject["semester"],
                    faculty_id,faculty_id,
                ),
            ).fetchone()
            if conflict:
                flash("Timetable conflict: the period overlaps for this classroom, faculty member, or section.", "error")
                return redirect(url_for("college.timetable"))
            connection.execute(
                """INSERT INTO timetable (subject_id,section,day_of_week,period,start_time,end_time,classroom,faculty_id)
                   VALUES (?,?,?,?,?,?,?,?)""",
                (subject_id,section,day,period,start,end,classroom,faculty_id),
            )
            log_audit(g.user["id"], "create", "timetable", subject_id, f"{day}:{period}")
        flash("Timetable entry added.", "success")
        return redirect(url_for("college.timetable"))
    with connect() as connection:
        options = _scope_options(connection, _options(connection))
        conditions, params = [], []
        if g.user["role"] == "student":
            student = connection.execute(
                "SELECT department_id,section,semester FROM students WHERE id=?",
                (g.user["student_id"],),
            ).fetchone()
            if student:
                conditions.append("s.department_id=? AND t.section=? AND s.semester=?")
                params.extend((student["department_id"],student["section"] or "",student["semester"]))
        elif g.user["role"] == "faculty":
            conditions.append("t.faculty_id=?")
            params.append(g.user["professor_id"])
        elif g.user["role"] == "hod":
            conditions.append("s.department_id=?")
            params.append(g.user["department_id"])
        where = " WHERE " + " AND ".join(conditions) if conditions else ""
        rows = [dict(row) for row in connection.execute(
            """SELECT t.*,s.code AS subject_code,s.name AS subject_name,d.code AS department_code,
                      p.name AS faculty_name
               FROM timetable t JOIN subjects s ON s.id=t.subject_id
               JOIN departments d ON d.id=s.department_id LEFT JOIN professors p ON p.id=t.faculty_id"""
            + where + " ORDER BY t.day_of_week,t.period",
            params,
        )]
    return render_template("college_module.html", module="timetable", options=options, rows=rows)


@bp.route("/academics/exams", methods=["GET", "POST"])
@roles_required("super_admin", "college_admin", "hod", "faculty", "student")
def exams():
    if request.method == "POST":
        if g.user["role"] not in ADMIN_ROLES | {"hod"}:
            abort(403)
        subject_id = request.form.get("subject_id", type=int)
        exam_type = request.form.get("exam_type", "")
        exam_date = request.form.get("exam_date", "")
        start, end = request.form.get("start_time",""),request.form.get("end_time","")
        hall = request.form.get("hall","").strip()
        if not subject_id or exam_type not in {"Internal","External"} or not all((exam_date,start,end,hall)):
            flash("Complete all examination fields.", "error")
            return redirect(url_for("college.exams"))
        try:
            date.fromisoformat(exam_date)
            parsed_start = datetime.strptime(start, "%H:%M")
            parsed_end = datetime.strptime(end, "%H:%M")
            if parsed_end <= parsed_start:
                raise ValueError
        except ValueError:
            flash("Enter a valid date and an end time later than the start time.", "error")
            return redirect(url_for("college.exams"))
        with connect() as connection:
            subject = _subject_scope(connection, subject_id)
            if g.user["role"] == "hod" and subject["department_id"] != g.user["department_id"]:
                abort(403)
            conflict = connection.execute(
                """SELECT 1 FROM exams e JOIN subjects s ON s.id=e.subject_id
                   WHERE e.exam_date=? AND e.start_time<? AND e.end_time>?
                   AND (e.hall=? OR (s.department_id=? AND s.semester=?)) LIMIT 1""",
                (exam_date,end,start,hall,subject["department_id"],subject["semester"]),
            ).fetchone()
            if conflict:
                flash("Examination conflicts with a scheduled exam in the same hall or department.", "error")
                return redirect(url_for("college.exams"))
            connection.execute(
                """INSERT INTO exams (subject_id,exam_type,exam_date,start_time,end_time,hall,created_by)
                   VALUES (?,?,?,?,?,?,?)""",
                (subject_id,exam_type,exam_date,start,end,hall,g.user["id"]),
            )
            log_audit(g.user["id"], "create", "exam", subject_id, exam_date)
        flash("Examination scheduled.", "success")
        return redirect(url_for("college.exams"))
    with connect() as connection:
        options = _scope_options(connection, _options(connection))
        conditions, params = [], []
        if g.user["role"] == "student":
            student = connection.execute("SELECT department_id FROM students WHERE id=?", (g.user["student_id"],)).fetchone()
            if student:
                conditions.append("s.department_id=?")
                params.append(student["department_id"])
        elif g.user["role"] == "faculty":
            conditions.append("s.faculty_id=?")
            params.append(g.user["professor_id"])
        elif g.user["role"] == "hod":
            conditions.append("s.department_id=?")
            params.append(g.user["department_id"])
        where = " WHERE " + " AND ".join(conditions) if conditions else ""
        rows = [dict(row) for row in connection.execute(
            """SELECT e.*,s.code AS subject_code,s.name AS subject_name,d.code AS department_code
               FROM exams e JOIN subjects s ON s.id=e.subject_id JOIN departments d ON d.id=s.department_id
               """ + where + " ORDER BY e.exam_date,e.start_time",
            params,
        )]
    return render_template("college_module.html", module="exams", options=options, rows=rows)


@bp.get("/student")
@roles_required("student")
def student_portal():
    student_id = g.user["student_id"]
    with connect() as connection:
        student = connection.execute(
            """SELECT s.*,d.code AS department_code,d.name AS department_name
               FROM students s LEFT JOIN departments d ON d.id=s.department_id WHERE s.id=?""",
            (student_id,),
        ).fetchone()
        if not student:
            abort(404)
        values = {
            "student": dict(student),
            "subject_count": connection.execute(
                "SELECT COUNT(*) FROM subject_enrollments WHERE student_id=?", (student_id,)
            ).fetchone()[0],
            "marks": [dict(row) for row in connection.execute(
                """SELECT m.*,s.code AS subject_code FROM internal_marks m JOIN subjects s ON s.id=m.subject_id
                   WHERE m.student_id=? ORDER BY s.code,m.assessment""", (student_id,)
            )],
            "attendance": [dict(row) for row in connection.execute(
                """SELECT s.code,COUNT(*) AS total,
                          SUM(CASE WHEN a.status IN ('Present','On Duty') THEN 1 ELSE 0 END) AS attended,
                          ROUND(100.0*SUM(CASE WHEN a.status IN ('Present','On Duty') THEN 1 ELSE 0 END)/COUNT(*),1) AS percentage
                   FROM attendance a JOIN subjects s ON s.id=a.subject_id WHERE a.student_id=? GROUP BY s.id""",
                (student_id,),
            )],
            "fees": [dict(row) for row in connection.execute(
                """SELECT f.*,COALESCE(SUM(CASE WHEN p.status='Recorded' THEN p.amount ELSE 0 END),0) AS paid
                   FROM fees f LEFT JOIN payments p ON p.fee_id=f.id WHERE f.student_id=? GROUP BY f.id""",
                (student_id,),
            )],
            "attendance_threshold": float(connection.execute(
                "SELECT setting_value FROM app_settings WHERE setting_key='attendance_threshold'"
            ).fetchone()[0]),
            "pending_assignments": [dict(row) for row in connection.execute(
                """SELECT a.id,a.title,a.deadline,s.code AS subject_code
                   FROM subject_enrollments se JOIN assignments a ON a.subject_id=se.subject_id
                   JOIN subjects s ON s.id=a.subject_id
                   LEFT JOIN submissions sub ON sub.assignment_id=a.id AND sub.student_id=se.student_id
                   WHERE se.student_id=? AND sub.id IS NULL
                   ORDER BY a.deadline LIMIT 6""",
                (student_id,),
            )],
            "upcoming_exams": [dict(row) for row in connection.execute(
                """SELECT e.exam_date,e.exam_type,e.start_time,e.end_time,e.hall,s.code AS subject_code
                   FROM subject_enrollments se JOIN exams e ON e.subject_id=se.subject_id
                   JOIN subjects s ON s.id=e.subject_id
                   WHERE se.student_id=? AND e.exam_date>=date('now')
                   ORDER BY e.exam_date,e.start_time LIMIT 6""",
                (student_id,),
            )],
            "timetable": [dict(row) for row in connection.execute(
                """SELECT t.day_of_week,t.period,t.start_time,t.end_time,t.classroom,
                          s.code AS subject_code,p.name AS faculty_name
                   FROM timetable t JOIN subjects s ON s.id=t.subject_id
                   LEFT JOIN professors p ON p.id=t.faculty_id
                   WHERE s.department_id=? AND s.semester=? AND t.section=?
                   ORDER BY t.day_of_week,t.period""",
                (
                    student["department_id"],student["semester"],
                    student["section"] or "",
                ),
            )] if student["department_id"] and student["semester"] else [],
        }
    attended = sum(row["attended"] or 0 for row in values["attendance"])
    conducted = sum(row["total"] or 0 for row in values["attendance"])
    values["attendance_average"] = round(100 * attended / conducted, 1) if conducted else None
    values["pending_assignment_count"] = len(values["pending_assignments"])
    values["fee_balance"] = 0.0
    for fee in values["fees"]:
        total = sum(
            float(fee[column] or 0)
            for column in ("tuition","examination","laboratory","library","transport","hostel","other")
        ) - float(fee["concession"] or 0)
        fee["balance"] = max(0.0, total - float(fee["paid"] or 0))
        values["fee_balance"] += fee["balance"]
    return render_template("student_portal.html", **values)


@bp.get("/faculty/portal")
@roles_required("faculty")
def faculty_portal():
    professor_id = g.user["professor_id"]
    today = date.today()
    with connect() as connection:
        professor = connection.execute(
            "SELECT * FROM professors WHERE id=?", (professor_id,)
        ).fetchone()
        if not professor:
            abort(404)
        subjects = [dict(row) for row in connection.execute(
            """SELECT s.id,s.code,s.name,s.semester,s.academic_year,d.code AS department_code,
                      (SELECT COUNT(*) FROM subject_enrollments se WHERE se.subject_id=s.id) AS student_count
               FROM subjects s JOIN departments d ON d.id=s.department_id
               WHERE s.faculty_id=? ORDER BY s.semester,s.code""",
            (professor_id,),
        )]
        stats = {
            "subjects": len(subjects),
            "students": connection.execute(
                """SELECT COUNT(DISTINCT se.student_id) FROM subject_enrollments se
                   JOIN subjects s ON s.id=se.subject_id WHERE s.faculty_id=?""",
                (professor_id,),
            ).fetchone()[0],
            "assignments": connection.execute(
                """SELECT COUNT(*) FROM assignments a JOIN subjects s ON s.id=a.subject_id
                   WHERE s.faculty_id=? AND a.deadline>=datetime('now')""",
                (professor_id,),
            ).fetchone()[0],
            "pending_reviews": connection.execute(
                """SELECT COUNT(*) FROM submissions sub JOIN assignments a ON a.id=sub.assignment_id
                   JOIN subjects s ON s.id=a.subject_id
                   WHERE s.faculty_id=? AND sub.status IN ('Submitted','Late Submission','Under Evaluation')""",
                (professor_id,),
            ).fetchone()[0],
        }
        today_classes = [dict(row) for row in connection.execute(
            """SELECT t.period,t.start_time,t.end_time,t.classroom,t.section,
                      s.code AS subject_code,s.name AS subject_name
               FROM timetable t JOIN subjects s ON s.id=t.subject_id
               WHERE t.faculty_id=? AND t.day_of_week=?
               ORDER BY t.period""",
            (professor_id,today.weekday()),
        )]
        assignments = [dict(row) for row in connection.execute(
            """SELECT a.title,a.deadline,s.code AS subject_code,
                      (SELECT COUNT(*) FROM submissions sub
                       WHERE sub.assignment_id=a.id AND sub.status IN
                         ('Submitted','Late Submission','Under Evaluation')) AS pending_count
               FROM assignments a JOIN subjects s ON s.id=a.subject_id
               WHERE s.faculty_id=? AND a.deadline>=datetime('now')
               ORDER BY a.deadline LIMIT 6""",
            (professor_id,),
        )]
        exams = [dict(row) for row in connection.execute(
            """SELECT e.exam_date,e.exam_type,e.start_time,e.end_time,e.hall,s.code AS subject_code
               FROM exams e JOIN subjects s ON s.id=e.subject_id
               WHERE s.faculty_id=? AND e.exam_date>=date('now')
               ORDER BY e.exam_date,e.start_time LIMIT 6""",
            (professor_id,),
        )]
    return render_template(
        "faculty_portal.html",
        professor=dict(professor),
        subjects=subjects,
        stats=stats,
        today_classes=today_classes,
        assignments=assignments,
        exams=exams,
        today=today,
    )


@bp.get("/students/<int:student_id>")
@roles_required("super_admin", "college_admin", "hod", "faculty", "student")
def student_profile(student_id):
    _student_scope(student_id)
    with connect() as connection:
        if g.user["role"] == "hod" and not connection.execute(
            "SELECT 1 FROM students WHERE id=? AND department_id=?",
            (student_id,g.user["department_id"]),
        ).fetchone():
            abort(403)
        if g.user["role"] == "faculty" and not connection.execute(
            """SELECT 1 FROM subject_enrollments se JOIN subjects s ON s.id=se.subject_id
               WHERE se.student_id=? AND s.faculty_id=? LIMIT 1""",
            (student_id,g.user["professor_id"]),
        ).fetchone():
            abort(403)
        student = connection.execute(
            """SELECT s.*,d.code AS department_code,d.name AS department_name,p.name AS class_teacher
               FROM students s LEFT JOIN departments d ON d.id=s.department_id
               LEFT JOIN professors p ON p.id=s.class_teacher_id WHERE s.id=?""",
            (student_id,),
        ).fetchone()
        if not student:
            abort(404)
        profile = dict(student)
        profile["marks"] = [dict(row) for row in connection.execute(
            """SELECT m.*,su.code AS subject_code FROM internal_marks m JOIN subjects su ON su.id=m.subject_id
               WHERE m.student_id=? ORDER BY su.code,m.assessment""", (student_id,)
        )]
        profile["attendance"] = [dict(row) for row in connection.execute(
            """SELECT su.code,COUNT(*) AS conducted,
                      SUM(CASE WHEN a.status IN ('Present','On Duty') THEN 1 ELSE 0 END) AS attended,
                      ROUND(100.0*SUM(CASE WHEN a.status IN ('Present','On Duty') THEN 1 ELSE 0 END)/COUNT(*),1) AS percentage
               FROM attendance a JOIN subjects su ON su.id=a.subject_id
               WHERE a.student_id=? GROUP BY su.id""", (student_id,)
        )]
        profile["subjects"] = [dict(row) for row in connection.execute(
            """SELECT su.code,su.name,su.semester FROM subject_enrollments se
               JOIN subjects su ON su.id=se.subject_id WHERE se.student_id=? ORDER BY su.code""",
            (student_id,),
        )]
    return render_template("student_profile.html", profile=profile)


@bp.get("/departments/<int:department_id>")
@roles_required("super_admin", "college_admin", "hod", "faculty")
def department_profile(department_id):
    with connect() as connection:
        department = connection.execute(
            "SELECT * FROM departments WHERE id=?", (department_id,)
        ).fetchone()
        if not department:
            abort(404)
        if g.user["role"] == "hod" and department_id != g.user["department_id"]:
            abort(403)
        if g.user["role"] == "faculty" and not connection.execute(
            """SELECT 1 FROM department_memberships
               WHERE professor_id=? AND department_id=?""",
            (g.user["professor_id"],department_id),
        ).fetchone():
            abort(403)
        data = dict(department)
        data["student_count"] = connection.execute(
            "SELECT COUNT(*) FROM students WHERE department_id=?", (department_id,)
        ).fetchone()[0]
        data["faculty"] = [dict(row) for row in connection.execute(
            """SELECT p.* FROM professors p JOIN department_memberships m ON m.professor_id=p.id
               WHERE m.department_id=? ORDER BY p.name""", (department_id,)
        )]
        data["students"] = [dict(row) for row in connection.execute(
            "SELECT id,name,registration_no,semester,section,academic_status FROM students WHERE department_id=? ORDER BY name",
            (department_id,),
        )]
        data["subjects"] = [dict(row) for row in connection.execute(
            "SELECT code,name,semester,academic_year,credits FROM subjects WHERE department_id=? ORDER BY semester,code",
            (department_id,),
        )]
        data["hod_account"] = connection.execute(
            "SELECT username FROM users WHERE role='hod' AND department_id=? AND active=1",
            (department_id,),
        ).fetchone()
        data["faculty_count"] = len(data["faculty"])
    return render_template("department_profile.html", department=data)


@bp.get("/faculty/<int:professor_id>")
@roles_required("super_admin", "college_admin", "hod", "faculty")
def faculty_profile(professor_id):
    with connect() as connection:
        professor = connection.execute(
            "SELECT * FROM professors WHERE id=?", (professor_id,)
        ).fetchone()
        if not professor:
            abort(404)
        departments = [dict(row) for row in connection.execute(
            """SELECT d.id,d.code,d.name FROM department_memberships m
               JOIN departments d ON d.id=m.department_id WHERE m.professor_id=?""",
            (professor_id,),
        )]
        if g.user["role"] == "hod" and all(row["id"] != g.user["department_id"] for row in departments):
            abort(403)
        if g.user["role"] == "faculty" and professor_id != g.user["professor_id"]:
            abort(403)
        subjects = [dict(row) for row in connection.execute(
            "SELECT code,name,semester,academic_year FROM subjects WHERE faculty_id=? ORDER BY code",
            (professor_id,),
        )]
        attendance_rows = [dict(row) for row in connection.execute(
            "SELECT attendance_date,check_in,check_out,status,leave_note FROM faculty_attendance WHERE professor_id=? ORDER BY attendance_date DESC LIMIT 50",
            (professor_id,),
        )]
    return render_template(
        "faculty_profile.html",
        professor=dict(professor),
        departments=departments,
        subjects=subjects,
        attendance=attendance_rows,
    )


def save_secure_upload(upload):
    if not upload or not upload.filename:
        return None
    filename = secure_filename(upload.filename)
    suffix = Path(filename).suffix.lower()
    if not filename or suffix not in ALLOWED_UPLOADS:
        raise ValueError("Unsupported upload file type.")
    UPLOAD_FOLDER.mkdir(parents=True, exist_ok=True)
    stored_name = secrets.token_hex(16) + suffix
    upload.save(UPLOAD_FOLDER / stored_name)
    return stored_name


def _save_upload(field_name):
    return save_secure_upload(request.files.get(field_name))


@bp.get("/files/<path:stored_name>")
@roles_required("super_admin", "college_admin", "hod", "faculty", "student")
def download_private_file(stored_name):
    name = secure_filename(stored_name)
    path = (UPLOAD_FOLDER / name).resolve()
    if path.parent != UPLOAD_FOLDER.resolve() or not path.is_file():
        abort(404)
    with connect() as connection:
        assignment = connection.execute(
            """SELECT a.id,s.department_id,s.faculty_id FROM assignments a
               JOIN subjects s ON s.id=a.subject_id WHERE a.document_name=?""",
            (name,),
        ).fetchone()
        submission = connection.execute(
            """SELECT sub.student_id,s.department_id,s.faculty_id FROM submissions sub
               JOIN assignments a ON a.id=sub.assignment_id
               JOIN subjects s ON s.id=a.subject_id WHERE sub.file_name=?""",
            (name,),
        ).fetchone()
        allowed = False
        if assignment:
            allowed = (
                g.user["role"] in ADMIN_ROLES
                or g.user["role"] == "hod" and assignment["department_id"] == g.user["department_id"]
                or g.user["role"] == "faculty" and assignment["faculty_id"] == g.user["professor_id"]
                or g.user["role"] == "student" and connection.execute(
                    "SELECT 1 FROM subject_enrollments WHERE student_id=? AND subject_id=(SELECT subject_id FROM assignments WHERE id=?)",
                    (g.user["student_id"],assignment["id"]),
                ).fetchone() is not None
            )
        elif submission:
            allowed = (
                g.user["role"] in ADMIN_ROLES
                or g.user["role"] == "student" and submission["student_id"] == g.user["student_id"]
                or g.user["role"] == "faculty" and submission["faculty_id"] == g.user["professor_id"]
                or g.user["role"] == "hod" and submission["department_id"] == g.user["department_id"]
            )
        else:
            student = connection.execute(
                "SELECT id,department_id FROM students WHERE photo_path=?", (name,)
            ).fetchone()
            professor = connection.execute(
                "SELECT id FROM professors WHERE photo_path=?", (name,)
            ).fetchone()
            department = connection.execute(
                "SELECT id FROM departments WHERE hod_photo_path=?", (name,)
            ).fetchone()
            if student:
                allowed = (
                    g.user["role"] in ADMIN_ROLES
                    or g.user["role"] == "hod" and student["department_id"] == g.user["department_id"]
                    or g.user["role"] == "student" and student["id"] == g.user["student_id"]
                    or g.user["role"] == "faculty" and connection.execute(
                        """SELECT 1 FROM subject_enrollments se JOIN subjects s ON s.id=se.subject_id
                           WHERE se.student_id=? AND s.faculty_id=? LIMIT 1""",
                        (student["id"],g.user["professor_id"]),
                    ).fetchone() is not None
                )
            elif professor:
                allowed = (
                    g.user["role"] in ADMIN_ROLES
                    or g.user["role"] == "faculty" and professor["id"] == g.user["professor_id"]
                    or g.user["role"] == "hod" and connection.execute(
                        "SELECT 1 FROM department_memberships WHERE professor_id=? AND department_id=?",
                        (professor["id"],g.user["department_id"]),
                    ).fetchone() is not None
                )
            elif department:
                allowed = (
                    g.user["role"] in ADMIN_ROLES
                    or g.user["role"] == "hod" and department["id"] == g.user["department_id"]
                )
    if not allowed:
        abort(404)
    return send_file(path, as_attachment=True, download_name=name)


@bp.get("/reports/<report_type>.<format>")
@roles_required("super_admin", "college_admin", "hod", "faculty", "finance")
def export_report(report_type, format):
    report_queries = {
        "students": ("SELECT name,registration_no,email,department_id,semester,academic_status FROM students ORDER BY name", []),
        "marks": ("SELECT st.name,st.registration_no,s.code,m.assessment,m.marks,m.max_marks FROM internal_marks m JOIN students st ON st.id=m.student_id JOIN subjects s ON s.id=m.subject_id ORDER BY st.name,s.code", []),
        "attendance": ("SELECT st.name,st.registration_no,s.code,a.attendance_date,a.period,a.status FROM attendance a JOIN students st ON st.id=a.student_id JOIN subjects s ON s.id=a.subject_id ORDER BY a.attendance_date", []),
        "payments": ("SELECT p.transaction_id,st.name,st.registration_no,p.payment_date,p.amount,p.payment_method,p.status FROM payments p JOIN fees f ON f.id=p.fee_id JOIN students st ON st.id=f.student_id ORDER BY p.payment_date", []),
        "submissions": ("SELECT st.name,st.registration_no,a.title,s.code,sub.status,sub.marks,sub.submitted_at FROM submissions sub JOIN students st ON st.id=sub.student_id JOIN assignments a ON a.id=sub.assignment_id JOIN subjects s ON s.id=a.subject_id ORDER BY st.name", []),
    }
    if report_type not in report_queries or format not in {"csv", "xlsx", "pdf"}:
        abort(404)
    if g.user["role"] == "finance" and report_type != "payments":
        abort(403)
    if g.user["role"] == "faculty" and report_type not in {"marks", "attendance", "submissions"}:
        abort(403)
    query, params = report_queries[report_type]
    scope = {
        "students": ("department_id=?", "department_id=?"),
        "marks": ("s.department_id=?", "s.faculty_id=?"),
        "attendance": ("s.department_id=?", "s.faculty_id=?"),
        "payments": ("st.department_id=?", None),
        "submissions": ("s.department_id=?", "s.faculty_id=?"),
    }
    with connect() as connection:
        if g.user["role"] == "hod":
            query = query.replace(" ORDER BY", " WHERE " + scope[report_type][0] + " ORDER BY")
            params.append(g.user["department_id"])
        elif g.user["role"] == "faculty":
            clause = scope[report_type][1]
            query = query.replace(" ORDER BY", " WHERE " + clause + " ORDER BY")
            params.append(g.user["professor_id"])
        rows = [dict(row) for row in connection.execute(query, params)]
    headers = list(rows[0]) if rows else ["message"]
    values = [[row[key] for key in headers] for row in rows] if rows else [["No records"]]
    def safe_spreadsheet_value(value):
        if isinstance(value, str) and value.lstrip().startswith(("=", "+", "-", "@")):
            return "'" + value
        return value

    safe_values = [[safe_spreadsheet_value(value) for value in row] for row in values]
    if format == "csv":
        output = io.StringIO()
        writer = csv.writer(output)
        writer.writerow(headers)
        writer.writerows(safe_values)
        response = make_response(output.getvalue())
        response.headers["Content-Type"] = "text/csv; charset=utf-8"
        response.headers["Content-Disposition"] = f"attachment; filename={report_type}.csv"
        return response
    if format == "xlsx":
        try:
            from openpyxl import Workbook
        except ImportError:
            abort(503, description="Excel exports require openpyxl; install the listed project dependencies.")
        output = io.BytesIO()
        workbook = Workbook()
        sheet = workbook.active
        sheet.title = report_type[:31]
        sheet.append(headers)
        for row in safe_values:
            sheet.append(row)
        workbook.save(output)
        output.seek(0)
        return send_file(output, as_attachment=True, download_name=f"{report_type}.xlsx",
                         mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
    try:
        from reportlab.lib.pagesizes import landscape, letter
        from reportlab.platypus import SimpleDocTemplate, Table
    except ImportError:
        abort(503, description="PDF exports require reportlab; install the listed project dependencies.")
    output = io.BytesIO()
    document = SimpleDocTemplate(output, pagesize=landscape(letter))
    document.build([Table([headers] + values, repeatRows=1)])
    output.seek(0)
    return send_file(output, as_attachment=True, download_name=f"{report_type}.pdf", mimetype="application/pdf")


@bp.get("/finance/payments/<int:payment_id>/receipt.pdf")
@roles_required("super_admin", "college_admin", "finance", "student")
def payment_receipt(payment_id):
    with connect() as connection:
        payment = connection.execute(
            """SELECT p.*,f.student_id,st.name AS student_name,st.registration_no,d.code AS department_code,
                      f.semester,f.academic_year
               FROM payments p JOIN fees f ON f.id=p.fee_id JOIN students st ON st.id=f.student_id
               LEFT JOIN departments d ON d.id=st.department_id WHERE p.id=?""",
            (payment_id,),
        ).fetchone()
        if not payment:
            abort(404)
        if g.user["role"] == "student" and payment["student_id"] != g.user["student_id"]:
            abort(404)
        if payment["status"] != "Recorded":
            abort(404)
    try:
        from reportlab.lib.pagesizes import letter
        from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table
        from reportlab.lib.styles import getSampleStyleSheet
    except ImportError:
        abort(503, description="PDF receipts require reportlab; install the listed project dependencies.")
    output = io.BytesIO()
    document = SimpleDocTemplate(output, pagesize=letter)
    styles = getSampleStyleSheet()
    details = [
        ["CampusLogic payment receipt", ""],
        ["Transaction ID", payment["transaction_id"]],
        ["Student", payment["student_name"]],
        ["USN / Registration", payment["registration_no"] or "—"],
        ["Department", payment["department_code"] or "—"],
        ["Semester / academic year", f"{payment['semester']} / {payment['academic_year']}"],
        ["Payment date", payment["payment_date"]],
        ["Amount", f"{payment['amount']:.2f}"],
        ["Method", payment["payment_method"]],
        ["Reference", payment["reference_number"] or "—"],
        ["Status", "Recorded manually by authorized finance staff"],
    ]
    document.build([
        Paragraph("CampusLogic · Payment Receipt", styles["Title"]),
        Spacer(1, 16),
        Table(details, colWidths=[180, 300]),
    ])
    output.seek(0)
    return send_file(
        output,
        as_attachment=True,
        download_name=f"receipt-{payment['transaction_id']}.pdf",
        mimetype="application/pdf",
    )


@bp.get("/communication/faculty")
@roles_required("super_admin", "college_admin", "hod", "faculty", "student")
def faculty_connect():
    contact_id = request.args.get("contact_id", type=int)
    with connect() as connection:
        _expire_stale_call_rooms(connection)
        contacts = _connection_contacts(connection)
        if contact_id is None and contacts:
            contact_id = contacts[0]["id"]
        selected_contact = next(
            (contact for contact in contacts if contact["id"] == contact_id), None
        )
        if selected_contact:
            messages = [dict(row) for row in connection.execute(
                """SELECT fm.id, fm.message, fm.created_at, fm.sender_id,
                          sender.username AS sender_username
                   FROM faculty_messages fm
                   JOIN users sender ON sender.id=fm.sender_id
                   WHERE ((fm.sender_id=? AND fm.receiver_id=?)
                       OR (fm.sender_id=? AND fm.receiver_id=?))
                   ORDER BY fm.id DESC LIMIT 100""",
                (g.user["id"], selected_contact["id"], selected_contact["id"], g.user["id"]),
            )]
            messages.reverse()
        else:
            messages = []
        incoming_calls = [dict(row) for row in connection.execute(
            """SELECT r.room_id, r.created_at, u.username,
                      COALESCE(p.name, s.name, u.username) AS caller_name
               FROM faculty_call_rooms r
               JOIN users u ON u.id=r.caller_id
               LEFT JOIN professors p ON p.id=u.professor_id
               LEFT JOIN students s ON s.id=u.student_id
               WHERE r.receiver_id=? AND r.status='pending'
                 AND r.created_at>=datetime('now','-15 minutes')
               ORDER BY r.id DESC""",
            (g.user["id"],),
        )]
    return render_template(
        "faculty_connect.html",
        contacts=contacts,
        selected_contact=selected_contact,
        messages=messages,
        incoming_calls=incoming_calls,
    )


@bp.get("/communication/messages/<int:contact_id>")
@roles_required("super_admin", "college_admin", "hod", "faculty", "student")
def faculty_messages(contact_id):
    since_id = request.args.get("since_id", type=int) or 0
    with connect() as connection:
        contact = _connection_contact(connection, contact_id)
        if not contact:
            return jsonify({"error": "This conversation is not available."}), 404
        messages = [dict(row) for row in connection.execute(
            """SELECT fm.id, fm.message, fm.created_at, fm.sender_id,
                      sender.username AS sender_username
               FROM faculty_messages fm
               JOIN users sender ON sender.id=fm.sender_id
               WHERE ((fm.sender_id=? AND fm.receiver_id=?)
                   OR (fm.sender_id=? AND fm.receiver_id=?))
                 AND fm.id>?
               ORDER BY fm.id ASC LIMIT 100""",
            (g.user["id"], contact_id, contact_id, g.user["id"], since_id),
        )]
    return jsonify({"messages": messages})


@bp.get("/communication/calls/incoming")
@roles_required("super_admin", "college_admin", "hod", "faculty", "student")
def incoming_faculty_calls():
    with connect() as connection:
        _expire_stale_call_rooms(connection)
        calls = [dict(row) for row in connection.execute(
            """SELECT r.room_id, r.created_at,
                      COALESCE(p.name, s.name, u.username) AS caller_name
               FROM faculty_call_rooms r
               JOIN users u ON u.id=r.caller_id
               LEFT JOIN professors p ON p.id=u.professor_id
               LEFT JOIN students s ON s.id=u.student_id
               WHERE r.receiver_id=? AND r.status='pending'
                 AND r.created_at>=datetime('now','-15 minutes')
               ORDER BY r.id DESC""",
            (g.user["id"],),
        )]
    return jsonify({"calls": calls})


def _expire_stale_call_rooms(connection):
    stale_rooms = [row["room_id"] for row in connection.execute(
        """SELECT room_id FROM faculty_call_rooms
           WHERE (status='pending' AND created_at<datetime('now','-15 minutes'))
              OR (status='active' AND COALESCE(started_at, created_at)<datetime('now','-6 hours'))"""
    )]
    if not stale_rooms:
        return
    connection.execute(
        """UPDATE faculty_call_rooms
           SET status='ended', ended_at=CURRENT_TIMESTAMP, ended_reason='expired'
           WHERE (status='pending' AND created_at<datetime('now','-15 minutes'))
              OR (status='active' AND COALESCE(started_at, created_at)<datetime('now','-6 hours'))"""
    )
    connection.executemany(
        "DELETE FROM faculty_call_signals WHERE room_id=?",
        [(room_id,) for room_id in stale_rooms],
    )


@bp.get("/communication/contacts/status")
@roles_required("super_admin", "college_admin", "hod", "faculty", "student")
def connection_contact_status():
    with connect() as connection:
        contacts = _connection_contacts(connection)
    return jsonify({"contacts": [
        {"id": contact["id"], "presence": contact["presence"]}
        for contact in contacts
    ]})


@bp.post("/communication/messages/<int:contact_id>")
@roles_required("super_admin", "college_admin", "hod", "faculty", "student")
def send_faculty_message(contact_id):
    message = (request.form.get("message", "") or "").strip()
    if not message or len(message) > 4000:
        flash("Messages must contain between 1 and 4,000 characters.", "error")
        return redirect(url_for("college.faculty_connect", contact_id=contact_id))
    with connect() as connection:
        contact = _connection_contact(connection, contact_id)
        if not contact:
            abort(404)
        cursor = connection.execute(
            "INSERT INTO faculty_messages (sender_id, receiver_id, message) VALUES (?, ?, ?)",
            (g.user["id"], contact_id, message),
        )
        log_audit(g.user["id"], "send", "faculty_message", cursor.lastrowid)
    flash("Message sent.", "success")
    return redirect(url_for("college.faculty_connect", contact_id=contact_id))


@bp.post("/communication/presence")
@roles_required("super_admin", "college_admin", "hod", "faculty", "student")
def update_presence():
    payload = request.get_json(silent=True) or {}
    if not isinstance(payload, dict):
        return jsonify({"error": "The presence request must be a JSON object."}), 400
    presence_state = payload.get("state", "online")
    if presence_state not in {"online", "away"}:
        return jsonify({"error": "Presence must be online or away."}), 400
    with connect() as connection:
        connection.execute(
            "UPDATE users SET last_seen_at=CURRENT_TIMESTAMP, presence_state=? WHERE id=?",
            (presence_state, g.user["id"]),
        )
    return jsonify({"ok": True})


@bp.post("/communication/call/<int:contact_id>")
@roles_required("super_admin", "college_admin", "hod", "faculty", "student")
def start_faculty_call(contact_id):
    with connect() as connection:
        _expire_stale_call_rooms(connection)
        contact = _connection_contact(connection, contact_id)
        if not contact:
            abort(404)
        existing_room = connection.execute(
            """SELECT room_id FROM faculty_call_rooms
               WHERE status IN ('pending','active')
                 AND ((caller_id=? AND receiver_id=?) OR (caller_id=? AND receiver_id=?))
               ORDER BY id DESC LIMIT 1""",
            (g.user["id"], contact_id, contact_id, g.user["id"]),
        ).fetchone()
        if existing_room:
            room_id = existing_room["room_id"]
        else:
            room_id = secrets.token_urlsafe(12)
            connection.execute(
                "INSERT INTO faculty_call_rooms (room_id, caller_id, receiver_id, status) VALUES (?, ?, ?, 'pending')",
                (room_id, g.user["id"], contact_id),
            )
            log_audit(g.user["id"], "start", "faculty_call", room_id)
    return redirect(url_for("college.faculty_call_room", room_id=room_id))


@bp.post("/communication/call/<room_id>/respond")
@roles_required("super_admin", "college_admin", "hod", "faculty", "student")
def respond_faculty_call(room_id):
    response = request.form.get("response")
    if response not in {"accept", "decline"}:
        abort(400)
    with connect() as connection:
        room = connection.execute(
            "SELECT caller_id, receiver_id, status, created_at FROM faculty_call_rooms WHERE room_id=?",
            (room_id,),
        ).fetchone()
        if not room:
            abort(404)
        if room["receiver_id"] != g.user["id"]:
            abort(403)
        if room["status"] == "pending" and room["created_at"] < connection.execute(
            "SELECT datetime('now','-15 minutes')"
        ).fetchone()[0]:
            connection.execute(
                """UPDATE faculty_call_rooms
                   SET status='ended', ended_at=CURRENT_TIMESTAMP, ended_reason='expired'
                   WHERE room_id=? AND status='pending'""",
                (room_id,),
            )
            connection.execute(
                "DELETE FROM faculty_call_signals WHERE room_id=?", (room_id,)
            )
            flash("This call request has expired.", "error")
            return redirect(url_for("college.faculty_connect"))
        if room["status"] != "pending":
            flash("This call request is no longer waiting for a response.", "error")
            return redirect(url_for("college.faculty_connect"))
        if response == "accept":
            connection.execute(
                """UPDATE faculty_call_rooms
                   SET status='active', started_at=CURRENT_TIMESTAMP
                   WHERE room_id=? AND status='pending'""",
                (room_id,),
            )
            log_audit(g.user["id"], "accept", "faculty_call", room_id)
            return redirect(url_for("college.faculty_call_room", room_id=room_id))
        connection.execute(
            """UPDATE faculty_call_rooms
               SET status='ended', ended_at=CURRENT_TIMESTAMP, ended_reason='declined'
               WHERE room_id=? AND status='pending'""",
            (room_id,),
        )
        connection.execute(
            "DELETE FROM faculty_call_signals WHERE room_id=?", (room_id,)
        )
        log_audit(g.user["id"], "decline", "faculty_call", room_id)
    flash("Call request declined.", "success")
    return redirect(url_for("college.faculty_connect"))


@bp.get("/communication/call/<room_id>")
@roles_required("super_admin", "college_admin", "hod", "faculty", "student")
def faculty_call_room(room_id):
    with connect() as connection:
        room = connection.execute(
            """SELECT room_id, caller_id, receiver_id, status, ended_reason
               FROM faculty_call_rooms WHERE room_id=?""",
            (room_id,),
        ).fetchone()
        if not room:
            abort(404)
        if g.user["id"] not in {room["caller_id"], room["receiver_id"]}:
            abort(403)
        if room["status"] == "ended":
            flash("This call has ended or was declined.", "error")
            return redirect(url_for("college.faculty_connect"))
        peer_id = room["receiver_id"] if room["caller_id"] == g.user["id"] else room["caller_id"]
        peer = connection.execute(
            """SELECT u.id, COALESCE(p.name, s.name, u.username) AS name, u.role
               FROM users u
               LEFT JOIN professors p ON p.id=u.professor_id
               LEFT JOIN students s ON s.id=u.student_id WHERE u.id=?""",
            (peer_id,),
        ).fetchone()
        can_enter = room["status"] == "active" or room["caller_id"] == g.user["id"]
    if not can_enter:
        flash("This call is waiting for the other person to accept.", "info")
        return redirect(url_for("college.faculty_connect"))
    return render_template(
        "faculty_call.html",
        room_id=room_id,
        peer=peer,
        is_caller=room["caller_id"] == g.user["id"],
        call_status=room["status"],
        ice_servers=current_app.config["WEBRTC_ICE_SERVERS"],
    )


@bp.post("/communication/call/<room_id>/end")
@roles_required("super_admin", "college_admin", "hod", "faculty", "student")
def end_faculty_call(room_id):
    with connect() as connection:
        room = connection.execute(
            "SELECT caller_id, receiver_id, status FROM faculty_call_rooms WHERE room_id=?",
            (room_id,),
        ).fetchone()
        if not room:
            abort(404)
        if g.user["id"] not in {room["caller_id"], room["receiver_id"]}:
            abort(403)
        if room["status"] != "ended":
            connection.execute(
                """UPDATE faculty_call_rooms
                   SET status='ended', ended_at=CURRENT_TIMESTAMP, ended_reason='ended'
                   WHERE room_id=?""",
                (room_id,),
            )
            connection.execute(
                "DELETE FROM faculty_call_signals WHERE room_id=?", (room_id,)
            )
            log_audit(g.user["id"], "end", "faculty_call", room_id)
    return redirect(url_for("college.faculty_connect"))


@bp.get("/communication/call/<room_id>/status")
@roles_required("super_admin", "college_admin", "hod", "faculty", "student")
def faculty_call_status(room_id):
    with connect() as connection:
        room = connection.execute(
            "SELECT caller_id, receiver_id, status, ended_reason, created_at FROM faculty_call_rooms WHERE room_id=?",
            (room_id,),
        ).fetchone()
        if not room:
            return jsonify({"error": "Call room not found."}), 404
        if g.user["id"] not in {room["caller_id"], room["receiver_id"]}:
            return jsonify({"error": "You are not part of this call."}), 403
        if room["status"] == "pending" and room["created_at"] < connection.execute(
            "SELECT datetime('now','-15 minutes')"
        ).fetchone()[0]:
            connection.execute(
                """UPDATE faculty_call_rooms
                   SET status='ended', ended_at=CURRENT_TIMESTAMP, ended_reason='expired'
                   WHERE room_id=? AND status='pending'""",
                (room_id,),
            )
            connection.execute(
                "DELETE FROM faculty_call_signals WHERE room_id=?", (room_id,)
            )
            room = dict(room)
            room["status"] = "ended"
            room["ended_reason"] = "expired"
    return jsonify({"status": room["status"], "ended_reason": room["ended_reason"]})


@bp.post("/communication/call/<room_id>/signal")
@roles_required("super_admin", "college_admin", "hod", "faculty", "student")
def faculty_call_signal(room_id):
    payload = request.get_json(silent=True) or {}
    kind = payload.get("kind")
    signal_data = payload.get("payload")
    if kind not in {"offer", "answer", "candidate"} or not isinstance(signal_data, dict):
        return jsonify({"error": "Invalid call signal."}), 400
    if len(json.dumps(signal_data)) > 65536:
        return jsonify({"error": "Call signal is too large."}), 413
    with connect() as connection:
        room = connection.execute(
            "SELECT room_id, caller_id, receiver_id, status FROM faculty_call_rooms WHERE room_id=?",
            (room_id,),
        ).fetchone()
        if not room:
            return jsonify({"error": "Call room not found."}), 404
        if g.user["id"] not in {room["caller_id"], room["receiver_id"]}:
            return jsonify({"error": "You are not part of this call."}), 403
        if room["status"] != "active":
            return jsonify({"error": "The call has not been accepted or has ended."}), 409
        if kind == "offer" and g.user["id"] != room["caller_id"]:
            return jsonify({"error": "Only the caller can start the call offer."}), 403
        if kind == "answer" and g.user["id"] != room["receiver_id"]:
            return jsonify({"error": "Only the recipient can answer the call offer."}), 403
        receiver_id = room["receiver_id"] if room["caller_id"] == g.user["id"] else room["caller_id"]
        connection.execute(
            "INSERT INTO faculty_call_signals (room_id, sender_id, receiver_id, kind, payload) VALUES (?, ?, ?, ?, ?)",
            (room_id, g.user["id"], receiver_id, kind, json.dumps(signal_data)),
        )
    return jsonify({"ok": True})


@bp.get("/communication/call/<room_id>/signals")
@roles_required("super_admin", "college_admin", "hod", "faculty", "student")
def faculty_call_signals(room_id):
    since_id = request.args.get("since_id", type=int) or 0
    with connect() as connection:
        room = connection.execute(
            "SELECT room_id, caller_id, receiver_id, status FROM faculty_call_rooms WHERE room_id=?",
            (room_id,),
        ).fetchone()
        if not room:
            return jsonify({"error": "Call room not found."}), 404
        if g.user["id"] not in {room["caller_id"], room["receiver_id"]}:
            return jsonify({"error": "You are not part of this call."}), 403
        if room["status"] != "active":
            return jsonify({"error": "The call is not active."}), 409
        signals = [dict(row) for row in connection.execute(
            """SELECT id, sender_id, receiver_id, kind, payload, created_at
               FROM faculty_call_signals
               WHERE room_id=? AND receiver_id=? AND id>? ORDER BY id ASC""",
            (room_id, g.user["id"], since_id),
        )]
    for signal in signals:
        signal["payload"] = json.loads(signal["payload"])
    return jsonify({"signals": signals})


@bp.get("/admin/backup")
@roles_required("super_admin")
def download_database_backup():
    import database

    source = sqlite3.connect(database.DATABASE_PATH)
    target = sqlite3.connect(":memory:")
    try:
        source.backup(target)
        payload = target.serialize()
    finally:
        target.close()
        source.close()
    return send_file(
        io.BytesIO(payload),
        as_attachment=True,
        download_name=f"campuslogic-backup-{date.today().isoformat()}.sqlite3",
        mimetype="application/vnd.sqlite3",
    )


@bp.get("/admin/audit")
@roles_required("super_admin", "college_admin")
def audit_log_page():
    with connect() as connection:
        rows = [dict(row) for row in connection.execute(
            """SELECT a.*,u.username FROM audit_log a LEFT JOIN users u ON u.id=a.user_id
               ORDER BY a.id DESC LIMIT 300"""
        )]
    return render_template("college_module.html", module="audit", rows=rows, options={})


@bp.get("/admin/communications")
@roles_required("super_admin", "college_admin")
def communication_admin():
    with connect() as connection:
        faculty = [dict(row) for row in connection.execute(
            """SELECT u.id, u.username, u.active, u.contact_enabled,
                      p.name AS faculty_name,
                      GROUP_CONCAT(DISTINCT s.code) AS subject_codes
               FROM users u
               JOIN professors p ON p.id=u.professor_id
               LEFT JOIN subjects s ON s.faculty_id=p.id
               WHERE u.role='faculty'
               GROUP BY u.id, u.username, u.active, u.contact_enabled, p.name
               ORDER BY p.name COLLATE NOCASE"""
        )]
    return render_template("communications_admin.html", faculty=faculty)


@bp.post("/admin/communications/<int:user_id>/toggle")
@roles_required("super_admin", "college_admin")
def toggle_faculty_communication(user_id):
    with connect() as connection:
        user = connection.execute(
            "SELECT id, active, contact_enabled FROM users WHERE id=? AND role='faculty'",
            (user_id,),
        ).fetchone()
        if not user:
            abort(404)
        enabled = 0 if user["contact_enabled"] else 1
        connection.execute(
            "UPDATE users SET contact_enabled=? WHERE id=?",
            (enabled, user_id),
        )
        if not enabled:
            active_rooms = connection.execute(
                """SELECT room_id FROM faculty_call_rooms
                   WHERE status IN ('pending','active')
                     AND (caller_id=? OR receiver_id=?)""",
                (user_id, user_id),
            ).fetchall()
            connection.execute(
                """UPDATE faculty_call_rooms
                   SET status='ended', ended_at=CURRENT_TIMESTAMP, ended_reason='disabled'
                   WHERE status IN ('pending','active') AND (caller_id=? OR receiver_id=?)""",
                (user_id, user_id),
            )
            for room in active_rooms:
                connection.execute(
                    "DELETE FROM faculty_call_signals WHERE room_id=?",
                    (room["room_id"],),
                )
        log_audit(
            g.user["id"],
            "enable_contact" if enabled else "disable_contact",
            "faculty_communication",
            user_id,
        )
    flash(
        "Faculty communication enabled." if enabled else "Faculty communication disabled.",
        "success",
    )
    return redirect(url_for("college.communication_admin"))
