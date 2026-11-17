"""SQLite schema, connection helpers, and first-launch sample data."""

import sqlite3
import os
from contextvars import ContextVar
from werkzeug.security import generate_password_hash
from contextlib import contextmanager
from pathlib import Path

from inference_rules import RULES


BASE_DIR = Path(__file__).resolve().parent
DATABASE_PATH = BASE_DIR / "college_knowledge.db"
_ACTIVE_CONNECTION = ContextVar("college_database_connection", default=None)


SCHEMA = """
PRAGMA foreign_keys = ON;
CREATE TABLE IF NOT EXISTS departments (
    id INTEGER PRIMARY KEY,
    code TEXT NOT NULL COLLATE NOCASE UNIQUE,
    name TEXT NOT NULL,
    description TEXT NOT NULL DEFAULT ''
);
CREATE TABLE IF NOT EXISTS students (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL COLLATE NOCASE UNIQUE,
    email TEXT NOT NULL DEFAULT '',
    department_id INTEGER REFERENCES departments(id) ON DELETE SET NULL
);
CREATE TABLE IF NOT EXISTS professors (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL COLLATE NOCASE UNIQUE,
    email TEXT NOT NULL DEFAULT ''
);
CREATE TABLE IF NOT EXISTS courses (
    id INTEGER PRIMARY KEY,
    code TEXT NOT NULL COLLATE NOCASE UNIQUE,
    title TEXT NOT NULL,
    semester INTEGER CHECK (semester BETWEEN 1 AND 8),
    description TEXT NOT NULL DEFAULT ''
);
CREATE TABLE IF NOT EXISTS enrollments (
    student_id INTEGER NOT NULL REFERENCES students(id) ON DELETE CASCADE,
    course_id INTEGER NOT NULL REFERENCES courses(id) ON DELETE CASCADE,
    PRIMARY KEY (student_id, course_id)
);
CREATE TABLE IF NOT EXISTS teaching_assignments (
    professor_id INTEGER NOT NULL REFERENCES professors(id) ON DELETE CASCADE,
    course_id INTEGER NOT NULL REFERENCES courses(id) ON DELETE CASCADE,
    PRIMARY KEY (professor_id, course_id)
);
CREATE TABLE IF NOT EXISTS department_memberships (
    professor_id INTEGER NOT NULL REFERENCES professors(id) ON DELETE CASCADE,
    department_id INTEGER NOT NULL REFERENCES departments(id) ON DELETE CASCADE,
    PRIMARY KEY (professor_id, department_id)
);
CREATE TABLE IF NOT EXISTS course_offerings (
    course_id INTEGER NOT NULL REFERENCES courses(id) ON DELETE CASCADE,
    department_id INTEGER NOT NULL REFERENCES departments(id) ON DELETE CASCADE,
    PRIMARY KEY (course_id, department_id)
);
CREATE TABLE IF NOT EXISTS logical_facts (
    id INTEGER PRIMARY KEY,
    predicate TEXT NOT NULL,
    arg1 TEXT NOT NULL COLLATE NOCASE,
    arg2 TEXT NOT NULL COLLATE NOCASE DEFAULT '',
    source TEXT NOT NULL CHECK (source IN ('record', 'manual')),
    UNIQUE (predicate, arg1, arg2)
);
CREATE TABLE IF NOT EXISTS inference_rules (
    rule_id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    formula TEXT NOT NULL,
    description TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS query_history (
    id INTEGER PRIMARY KEY,
    query_text TEXT NOT NULL,
    result TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY,
    username TEXT NOT NULL COLLATE NOCASE UNIQUE,
    password_hash TEXT NOT NULL,
    role TEXT NOT NULL CHECK (role IN
      ('super_admin','college_admin','hod','faculty','student','finance')),
    student_id INTEGER REFERENCES students(id) ON DELETE CASCADE,
    professor_id INTEGER REFERENCES professors(id) ON DELETE CASCADE,
    department_id INTEGER REFERENCES departments(id) ON DELETE SET NULL,
    active INTEGER NOT NULL DEFAULT 1,
    contact_enabled INTEGER NOT NULL DEFAULT 1 CHECK (contact_enabled IN (0,1)),
    presence_state TEXT NOT NULL DEFAULT 'offline' CHECK (presence_state IN ('online','away','offline')),
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS subjects (
    id INTEGER PRIMARY KEY,
    code TEXT NOT NULL COLLATE NOCASE,
    name TEXT NOT NULL,
    department_id INTEGER NOT NULL REFERENCES departments(id) ON DELETE CASCADE,
    semester INTEGER NOT NULL CHECK (semester BETWEEN 1 AND 12),
    academic_year TEXT NOT NULL,
    category TEXT NOT NULL DEFAULT 'Core',
    elective_group TEXT NOT NULL DEFAULT '',
    is_required INTEGER NOT NULL DEFAULT 1 CHECK (is_required IN (0,1)),
    credits REAL NOT NULL DEFAULT 0,
    internal_max REAL NOT NULL DEFAULT 100,
    external_max REAL NOT NULL DEFAULT 100,
    faculty_id INTEGER REFERENCES professors(id) ON DELETE SET NULL,
    outcomes TEXT NOT NULL DEFAULT '',
    description TEXT NOT NULL DEFAULT '',
    UNIQUE (code, academic_year, department_id)
);
CREATE TABLE IF NOT EXISTS subject_enrollments (
    student_id INTEGER NOT NULL REFERENCES students(id) ON DELETE CASCADE,
    subject_id INTEGER NOT NULL REFERENCES subjects(id) ON DELETE CASCADE,
    PRIMARY KEY (student_id, subject_id)
);
CREATE TABLE IF NOT EXISTS internal_marks (
    id INTEGER PRIMARY KEY,
    student_id INTEGER NOT NULL REFERENCES students(id) ON DELETE CASCADE,
    subject_id INTEGER NOT NULL REFERENCES subjects(id) ON DELETE CASCADE,
    assessment TEXT NOT NULL CHECK (assessment IN ('IA1','IA2','IA3','Assignment','Quiz','Practical','Lab')),
    marks REAL NOT NULL CHECK (marks >= 0),
    max_marks REAL NOT NULL CHECK (max_marks > 0),
    updated_by INTEGER REFERENCES users(id) ON DELETE SET NULL,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE (student_id, subject_id, assessment)
);
CREATE TABLE IF NOT EXISTS assignments (
    id INTEGER PRIMARY KEY,
    subject_id INTEGER NOT NULL REFERENCES subjects(id) ON DELETE CASCADE,
    title TEXT NOT NULL,
    description TEXT NOT NULL DEFAULT '',
    document_name TEXT,
    deadline TEXT NOT NULL,
    max_marks REAL NOT NULL DEFAULT 100 CHECK (max_marks > 0),
    created_by INTEGER REFERENCES users(id) ON DELETE SET NULL,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS submissions (
    id INTEGER PRIMARY KEY,
    assignment_id INTEGER NOT NULL REFERENCES assignments(id) ON DELETE CASCADE,
    student_id INTEGER NOT NULL REFERENCES students(id) ON DELETE CASCADE,
    file_name TEXT,
    submitted_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    status TEXT NOT NULL DEFAULT 'Submitted' CHECK (status IN ('Submitted','Late Submission','Under Evaluation','Evaluated')),
    marks REAL CHECK (marks IS NULL OR marks >= 0),
    feedback TEXT NOT NULL DEFAULT '',
    evaluated_by INTEGER REFERENCES users(id) ON DELETE SET NULL,
    UNIQUE (assignment_id, student_id)
);
CREATE TABLE IF NOT EXISTS attendance (
    id INTEGER PRIMARY KEY,
    student_id INTEGER NOT NULL REFERENCES students(id) ON DELETE CASCADE,
    subject_id INTEGER NOT NULL REFERENCES subjects(id) ON DELETE CASCADE,
    attendance_date TEXT NOT NULL,
    period INTEGER NOT NULL DEFAULT 1,
    status TEXT NOT NULL CHECK (status IN ('Present','Absent','On Duty')),
    marked_by INTEGER REFERENCES users(id) ON DELETE SET NULL,
    UNIQUE (student_id, subject_id, attendance_date, period)
);
CREATE TABLE IF NOT EXISTS faculty_attendance (
    id INTEGER PRIMARY KEY,
    professor_id INTEGER NOT NULL REFERENCES professors(id) ON DELETE CASCADE,
    attendance_date TEXT NOT NULL,
    check_in TEXT,
    check_out TEXT,
    status TEXT NOT NULL DEFAULT 'Present' CHECK (status IN ('Present','Absent','On Duty','Leave')),
    leave_note TEXT NOT NULL DEFAULT '',
    UNIQUE (professor_id, attendance_date)
);
CREATE TABLE IF NOT EXISTS fees (
    id INTEGER PRIMARY KEY,
    student_id INTEGER NOT NULL REFERENCES students(id) ON DELETE CASCADE,
    semester INTEGER NOT NULL CHECK (semester BETWEEN 1 AND 12),
    academic_year TEXT NOT NULL,
    tuition REAL NOT NULL DEFAULT 0,
    examination REAL NOT NULL DEFAULT 0,
    laboratory REAL NOT NULL DEFAULT 0,
    library REAL NOT NULL DEFAULT 0,
    transport REAL NOT NULL DEFAULT 0,
    hostel REAL NOT NULL DEFAULT 0,
    other REAL NOT NULL DEFAULT 0,
    concession REAL NOT NULL DEFAULT 0,
    due_date TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'Pending' CHECK (status IN ('Paid','Partially Paid','Pending','Overdue','Waived')),
    created_by INTEGER REFERENCES users(id) ON DELETE SET NULL,
    UNIQUE (student_id, semester, academic_year)
);
CREATE TABLE IF NOT EXISTS payments (
    id INTEGER PRIMARY KEY,
    transaction_id TEXT NOT NULL UNIQUE,
    fee_id INTEGER NOT NULL REFERENCES fees(id) ON DELETE CASCADE,
    amount REAL NOT NULL CHECK (amount > 0),
    payment_method TEXT NOT NULL CHECK (payment_method IN ('Cash','UPI','Bank Transfer','Card','Online Payment')),
    reference_number TEXT NOT NULL DEFAULT '',
    payment_date TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('Recorded','Pending Verification','Failed','Refunded')),
    recorded_by INTEGER REFERENCES users(id) ON DELETE SET NULL,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS notifications (
    id INTEGER PRIMARY KEY,
    title TEXT NOT NULL,
    description TEXT NOT NULL,
    category TEXT NOT NULL DEFAULT 'General',
    target_audience TEXT NOT NULL DEFAULT 'All students',
    department_id INTEGER REFERENCES departments(id) ON DELETE CASCADE,
    semester INTEGER,
    target_user_id INTEGER,
    priority TEXT NOT NULL DEFAULT 'Normal' CHECK (priority IN ('Low','Normal','High','Urgent')),
    published_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    expires_at TEXT,
    posted_by INTEGER REFERENCES users(id) ON DELETE SET NULL,
    attachment_name TEXT
);
CREATE TABLE IF NOT EXISTS notification_reads (
    notification_id INTEGER NOT NULL REFERENCES notifications(id) ON DELETE CASCADE,
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    read_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (notification_id, user_id)
);
CREATE TABLE IF NOT EXISTS faculty_messages (
    id INTEGER PRIMARY KEY,
    sender_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    receiver_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    message TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS faculty_call_rooms (
    id INTEGER PRIMARY KEY,
    room_id TEXT NOT NULL UNIQUE,
    caller_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    receiver_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    status TEXT NOT NULL DEFAULT 'pending' CHECK (status IN ('pending','active','ended')),
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    started_at TEXT,
    ended_at TEXT,
    ended_reason TEXT NOT NULL DEFAULT ''
);
CREATE TABLE IF NOT EXISTS faculty_call_signals (
    id INTEGER PRIMARY KEY,
    room_id TEXT NOT NULL,
    sender_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    receiver_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    kind TEXT NOT NULL CHECK (kind IN ('offer','answer','candidate')),
    payload TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (room_id) REFERENCES faculty_call_rooms(room_id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_faculty_messages_pair ON faculty_messages(sender_id, receiver_id, id);
CREATE INDEX IF NOT EXISTS idx_call_rooms_receiver_status ON faculty_call_rooms(receiver_id, status, id);
CREATE INDEX IF NOT EXISTS idx_call_signals_room_receiver ON faculty_call_signals(room_id, receiver_id, id);
CREATE TABLE IF NOT EXISTS timetable (
    id INTEGER PRIMARY KEY,
    subject_id INTEGER NOT NULL REFERENCES subjects(id) ON DELETE CASCADE,
    section TEXT NOT NULL,
    day_of_week INTEGER NOT NULL CHECK (day_of_week BETWEEN 0 AND 6),
    period INTEGER NOT NULL,
    start_time TEXT NOT NULL,
    end_time TEXT NOT NULL,
    classroom TEXT NOT NULL,
    faculty_id INTEGER REFERENCES professors(id) ON DELETE SET NULL,
    UNIQUE (subject_id, section, day_of_week, period)
);
CREATE TABLE IF NOT EXISTS exams (
    id INTEGER PRIMARY KEY,
    subject_id INTEGER NOT NULL REFERENCES subjects(id) ON DELETE CASCADE,
    exam_type TEXT NOT NULL CHECK (exam_type IN ('Internal','External')),
    exam_date TEXT NOT NULL,
    start_time TEXT NOT NULL,
    end_time TEXT NOT NULL,
    hall TEXT NOT NULL,
    created_by INTEGER REFERENCES users(id) ON DELETE SET NULL,
    UNIQUE (subject_id, exam_type, exam_date, start_time)
);
CREATE TABLE IF NOT EXISTS audit_log (
    id INTEGER PRIMARY KEY,
    user_id INTEGER REFERENCES users(id) ON DELETE SET NULL,
    action TEXT NOT NULL,
    entity TEXT NOT NULL,
    record_id TEXT NOT NULL DEFAULT '',
    details TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS app_settings (
    setting_key TEXT PRIMARY KEY,
    setting_value TEXT NOT NULL
);
"""

MIGRATION_COLUMNS = {
    "courses": {
        "semester": "INTEGER",
    },
    "subjects": {
        "elective_group": "TEXT NOT NULL DEFAULT ''",
        "is_required": "INTEGER NOT NULL DEFAULT 1",
    },
    "users": {
        "last_seen_at": "TEXT",
        "contact_enabled": "INTEGER NOT NULL DEFAULT 1",
        "presence_state": "TEXT NOT NULL DEFAULT 'offline'",
    },
    "faculty_call_rooms": {
        "ended_reason": "TEXT NOT NULL DEFAULT ''",
    },
    "students": {
        "registration_no": "TEXT",
        "admission_no": "TEXT",
        "photo_path": "TEXT",
        "date_of_birth": "TEXT",
        "gender": "TEXT",
        "blood_group": "TEXT",
        "mobile": "TEXT",
        "permanent_address": "TEXT",
        "current_address": "TEXT",
        "guardian_name": "TEXT",
        "guardian_contact": "TEXT",
        "admission_year": "INTEGER",
        "academic_status": "TEXT NOT NULL DEFAULT 'Active'",
        "degree": "TEXT",
        "semester": "INTEGER",
        "section": "TEXT",
        "batch": "TEXT",
        "academic_year": "TEXT",
        "class_teacher_id": "INTEGER REFERENCES professors(id) ON DELETE SET NULL",
        "roll_number": "TEXT",
        "cgpa": "REAL",
        "sgpa": "REAL",
        "backlog_subjects": "TEXT NOT NULL DEFAULT ''",
    },
    "professors": {
        "employee_id": "TEXT",
        "mobile": "TEXT",
        "photo_path": "TEXT",
        "designation": "TEXT",
        "date_of_joining": "TEXT",
    },
    "departments": {
        "hod_name": "TEXT",
        "hod_employee_id": "TEXT",
        "hod_photo_path": "TEXT",
        "hod_email": "TEXT",
        "hod_contact": "TEXT",
        "office_location": "TEXT",
        "established_year": "INTEGER",
    },
    "notifications": {
        "target_user_id": "INTEGER",
        "attachment_name": "TEXT",
    },
}

@contextmanager
def connect():
    active_connection = _ACTIVE_CONNECTION.get()
    if active_connection is not None:
        yield active_connection
        return
    connection = sqlite3.connect(DATABASE_PATH)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    token = _ACTIVE_CONNECTION.set(connection)
    try:
        yield connection
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        _ACTIVE_CONNECTION.reset(token)
        connection.close()


def initialize_database():
    with connect() as connection:
        connection.executescript(SCHEMA)
        _apply_column_migrations(connection)
        _ensure_subject_code_uniqueness_by_department(connection)
        connection.execute(
            """UPDATE subjects SET is_required=CASE
               WHEN category='Elective' THEN 0 ELSE 1 END"""
        )
        connection.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS idx_students_registration_no "
            "ON students(registration_no) WHERE registration_no IS NOT NULL AND registration_no<>''"
        )
        connection.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS idx_professors_employee_id "
            "ON professors(employee_id) WHERE employee_id IS NOT NULL AND employee_id<>''"
        )
        connection.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS idx_users_student_id "
            "ON users(student_id) WHERE student_id IS NOT NULL"
        )
        connection.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS idx_users_professor_id "
            "ON users(professor_id) WHERE professor_id IS NOT NULL"
        )
        connection.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS idx_users_username_lower "
            "ON users(lower(username))"
        )
        connection.execute(
            "INSERT OR IGNORE INTO app_settings (setting_key, setting_value) VALUES ('attendance_threshold', '75')"
        )
        for rule in RULES:
            connection.execute(
                """INSERT INTO inference_rules (rule_id, name, formula, description)
                   VALUES (?, ?, ?, ?)
                   ON CONFLICT(rule_id) DO UPDATE SET
                   name=excluded.name, formula=excluded.formula,
                   description=excluded.description""",
                (rule["id"], rule["name"], rule["formula"], rule["description"]),
            )
        if connection.execute("SELECT COUNT(*) FROM departments").fetchone()[0] == 0:
            _insert_sample_data(connection)
        for row in connection.execute(
            "SELECT id FROM students WHERE semester=5 AND department_id IS NOT NULL AND academic_year IS NOT NULL AND academic_year<>''"
        ):
            ensure_semester_five_core_enrollments(connection, row["id"])
        refresh_record_facts(connection)
        _bootstrap_admin(connection)


def _apply_column_migrations(connection):
    for table, columns in MIGRATION_COLUMNS.items():
        existing = {
            row["name"] for row in connection.execute(f"PRAGMA table_info({table})")
        }
        for column, definition in columns.items():
            if column not in existing:
                connection.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")


def _ensure_subject_code_uniqueness_by_department(connection):
    for index in connection.execute("PRAGMA index_list(subjects)"):
        if not index["unique"]:
            continue
        columns = [
            row["name"] for row in connection.execute(
                f"PRAGMA index_info({index['name']})"
            )
        ]
        if columns != ["code", "academic_year"]:
            continue
        connection.commit()
        connection.execute("PRAGMA foreign_keys=OFF")
        try:
            connection.execute(
                """CREATE TABLE subjects_new (
                    id INTEGER PRIMARY KEY,
                    code TEXT NOT NULL COLLATE NOCASE,
                    name TEXT NOT NULL,
                    department_id INTEGER NOT NULL REFERENCES departments(id) ON DELETE CASCADE,
                    semester INTEGER NOT NULL CHECK (semester BETWEEN 1 AND 12),
                    academic_year TEXT NOT NULL,
                    category TEXT NOT NULL DEFAULT 'Core',
                    elective_group TEXT NOT NULL DEFAULT '',
                    is_required INTEGER NOT NULL DEFAULT 1 CHECK (is_required IN (0,1)),
                    credits REAL NOT NULL DEFAULT 0,
                    internal_max REAL NOT NULL DEFAULT 100,
                    external_max REAL NOT NULL DEFAULT 100,
                    faculty_id INTEGER REFERENCES professors(id) ON DELETE SET NULL,
                    outcomes TEXT NOT NULL DEFAULT '',
                    description TEXT NOT NULL DEFAULT '',
                    UNIQUE (code, academic_year, department_id)
                )"""
            )
            connection.execute(
                """INSERT INTO subjects_new
                   (id,code,name,department_id,semester,academic_year,category,
                    elective_group,is_required,credits,internal_max,external_max,
                    faculty_id,outcomes,description)
                   SELECT id,code,name,department_id,semester,academic_year,category,
                          elective_group,is_required,credits,internal_max,external_max,
                          faculty_id,outcomes,description
                   FROM subjects"""
            )
            connection.execute("DROP TABLE subjects")
            connection.execute("ALTER TABLE subjects_new RENAME TO subjects")
        finally:
            connection.execute("PRAGMA foreign_keys=ON")
        break


def _bootstrap_admin(connection):
    username = os.environ.get("BOOTSTRAP_ADMIN_USERNAME", "").strip()
    password = os.environ.get("BOOTSTRAP_ADMIN_PASSWORD", "")
    if not username and not password:
        return
    if not username or len(password) < 12:
        raise RuntimeError(
            "Set both BOOTSTRAP_ADMIN_USERNAME and BOOTSTRAP_ADMIN_PASSWORD; "
            "the password must contain at least 12 characters."
        )
    existing = connection.execute(
        "SELECT id FROM users WHERE username=?", (username,)
    ).fetchone()
    if existing is None and connection.execute("SELECT COUNT(*) FROM users").fetchone()[0] == 0:
        connection.execute(
            "INSERT INTO users (username, password_hash, role) VALUES (?, ?, 'super_admin')",
            (username, generate_password_hash(password)),
        )


def _insert_sample_data(connection):
    departments = [
        ("CSE", "Computer Science and Engineering", "Computing, software, and intelligent systems."),
        ("ECE", "Electronics and Communication Engineering", "Electronics, communications, and embedded systems."),
    ]
    connection.executemany(
        "INSERT INTO departments (code, name, description) VALUES (?, ?, ?)", departments
    )
    department_ids = {
        row["code"]: row["id"]
        for row in connection.execute("SELECT id, code FROM departments")
    }
    connection.executemany(
        "INSERT INTO students (name, email, department_id) VALUES (?, ?, ?)",
        [
            ("Ravi", "ravi@example.edu", department_ids["CSE"]),
            ("Ananya", "ananya@example.edu", department_ids["CSE"]),
            ("Kiran", "kiran@example.edu", department_ids["ECE"]),
        ],
    )
    connection.executemany(
        "INSERT INTO professors (name, email) VALUES (?, ?)",
        [("Meena", "meena@example.edu"), ("Rahul", "rahul@example.edu")],
    )
    connection.executemany(
        "INSERT INTO courses (code, title, description) VALUES (?, ?, ?)",
        [
            ("AI", "Artificial Intelligence", "Search, knowledge representation, and reasoning."),
            ("DBMS", "Database Management Systems", "Relational data modeling and database systems."),
            ("PYTHON", "Python Programming", "Programming fundamentals with Python."),
        ],
    )
    student_ids = {
        row["name"]: row["id"] for row in connection.execute("SELECT id, name FROM students")
    }
    professor_ids = {
        row["name"]: row["id"] for row in connection.execute("SELECT id, name FROM professors")
    }
    course_ids = {
        row["code"]: row["id"] for row in connection.execute("SELECT id, code FROM courses")
    }
    connection.executemany(
        "INSERT INTO enrollments (student_id, course_id) VALUES (?, ?)",
        [
            (student_ids["Ravi"], course_ids["AI"]),
            (student_ids["Ananya"], course_ids["AI"]),
            (student_ids["Kiran"], course_ids["DBMS"]),
        ],
    )
    connection.executemany(
        "INSERT INTO teaching_assignments (professor_id, course_id) VALUES (?, ?)",
        [
            (professor_ids["Meena"], course_ids["AI"]),
            (professor_ids["Rahul"], course_ids["DBMS"]),
            (professor_ids["Meena"], course_ids["PYTHON"]),
        ],
    )
    connection.executemany(
        "INSERT INTO department_memberships (professor_id, department_id) VALUES (?, ?)",
        [
            (professor_ids["Meena"], department_ids["CSE"]),
            (professor_ids["Rahul"], department_ids["CSE"]),
        ],
    )
    connection.executemany(
        "INSERT INTO course_offerings (course_id, department_id) VALUES (?, ?)",
        [
            (course_ids["AI"], department_ids["CSE"]),
            (course_ids["DBMS"], department_ids["CSE"]),
            (course_ids["PYTHON"], department_ids["ECE"]),
        ],
    )


def refresh_record_facts(connection):
    """Rebuild only record-managed facts; manually entered facts are preserved."""
    connection.execute("DELETE FROM logical_facts WHERE source='record'")
    facts = []
    for row in connection.execute("SELECT name FROM students"):
        facts.append(("Student", row["name"], "", "record"))
    for row in connection.execute("SELECT name FROM professors"):
        facts.append(("Professor", row["name"], "", "record"))
    for row in connection.execute("SELECT code FROM courses"):
        facts.append(("Course", row["code"], "", "record"))
    for row in connection.execute("SELECT code FROM departments"):
        facts.append(("Department", row["code"], "", "record"))
    for row in connection.execute("SELECT code FROM subjects"):
        facts.append(("Subject", row["code"], "", "record"))
    for row in connection.execute("SELECT title FROM assignments"):
        facts.append(("Assignment", row["title"], "", "record"))
    for row in connection.execute("SELECT transaction_id FROM payments"):
        facts.append(("Payment", row["transaction_id"], "", "record"))
    for row in connection.execute(
        """SELECT s.name, d.code FROM students s JOIN departments d
           ON d.id=s.department_id"""
    ):
        facts.append(("BelongsTo", row["name"], row["code"], "record"))
    for row in connection.execute(
        "SELECT hod_name, code FROM departments WHERE hod_name IS NOT NULL AND hod_name <> ''"
    ):
        facts.append(("HOD", row["hod_name"], "", "record"))
        facts.append(("AssignedHOD", row["hod_name"], row["code"], "record"))
    queries = [
        ("Enrolled", "SELECT s.name AS a, c.code AS b FROM enrollments e JOIN students s ON s.id=e.student_id JOIN courses c ON c.id=e.course_id"),
        ("Teaches", "SELECT p.name AS a, c.code AS b FROM teaching_assignments t JOIN professors p ON p.id=t.professor_id JOIN courses c ON c.id=t.course_id"),
        ("BelongsTo", "SELECT p.name AS a, d.code AS b FROM department_memberships m JOIN professors p ON p.id=m.professor_id JOIN departments d ON d.id=m.department_id"),
        ("OfferedBy", "SELECT c.code AS a, d.code AS b FROM course_offerings o JOIN courses c ON c.id=o.course_id JOIN departments d ON d.id=o.department_id"),
        ("Teaches", "SELECT p.name AS a, s.code AS b FROM subjects s JOIN professors p ON p.id=s.faculty_id"),
        ("OfferedBy", "SELECT s.code AS a, d.code AS b FROM subjects s JOIN departments d ON d.id=s.department_id"),
        ("Enrolled", "SELECT st.name AS a, s.code AS b FROM subject_enrollments se JOIN students st ON st.id=se.student_id JOIN subjects s ON s.id=se.subject_id"),
        ("HasInternalMarks", "SELECT st.name AS a, s.code AS b FROM internal_marks m JOIN students st ON st.id=m.student_id JOIN subjects s ON s.id=m.subject_id"),
        ("HasAttendance", "SELECT st.name AS a, s.code AS b FROM attendance a JOIN students st ON st.id=a.student_id JOIN subjects s ON s.id=a.subject_id"),
        ("Attends", "SELECT DISTINCT st.name AS a, s.code AS b FROM attendance a JOIN students st ON st.id=a.student_id JOIN subjects s ON s.id=a.subject_id WHERE a.status IN ('Present','On Duty')"),
        ("Submitted", "SELECT st.name AS a, a.title AS b FROM submissions sub JOIN students st ON st.id=sub.student_id JOIN assignments a ON a.id=sub.assignment_id"),
        ("PaidFees", "SELECT DISTINCT st.name AS a, p.transaction_id AS b FROM payments p JOIN fees f ON f.id=p.fee_id JOIN students st ON st.id=f.student_id WHERE p.status='Recorded'"),
    ]
    for predicate, query in queries:
        for row in connection.execute(query):
            facts.append((predicate, row["a"], row["b"], "record"))
    connection.executemany(
        """INSERT OR IGNORE INTO logical_facts (predicate, arg1, arg2, source)
           VALUES (?, ?, ?, ?)""",
        facts,
    )


def ensure_semester_five_core_enrollments(connection, student_id):
    """Enroll a fifth-semester student in every matching required subject."""
    student = connection.execute(
        "SELECT department_id, semester, academic_year FROM students WHERE id=?",
        (student_id,),
    ).fetchone()
    if not student or student["semester"] != 5 or not student["department_id"] or not student["academic_year"]:
        return 0
    cursor = connection.execute(
        """INSERT OR IGNORE INTO subject_enrollments (student_id, subject_id)
           SELECT ?, id FROM subjects
           WHERE department_id=? AND semester=5 AND is_required=1 AND academic_year=?""",
        (student_id, student["department_id"], student["academic_year"]),
    )
    return cursor.rowcount
