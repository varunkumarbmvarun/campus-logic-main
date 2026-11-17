from io import BytesIO
import tempfile
import unittest
from pathlib import Path

import college_modules
import database
from app import app
from werkzeug.security import generate_password_hash


class CollegeKnowledgeBaseAppTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.original_database_path = database.DATABASE_PATH
        self.original_upload_folder = college_modules.UPLOAD_FOLDER
        database.DATABASE_PATH = Path(self.temp_dir.name) / "test.db"
        college_modules.UPLOAD_FOLDER = Path(self.temp_dir.name) / "uploads"
        database.initialize_database()
        app.config.update(TESTING=True)
        self.client = app.test_client()
        with database.connect() as connection:
            cursor = connection.execute(
                "INSERT INTO users (username,password_hash,role) VALUES (?,?,?)",
                ("test-admin", generate_password_hash("test-password-123"), "super_admin"),
            )
            self.admin_id = cursor.lastrowid
        with self.client.session_transaction() as session:
            session["user_id"] = self.admin_id
            session["csrf_token"] = "test-csrf-token"

    def tearDown(self):
        database.DATABASE_PATH = self.original_database_path
        college_modules.UPLOAD_FOLDER = self.original_upload_folder
        self.temp_dir.cleanup()

    def create_role_client(self, role, username, **links):
        with database.connect() as connection:
            columns = ["username", "password_hash", "role"] + list(links)
            values = [username, generate_password_hash("test-account-password-123"), role] + list(links.values())
            cursor = connection.execute(
                f"INSERT INTO users ({','.join(columns)}) VALUES ({','.join('?' for _ in columns)})",
                values,
            )
            user_id = cursor.lastrowid
        client = app.test_client()
        with client.session_transaction() as session:
            session["user_id"] = user_id
            session["csrf_token"] = "role-test-csrf"
        return client, user_id

    def test_dashboard_and_query_render(self):
        response = self.client.get("/")
        self.assertEqual(response.status_code, 200)
        self.assertIn(b"Logical facts", response.data)
        self.assertIn(b'class="sidebar-navigation"', response.data)
        self.assertIn(b"Sign out", response.data)
        self.assertIn(b"css/style.css?v=4", response.data)
        self.assertIn(b"js/app.js?v=4", response.data)
        response = self.client.post("/query", data={"query": "Who teaches AI?"})
        self.assertEqual(response.status_code, 200)
        self.assertIn(b"Meena", response.data)
        self.assertIn(b"Teaches", response.data)
        history = self.client.get("/history")
        self.assertEqual(history.status_code, 200)
        self.assertIn(b"Who teaches AI?", history.data)

    def test_faculty_communication_and_call_room(self):
        with database.connect() as connection:
            professor_id = connection.execute("SELECT id FROM professors WHERE name='Meena'").fetchone()["id"]
            student_id = connection.execute("SELECT id FROM students WHERE name='Ravi'").fetchone()["id"]
            cse_id = connection.execute("SELECT id FROM departments WHERE code='CSE'").fetchone()["id"]
            connection.execute(
                """INSERT INTO subjects (code,name,department_id,semester,academic_year,faculty_id)
                   VALUES ('CONNECT101','Project studio',?,5,'2026-27',?)""",
                (cse_id, professor_id),
            )
            subject_id = connection.execute(
                "SELECT id FROM subjects WHERE code='CONNECT101'"
            ).fetchone()["id"]
            connection.execute(
                "INSERT INTO subject_enrollments (student_id,subject_id) VALUES (?,?)",
                (student_id, subject_id),
            )
            connection.execute(
                "INSERT INTO users (username, password_hash, role, professor_id) VALUES (?, ?, ?, ?)",
                ("faculty-demo", generate_password_hash("test-account-password-123"), "faculty", professor_id),
            )
            connection.execute(
                "INSERT INTO users (username, password_hash, role, student_id) VALUES (?, ?, ?, ?)",
                ("student-demo", generate_password_hash("test-account-password-123"), "student", student_id),
            )
            faculty_user_id = connection.execute(
                "SELECT id FROM users WHERE username='faculty-demo'"
            ).fetchone()["id"]
            student_user_id = connection.execute(
                "SELECT id FROM users WHERE username='student-demo'"
            ).fetchone()["id"]
        response = self.client.get("/communication/faculty")
        self.assertEqual(response.status_code, 200)
        self.assertIn(b"College contacts", response.data)
        response = self.client.post(
            f"/communication/messages/{faculty_user_id}",
            data={"message": "Please review my project progress."},
            follow_redirects=True,
        )
        self.assertEqual(response.status_code, 200)
        self.assertIn(b"Message sent.", response.data)
        with database.connect() as connection:
            count = connection.execute("SELECT COUNT(*) FROM faculty_messages").fetchone()[0]
            self.assertGreater(count, 0)
        response = self.client.post(
            f"/communication/call/{faculty_user_id}",
            follow_redirects=True,
        )
        self.assertEqual(response.status_code, 200)
        self.assertIn(b"Secure call with", response.data)
        with database.connect() as connection:
            room_id = connection.execute(
                "SELECT room_id FROM faculty_call_rooms ORDER BY id DESC LIMIT 1"
            ).fetchone()["room_id"]

        faculty_client = app.test_client()
        with faculty_client.session_transaction() as session:
            session["user_id"] = faculty_user_id
            session["csrf_token"] = "faculty-csrf-token"
        response = faculty_client.get("/communication/faculty")
        self.assertIn(b"Incoming secure call from", response.data)
        response = faculty_client.post(
            f"/communication/call/{room_id}/respond",
            data={"response": "accept"},
            follow_redirects=True,
        )
        self.assertEqual(response.status_code, 200)
        self.assertIn(b"Waiting for the caller", response.data)

        response = self.client.post(
            f"/communication/call/{room_id}/signal",
            json={"kind": "offer", "payload": {"type": "offer", "sdp": "sample-offer"}},
        )
        self.assertEqual(response.status_code, 200)
        response = faculty_client.get(f"/communication/call/{room_id}/signals")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json["signals"][0]["kind"], "offer")
        self.assertEqual(response.json["signals"][0]["payload"]["type"], "offer")

        student_client = app.test_client()
        with student_client.session_transaction() as session:
            session["user_id"] = student_user_id
            session["csrf_token"] = "student-csrf-token"
        response = student_client.get("/communication/faculty")
        self.assertIn(b"Meena", response.data)
        self.assertNotIn(b"Rahul", response.data)
        response = student_client.get(f"/communication/messages/{self.admin_id}")
        self.assertEqual(response.status_code, 404)
        self.assertEqual(
            student_client.get(f"/communication/call/{room_id}/signals").status_code,
            403,
        )
        self.assertEqual(
            self.client.post(f"/communication/call/{room_id}/end").status_code,
            302,
        )
        self.assertEqual(
            faculty_client.get(f"/communication/call/{room_id}/status").json["status"],
            "ended",
        )
        with database.connect() as connection:
            self.assertEqual(
                connection.execute(
                    "SELECT COUNT(*) FROM faculty_call_signals WHERE room_id=?",
                    (room_id,),
                ).fetchone()[0],
                0,
            )
        response = self.client.get("/admin/communications")
        self.assertEqual(response.status_code, 200)
        self.assertIn(b"Meena", response.data)
        response = self.client.post(
            f"/admin/communications/{faculty_user_id}/toggle",
            follow_redirects=True,
        )
        self.assertEqual(response.status_code, 200)
        self.assertIn(b"Faculty communication disabled.", response.data)
        self.assertNotIn(b"Meena", student_client.get("/communication/faculty").data)
        student_client.post("/communication/presence")
        with database.connect() as connection:
            presence = connection.execute(
                "SELECT last_seen_at, presence_state FROM users WHERE id=?",
                (student_user_id,),
            ).fetchone()
            self.assertIsNotNone(presence["last_seen_at"])
            self.assertEqual(presence["presence_state"], "online")
        student_client.post("/logout")
        with database.connect() as connection:
            presence = connection.execute(
                "SELECT last_seen_at, presence_state FROM users WHERE id=?",
                (student_user_id,),
            ).fetchone()
            self.assertIsNone(presence["last_seen_at"])
            self.assertEqual(presence["presence_state"], "offline")

    def test_semester_five_core_enrollment_and_admin_assigned_electives(self):
        with database.connect() as connection:
            cse_id = connection.execute(
                "SELECT id FROM departments WHERE code='CSE'"
            ).fetchone()["id"]

        response = self.client.post(
            "/manage/students",
            data={
                "name": "Term Five Learner",
                "department_id": cse_id,
                "semester": "5",
                "academic_year": "2026-27",
            },
            follow_redirects=True,
        )
        self.assertIn(b"Student added.", response.data)
        with database.connect() as connection:
            student_id = connection.execute(
                "SELECT id FROM students WHERE name='Term Five Learner'"
            ).fetchone()["id"]

        for code, category, elective_group in (
            ("T5CORE", "Core", ""),
            ("T5ELECTIVE", "Elective", "Professional Elective I"),
            ("T5ELECTIVEALT", "Elective", "Professional Elective I"),
        ):
            response = self.client.post(
                "/academics/subjects",
                data={
                    "code": code,
                    "name": code,
                    "department_id": cse_id,
                    "semester": "5",
                    "academic_year": "2026-27",
                    "category": category,
                    "elective_group": elective_group,
                    "credits": "3",
                    "internal_max": "50",
                    "external_max": "100",
                },
            )
            self.assertEqual(response.status_code, 302)

        with database.connect() as connection:
            core_id = connection.execute(
                "SELECT id FROM subjects WHERE code='T5CORE'"
            ).fetchone()["id"]
            elective_id = connection.execute(
                "SELECT id FROM subjects WHERE code='T5ELECTIVE'"
            ).fetchone()["id"]
            alternate_elective_id = connection.execute(
                "SELECT id FROM subjects WHERE code='T5ELECTIVEALT'"
            ).fetchone()["id"]
            self.assertEqual(
                connection.execute(
                    "SELECT COUNT(*) FROM subject_enrollments WHERE student_id=? AND subject_id=?",
                    (student_id, core_id),
                ).fetchone()[0],
                1,
            )
            self.assertEqual(
                connection.execute(
                    "SELECT COUNT(*) FROM subject_enrollments WHERE student_id=? AND subject_id=?",
                    (student_id, elective_id),
                ).fetchone()[0],
                0,
            )

        response = self.client.post(
            "/academics/subjects/enroll",
            data={"student_id": student_id, "subject_id": elective_id},
            follow_redirects=True,
        )
        self.assertIn(b"Student enrolled in subject.", response.data)
        response = self.client.post(
            "/academics/subjects/enroll",
            data={"student_id": student_id, "subject_id": alternate_elective_id},
            follow_redirects=True,
        )
        self.assertIn(b"already has an assigned subject in that elective group", response.data)
        with database.connect() as connection:
            self.assertEqual(
                connection.execute(
                    "SELECT COUNT(*) FROM subject_enrollments WHERE student_id=? AND subject_id=?",
                    (student_id, alternate_elective_id),
                ).fetchone()[0],
                0,
            )

    def test_academic_data_entry_uses_blank_selections_and_suggestions(self):
        students_page = self.client.get("/manage/students")
        self.assertIn(b'id="student-name-suggestions"', students_page.data)
        self.assertNotIn(b">None<", students_page.data)

        courses_page = self.client.get("/manage/courses")
        self.assertIn(b'id="course-code-suggestions"', courses_page.data)
        self.assertIn(b"Choose semester", courses_page.data)
        self.assertNotIn(b">None<", courses_page.data)

        subjects_page = self.client.get("/academics/subjects")
        self.assertIn(b'id="subject-code-suggestions"', subjects_page.data)
        self.assertIn(b"Choose semester", subjects_page.data)

    def test_import_verified_vtu_semester_five_curriculum(self):
        with database.connect() as connection:
            cse_id = connection.execute(
                "SELECT id FROM departments WHERE code='CSE'"
            ).fetchone()["id"]
            student_id = connection.execute(
                """INSERT INTO students (name,department_id,semester,academic_year)
                   VALUES ('VTU Catalog Learner',?,5,'2026-27')""",
                (cse_id,),
            ).lastrowid

        response = self.client.post(
            "/academics/subjects/import-vtu",
            data={
                "branch_key": "cse",
                "department_id": cse_id,
                "academic_year": "2026-27",
            },
            follow_redirects=True,
        )
        self.assertIn(b"Imported 14 VTU Semester 5 subjects", response.data)
        with database.connect() as connection:
            imported = connection.execute(
                """SELECT code,category,elective_group,is_required FROM subjects
                   WHERE department_id=? AND academic_year='2026-27'""",
                (cse_id,),
            ).fetchall()
            self.assertEqual(len(imported), 14)
            self.assertEqual(
                connection.execute(
                    """SELECT COUNT(*) FROM subject_enrollments se
                       JOIN subjects s ON s.id=se.subject_id
                       WHERE se.student_id=? AND s.is_required=1""",
                    (student_id,),
                ).fetchone()[0],
                7,
            )
            elective = connection.execute(
                "SELECT id FROM subjects WHERE code='BAI515A' AND department_id=?",
                (cse_id,),
            ).fetchone()["id"]
            alternate = connection.execute(
                "SELECT id FROM subjects WHERE code='BCS515B' AND department_id=?",
                (cse_id,),
            ).fetchone()["id"]
        response = self.client.post(
            "/academics/subjects/enroll",
            data={"student_id": student_id, "subject_id": elective},
            follow_redirects=True,
        )
        self.assertIn(b"Student enrolled in subject.", response.data)
        response = self.client.post(
            "/academics/subjects/enroll",
            data={"student_id": student_id, "subject_id": alternate},
            follow_redirects=True,
        )
        self.assertIn(b"already has an assigned subject in that elective group", response.data)

        response = self.client.post(
            "/academics/subjects/import-vtu",
            data={
                "branch_key": "cse",
                "department_id": cse_id,
                "academic_year": "2026-27",
            },
            follow_redirects=True,
        )
        self.assertIn(b"All subjects for that branch and academic year already exist", response.data)

    def test_legacy_subject_codes_migrate_to_department_scoped_uniqueness(self):
        test_database_path = database.DATABASE_PATH
        legacy_path = Path(self.temp_dir.name) / "legacy.db"
        database.DATABASE_PATH = legacy_path
        legacy_schema = (
            database.SCHEMA
            .replace("    elective_group TEXT NOT NULL DEFAULT '',\n", "")
            .replace(
                "    is_required INTEGER NOT NULL DEFAULT 1 CHECK (is_required IN (0,1)),\n",
                "",
            )
            .replace(
                "UNIQUE (code, academic_year, department_id)",
                "UNIQUE (code, academic_year)",
            )
        )
        try:
            with database.connect() as connection:
                connection.executescript(legacy_schema)
                first_department = connection.execute(
                    "INSERT INTO departments (code,name) VALUES ('OLD1','Old department 1')"
                ).lastrowid
                second_department = connection.execute(
                    "INSERT INTO departments (code,name) VALUES ('OLD2','Old department 2')"
                ).lastrowid
                connection.execute(
                    """INSERT INTO subjects (code,name,department_id,semester,academic_year)
                       VALUES ('BRMK557','Research Methodology',?,5,'2026-27')""",
                    (first_department,),
                )
                student_id = connection.execute(
                    "INSERT INTO students (name,department_id) VALUES ('Legacy student',?)",
                    (first_department,),
                ).lastrowid
                subject_id = connection.execute(
                    "SELECT id FROM subjects WHERE code='BRMK557'"
                ).fetchone()["id"]
                connection.execute(
                    "INSERT INTO subject_enrollments (student_id,subject_id) VALUES (?,?)",
                    (student_id, subject_id),
                )
            database.initialize_database()
            with database.connect() as connection:
                connection.execute(
                    """INSERT INTO subjects (code,name,department_id,semester,academic_year)
                       VALUES ('BRMK557','Research Methodology',?,5,'2026-27')""",
                    (second_department,),
                )
                self.assertEqual(
                    connection.execute(
                        "SELECT COUNT(*) FROM subjects WHERE code='BRMK557' AND academic_year='2026-27'"
                    ).fetchone()[0],
                    2,
                )
                self.assertEqual(
                    connection.execute(
                        "SELECT COUNT(*) FROM subject_enrollments WHERE student_id=? AND subject_id=?",
                        (student_id, subject_id),
                    ).fetchone()[0],
                    1,
                )
                self.assertEqual(connection.execute("PRAGMA foreign_key_check").fetchall(), [])
        finally:
            database.DATABASE_PATH = test_database_path

    def test_all_demonstration_query_patterns(self):
        examples = [
            ("Who teaches AI?", b"Meena"),
            ("Which students are enrolled in AI?", b"Ravi"),
            ("Is Ravi associated with CSE?", b"Yes"),
            ("Which professors belong to CSE?", b"Rahul"),
            ("Which courses are offered by CSE?", b"DBMS"),
            ("Which students study DBMS?", b"Kiran"),
            ("Which department is associated with Meena?", b"CSE"),
            ("Which professors teach courses offered by CSE?", b"Rahul"),
        ]
        for question, expected in examples:
            with self.subTest(question=question):
                response = self.client.post("/query", data={"query": question})
                self.assertEqual(response.status_code, 200)
                self.assertIn(expected, response.data)

    def test_record_relationships_sync_and_infer(self):
        response = self.client.post(
            "/manage/students",
            data={"name": "Nisha", "email": "nisha@example.edu", "department_id": ""},
            follow_redirects=True,
        )
        self.assertEqual(response.status_code, 200)
        with database.connect() as connection:
            student_id = connection.execute(
                "SELECT id FROM students WHERE name='Nisha'"
            ).fetchone()["id"]
            ai_id = connection.execute("SELECT id FROM courses WHERE code='AI'").fetchone()["id"]
        response = self.client.post(
            "/relationships/enrollment",
            data={"student_id": student_id, "course_id": ai_id},
            follow_redirects=True,
        )
        self.assertEqual(response.status_code, 200)
        response = self.client.post(
            "/query", data={"query": "Which students study AI?"}
        )
        self.assertIn(b"Nisha", response.data)
        self.assertIn(b"Enrollment implies study", response.data)

    def test_management_relationship_and_course_query_flow(self):
        pages = [
            "/manage/students",
            "/manage/professors",
            "/manage/courses",
            "/manage/departments",
            "/relationships",
            "/facts",
            "/history",
        ]
        for path in pages:
            with self.subTest(path=path):
                self.assertEqual(self.client.get(path).status_code, 200)

        with database.connect() as connection:
            cse_id = connection.execute(
                "SELECT id FROM departments WHERE code='CSE'"
            ).fetchone()["id"]
        response = self.client.post(
            "/manage/courses",
            data={
                "code": "ML",
                "title": "Machine Learning",
                "description": "Statistical learning and prediction.",
            },
            follow_redirects=True,
        )
        self.assertIn(b"Machine Learning", response.data)
        response = self.client.post(
            "/manage/professors",
            data={"name": "Priya", "email": "priya@example.edu"},
            follow_redirects=True,
        )
        self.assertEqual(response.status_code, 200)
        with database.connect() as connection:
            professor_id = connection.execute(
                "SELECT id FROM professors WHERE name='Priya'"
            ).fetchone()["id"]
            ml_id = connection.execute(
                "SELECT id FROM courses WHERE code='ML'"
            ).fetchone()["id"]

        for relation, form in [
            ("membership", {"professor_id": professor_id, "department_id": cse_id}),
            ("teaching", {"professor_id": professor_id, "course_id": ml_id}),
            ("offering", {"course_id": ml_id, "department_id": cse_id}),
        ]:
            response = self.client.post(
                f"/relationships/{relation}", data=form, follow_redirects=True
            )
            self.assertEqual(response.status_code, 200)

        response = self.client.post(
            "/query",
            data={"query": "Which professors teach courses offered by CSE?"},
        )
        self.assertIn(b"Priya", response.data)
        self.assertIn(b"OfferedBy", response.data)

        response = self.client.post(
            "/manage/professors",
            data={
                "record_id": professor_id,
                "name": "Priya Shah",
                "email": "priya.shah@example.edu",
            },
            follow_redirects=True,
        )
        self.assertIn(b"Priya Shah", response.data)
        response = self.client.post(
            f"/manage/professors/{professor_id}/delete", follow_redirects=True
        )
        self.assertEqual(response.status_code, 200)
        with database.connect() as connection:
            self.assertIsNone(
                connection.execute(
                    "SELECT id FROM professors WHERE id=?", (professor_id,)
                ).fetchone()
            )
            self.assertEqual(
                connection.execute(
                    "SELECT COUNT(*) FROM logical_facts WHERE predicate='Teaches' AND arg1='Priya Shah'"
                ).fetchone()[0],
                0,
            )

    def test_records_persist_across_database_initialization(self):
        self.client.post(
            "/manage/students",
            data={"name": "Persistent Learner", "email": "", "department_id": ""},
        )
        database.initialize_database()
        with database.connect() as connection:
            self.assertIsNotNone(
                connection.execute(
                    "SELECT id FROM students WHERE name='Persistent Learner'"
                ).fetchone()
            )
            self.assertEqual(
                connection.execute(
                    "SELECT COUNT(*) FROM students WHERE name='Ravi'"
                ).fetchone()[0],
                1,
            )

    def test_manual_fact_validation_and_removal(self):
        response = self.client.post(
            "/facts",
            data={"action": "add", "predicate": "Enrolled", "arg1": "Ravi", "arg2": "AI"},
            follow_redirects=True,
        )
        self.assertIn(b"already exists", response.data)
        response = self.client.post(
            "/facts",
            data={"action": "add", "predicate": "Enrolled", "arg1": "Ravi", "arg2": "MISSING"},
            follow_redirects=True,
        )
        self.assertIn(b"known course", response.data)

    def test_manual_fact_add_and_remove(self):
        response = self.client.post(
            "/facts",
            data={"action": "add", "predicate": "Student", "arg1": "DemoStudent"},
            follow_redirects=True,
        )
        self.assertIn(b"DemoStudent", response.data)
        with database.connect() as connection:
            fact_id = connection.execute(
                "SELECT id FROM logical_facts WHERE predicate='Student' AND arg1='DemoStudent'"
            ).fetchone()["id"]
        response = self.client.post(
            "/facts",
            data={"action": "delete", "fact_id": fact_id},
            follow_redirects=True,
        )
        self.assertNotIn(b"DemoStudent", response.data)

    def test_unsupported_query_returns_helpful_message(self):
        response = self.client.post(
            "/query", data={"query": "Tell me everything about campus"}
        )
        self.assertEqual(response.status_code, 200)
        self.assertIn(b"not supported", response.data)

    def test_query_api_supports_structured_payload(self):
        response = self.client.post(
            "/api/query",
            json={"query_type": "who_teaches", "value": "AI"},
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json()["answer"], ["Meena"])

    def test_extended_module_pages_render(self):
        routes = [
            "/academics/subjects",
            "/academics/marks",
            "/academics/assignments",
            "/academics/attendance",
            "/academics/faculty-attendance",
            "/finance/fees",
            "/finance/payments",
            "/announcements",
            "/academics/timetable",
            "/academics/exams",
            "/admin/users",
            "/admin/audit",
            "/reports/students.csv",
        ]
        for route in routes:
            with self.subTest(route=route):
                response = self.client.get(route)
                self.assertEqual(response.status_code, 200)

    def test_academic_finance_and_rule_based_queries(self):
        with database.connect() as connection:
            cse_id = connection.execute(
                "SELECT id FROM departments WHERE code='CSE'"
            ).fetchone()["id"]
            ravi_id = connection.execute(
                "SELECT id FROM students WHERE name='Ravi'"
            ).fetchone()["id"]
            meena_id = connection.execute(
                "SELECT id FROM professors WHERE name='Meena'"
            ).fetchone()["id"]

        response = self.client.post(
            "/academics/subjects",
            data={
                "code": "BAI501", "name": "Artificial Intelligence",
                "department_id": cse_id, "semester": 5, "academic_year": "2026-27",
                "category": "Core", "credits": 4, "internal_max": 50,
                "external_max": 100, "faculty_id": meena_id,
                "outcomes": "Apply AI search techniques.",
            },
            follow_redirects=True,
        )
        self.assertEqual(response.status_code, 200)
        with database.connect() as connection:
            subject_id = connection.execute(
                "SELECT id FROM subjects WHERE code='BAI501'"
            ).fetchone()["id"]

        self.client.post(
            "/academics/subjects/enroll",
            data={"student_id": ravi_id, "subject_id": subject_id},
        )
        for attendance_status in ("Present", "Absent"):
            self.client.post(
                "/academics/attendance",
                data={
                    "student_id": ravi_id, "subject_id": subject_id,
                    "attendance_date": "2026-11-10" if attendance_status == "Present" else "2026-11-11",
                    "period": 1, "status": attendance_status,
                },
            )
        self.client.post(
            "/academics/marks",
            data={
                "student_id": ravi_id, "subject_id": subject_id,
                "assessment": "IA1", "marks": 38, "max_marks": 50,
            },
        )
        self.client.post(
            "/academics/assignments",
            data={
                "subject_id": subject_id, "title": "Search algorithms",
                "description": "Compare informed search methods.",
                "deadline": "2027-01-15T17:00", "max_marks": 20,
            },
        )
        self.client.post(
            "/finance/fees",
            data={
                "student_id": ravi_id, "semester": 5, "academic_year": "2026-27",
                "tuition": 1000, "due_date": "2027-02-01",
            },
        )
        response = self.client.post(
            "/finance/payments",
            data={
                "fee_id": 1, "amount": 200, "payment_method": "Cash",
                "reference_number": "cash-001", "payment_date": "2026-11-16",
            },
        )
        self.assertEqual(response.status_code, 302)

        expected_answers = {
            "Which students have low attendance?": b"Ravi",
            "Which students have pending fees?": b"Ravi",
            "Which students have not submitted assignments?": b"Ravi",
            "Which students are enrolled in subjects offered by CSE?": b"Ravi",
            "Which faculty members teach BAI501?": b"Meena",
        }
        for query, answer in expected_answers.items():
            with self.subTest(query=query):
                response = self.client.post("/query", data={"query": query})
                self.assertEqual(response.status_code, 200)
                self.assertIn(answer, response.data)

        with database.connect() as connection:
            balance = connection.execute(
                """SELECT (f.tuition+f.examination+f.laboratory+f.library+f.transport+f.hostel+f.other-f.concession)
                      - SUM(CASE WHEN p.status='Recorded' THEN p.amount ELSE 0 END)
                   FROM fees f JOIN payments p ON p.fee_id=f.id WHERE f.student_id=? GROUP BY f.id""",
                (ravi_id,),
            ).fetchone()[0]
            self.assertEqual(balance, 800)
            self.assertEqual(
                connection.execute(
                    "SELECT marks FROM internal_marks WHERE student_id=? AND subject_id=?",
                    (ravi_id, subject_id),
                ).fetchone()[0],
                38,
            )

    def test_student_cannot_read_other_students_profile_or_create_records(self):
        with database.connect() as connection:
            student_id = connection.execute(
                "SELECT id FROM students WHERE name='Ravi'"
            ).fetchone()["id"]
            other_id = connection.execute(
                "SELECT id FROM students WHERE name='Ananya'"
            ).fetchone()["id"]
            cursor = connection.execute(
                "INSERT INTO users (username,password_hash,role,student_id) VALUES (?,?,?,?)",
                ("ravi-account", generate_password_hash("student-password-123"), "student", student_id),
            )
            user_id = cursor.lastrowid
        client = app.test_client()
        with client.session_transaction() as session:
            session["user_id"] = user_id
            session["csrf_token"] = "student-csrf"
        self.assertEqual(client.get("/").status_code, 302)
        self.assertEqual(client.get(f"/students/{student_id}").status_code, 200)
        self.assertEqual(client.get(f"/students/{other_id}").status_code, 403)
        self.assertEqual(
            client.post(
                "/manage/students",
                data={"name": "Unauthorized student"},
            ).status_code,
            403,
        )

    def test_student_internal_marks_show_only_their_own_records(self):
        with database.connect() as connection:
            cse_id = connection.execute(
            "SELECT id FROM departments WHERE code='CSE'"
            ).fetchone()["id"]
            meena_id = connection.execute(
            "SELECT id FROM professors WHERE name='Meena'"
            ).fetchone()["id"]
            ravi_id = connection.execute(
            "SELECT id FROM students WHERE name='Ravi'"
            ).fetchone()["id"]
            ananya_id = connection.execute(
            "SELECT id FROM students WHERE name='Ananya'"
            ).fetchone()["id"]
            subject_id = connection.execute(
            """INSERT INTO subjects (code,name,department_id,semester,academic_year,faculty_id,internal_max)
               VALUES (?,?,?,?,?,?,?)""",
            ("MARKS501","Marks privacy test",cse_id,5,"2026-27",meena_id,50),
            ).lastrowid
            connection.executemany(
            "INSERT INTO subject_enrollments (student_id,subject_id) VALUES (?,?)",
            [(ravi_id,subject_id),(ananya_id,subject_id)],
            )
        self.client.post(
            "/academics/marks",
            data={
            "student_id": ravi_id, "subject_id": subject_id,
            "assessment": "IA1", "marks": 38, "max_marks": 50,
            },
        )
        self.client.post(
            "/academics/marks",
            data={
            "student_id": ananya_id, "subject_id": subject_id,
            "assessment": "IA1", "marks": 11, "max_marks": 50,
            },
        )

        student_client, _ = self.create_role_client(
            "student", "ravi-marks-view", student_id=ravi_id
        )
        response = student_client.get("/academics/marks")
        self.assertEqual(response.status_code, 200)
        self.assertIn(b"MARKS501", response.data)
        self.assertIn(b"38", response.data)
        self.assertNotIn(b"/reports/marks.csv", response.data)
        self.assertNotIn(b"Ananya", response.data)
        self.assertNotIn(b"11", response.data)
        self.assertEqual(
            student_client.post(
            "/academics/marks",
            data={
                "student_id": ravi_id, "subject_id": subject_id,
                "assessment": "IA1", "marks": 40, "max_marks": 50,
            },
            ).status_code,
            403,
        )

    def test_account_controls_and_role_restrictions(self):
        with database.connect() as connection:
            cse_id = connection.execute("SELECT id FROM departments WHERE code='CSE'").fetchone()["id"]
        hod_client, hod_id = self.create_role_client("hod", "cse-hod", department_id=cse_id)
        self.assertEqual(hod_client.get("/facts").status_code, 403)
        self.assertEqual(hod_client.get("/admin/backup").status_code, 403)
        self.assertEqual(hod_client.get("/query").status_code, 200)

        with database.connect() as connection:
            cursor = connection.execute(
                "INSERT INTO users (username,password_hash,role) VALUES (?,?,?)",
                ("inactive-test", generate_password_hash("test-account-password-123"), "faculty"),
            )
            inactive_id = cursor.lastrowid
        response = self.client.post(f"/admin/users/{inactive_id}/toggle")
        self.assertEqual(response.status_code, 302)
        with database.connect() as connection:
            self.assertEqual(
                connection.execute("SELECT active FROM users WHERE id=?", (inactive_id,)).fetchone()[0],
                0,
            )
        self.client.post(f"/admin/users/{self.admin_id}/toggle")
        with database.connect() as connection:
            self.assertEqual(
                connection.execute("SELECT active FROM users WHERE id=?", (self.admin_id,)).fetchone()[0],
                1,
            )

        finance_client, _ = self.create_role_client("finance", "finance-test")
        self.assertEqual(finance_client.get("/reports/students.csv").status_code, 403)
        self.assertEqual(finance_client.get("/reports/payments.csv").status_code, 200)

    def test_student_user_account_links_to_selected_student_profile(self):
        with database.connect() as connection:
            student_id = connection.execute(
                "SELECT id FROM students WHERE name='Ravi'"
            ).fetchone()["id"]

        page = self.client.get("/admin/users")
        self.assertIn(f'<option value="{student_id}">Ravi</option>'.encode(), page.data)
        self.assertIn(b'Select an account role</option>', page.data)
        self.assertIn(b'data-user-student><label for="student_id">Student profile', page.data)

        response = self.client.post(
            "/admin/users",
            data={
                "username": "ravi-student-login",
                "password": "student-account-password",
                "role": "student",
                "student_id": student_id,
            },
            follow_redirects=True,
        )
        self.assertEqual(response.status_code, 200)
        self.assertIn(b"User account created.", response.data)
        with database.connect() as connection:
            linked_user = connection.execute(
                "SELECT role, student_id FROM users WHERE username=?",
                ("ravi-student-login",),
            ).fetchone()
        self.assertEqual(linked_user["role"], "student")
        self.assertEqual(linked_user["student_id"], student_id)

    def test_password_change_and_csrf_guard(self):
        response = self.client.post(
            "/account/password",
            data={
                "old_password": "test-password-123",
                "new_password": "new-test-password-456",
                "confirm_password": "new-test-password-456",
            },
            follow_redirects=True,
        )
        self.assertEqual(response.status_code, 200)
        with database.connect() as connection:
            password_hash = connection.execute(
                "SELECT password_hash FROM users WHERE id=?", (self.admin_id,)
            ).fetchone()["password_hash"]
        from werkzeug.security import check_password_hash
        self.assertTrue(check_password_hash(password_hash, "new-test-password-456"))

        original_testing = app.config["TESTING"]
        app.config["TESTING"] = False
        try:
            response = self.client.post("/manage/students", data={"name": "CSRF learner"})
            self.assertEqual(response.status_code, 400)
            response = self.client.post(
                "/manage/students",
                data={"name": "CSRF learner", "csrf_token": "test-csrf-token"},
            )
            self.assertEqual(response.status_code, 302)
        finally:
            app.config["TESTING"] = original_testing

    def test_announcement_audience_is_scoped(self):
        with database.connect() as connection:
            departments = {
                row["code"]: row["id"]
                for row in connection.execute("SELECT id,code FROM departments")
            }
            cse_student = connection.execute(
                "INSERT INTO students (name,email,department_id,semester) VALUES (?,?,?,?)",
                ("CSE Notice Recipient", "", departments["CSE"], 3),
            ).lastrowid
            ece_student = connection.execute(
                "INSERT INTO students (name,email,department_id,semester) VALUES (?,?,?,?)",
                ("ECE Notice Recipient", "", departments["ECE"], 3),
            ).lastrowid
        hod_client, _ = self.create_role_client("hod", "notice-hod", department_id=departments["CSE"])
        response = hod_client.post(
            "/announcements",
            data={
                "title": "CSE only", "description": "Department-specific notice.",
                "category": "Academic", "priority": "Normal",
                "target_audience": "Department",
                "attachment": (BytesIO(b"department notice"), "notice.txt"),
            },
        )
        self.assertEqual(response.status_code, 302)
        self.assertEqual(
            hod_client.post(
                "/announcements",
                data={
                    "title": "Global", "description": "Not permitted for HOD.",
                    "category": "General", "priority": "Normal",
                    "target_audience": "Everyone",
                },
            ).status_code,
            403,
        )
        cse_client, cse_user = self.create_role_client("student", "cse-notice-student", student_id=cse_student)
        ece_client, _ = self.create_role_client("student", "ece-notice-student", student_id=ece_student)
        self.assertIn(b"CSE only", cse_client.get("/announcements").data)
        self.assertNotIn(b"CSE only", ece_client.get("/announcements").data)
        with database.connect() as connection:
            notice_id = connection.execute(
                "SELECT id FROM notifications WHERE title='CSE only'"
            ).fetchone()["id"]
        attachment_url = f"/announcements/{notice_id}/attachment"
        response = cse_client.get(attachment_url)
        self.assertEqual(response.data, b"department notice")
        response.close()
        self.assertEqual(ece_client.get(attachment_url).status_code, 404)

        individual = self.client.post(
            "/announcements",
            data={
                "title": "Individual notice", "description": "Only Ravi should see this.",
                "category": "General", "priority": "Normal",
                "target_audience": "Individual student", "target_user_id": cse_user,
            },
        )
        self.assertEqual(individual.status_code, 302)
        self.assertIn(b"Individual notice", cse_client.get("/announcements").data)
        self.assertNotIn(b"Individual notice", ece_client.get("/announcements").data)

    def test_student_and_faculty_landings_show_only_linked_academic_records(self):
        with database.connect() as connection:
            cse_id = connection.execute("SELECT id FROM departments WHERE code='CSE'").fetchone()["id"]
            ece_id = connection.execute("SELECT id FROM departments WHERE code='ECE'").fetchone()["id"]
            meena_id = connection.execute("SELECT id FROM professors WHERE name='Meena'").fetchone()["id"]
            rahul_id = connection.execute("SELECT id FROM professors WHERE name='Rahul'").fetchone()["id"]
            ravi_id = connection.execute("SELECT id FROM students WHERE name='Ravi'").fetchone()["id"]
            meena_subject = connection.execute(
                """INSERT INTO subjects (code,name,department_id,semester,academic_year,faculty_id)
                   VALUES (?,?,?,?,?,?)""",
                ("MEENA501","Meena faculty subject",cse_id,5,"2026-27",meena_id),
            ).lastrowid
            rahul_subject = connection.execute(
                """INSERT INTO subjects (code,name,department_id,semester,academic_year,faculty_id)
                   VALUES (?,?,?,?,?,?)""",
                ("RAHUL501","Rahul faculty subject",cse_id,5,"2026-27",rahul_id),
            ).lastrowid
            connection.executemany(
                "INSERT INTO subject_enrollments (student_id,subject_id) VALUES (?,?)",
                [(ravi_id,meena_subject),(ravi_id,rahul_subject)],
            )
            meena_assignment = connection.execute(
                """INSERT INTO assignments (subject_id,title,deadline,max_marks)
                   VALUES (?,?,?,?)""",
                (meena_subject,"Meena pending review","2027-01-15T17:00",20),
            ).lastrowid
            connection.execute(
                """INSERT INTO assignments (subject_id,title,deadline,max_marks)
                   VALUES (?,?,?,?)""",
                (rahul_subject,"Rahul only assignment","2027-01-16T17:00",20),
            )
            connection.execute(
                "INSERT INTO submissions (assignment_id,student_id) VALUES (?,?)",
                (meena_assignment,ravi_id),
            )
            unassigned_student = connection.execute(
                "INSERT INTO students (name,email,department_id) VALUES (?,?,?)",
                ("Unassigned student", "", cse_id),
            ).lastrowid

        faculty_client, _ = self.create_role_client("faculty", "meena-faculty", professor_id=meena_id)
        landing = faculty_client.get("/")
        self.assertEqual(landing.status_code, 302)
        self.assertIn("/faculty/portal", landing.location)
        faculty_page = faculty_client.get("/faculty/portal")
        self.assertEqual(faculty_page.status_code, 200)
        self.assertIn(b"FACULTY WORKSPACE", faculty_page.data)
        self.assertIn(b"Meena faculty subject", faculty_page.data)
        self.assertIn(b"Meena pending review", faculty_page.data)
        self.assertNotIn(b"Rahul faculty subject", faculty_page.data)
        self.assertNotIn(b"Rahul only assignment", faculty_page.data)
        self.assertIn(b"Submissions to review", faculty_page.data)
        student_profile_page = faculty_client.get(f"/students/{ravi_id}")
        self.assertEqual(student_profile_page.status_code, 200)
        self.assertNotIn(b"Guardian", student_profile_page.data)
        self.assertEqual(faculty_client.get(f"/students/{unassigned_student}").status_code, 403)
        self.assertEqual(faculty_client.get(f"/departments/{ece_id}").status_code, 403)

        student_client, _ = self.create_role_client("student", "ravi-overview", student_id=ravi_id)
        student_landing = student_client.get("/")
        self.assertEqual(student_landing.status_code, 302)
        self.assertIn("/student", student_landing.location)
        student_page = student_client.get("/student")
        self.assertEqual(student_page.status_code, 200)
        self.assertIn(b"Ravi.</span>", student_page.data)
        self.assertIn(b"Outstanding assignments", student_page.data)
        self.assertIn(b"Rahul only assignment", student_page.data)
        self.assertNotIn(b"Total students", student_page.data)

    def test_exports_receipts_and_database_backup(self):
        with database.connect() as connection:
            ravi_id = connection.execute("SELECT id FROM students WHERE name='Ravi'").fetchone()["id"]
        self.client.post(
            "/finance/fees",
            data={
                "student_id": ravi_id, "semester": 1, "academic_year": "2026-27",
                "tuition": 100, "due_date": "2027-01-01",
            },
        )
        with database.connect() as connection:
            fee_id = connection.execute("SELECT id FROM fees WHERE student_id=?", (ravi_id,)).fetchone()["id"]
        self.client.post(
            "/finance/payments",
            data={
                "fee_id": fee_id, "amount": 30, "payment_method": "Cash",
                "payment_date": "2026-11-16", "reference_number": "receipt-001",
            },
        )
        self.client.post(
            "/finance/payments",
            data={
                "fee_id": fee_id, "amount": 20, "payment_method": "Online Payment",
                "payment_date": "2026-11-16", "reference_number": "pending-001",
            },
        )
        with database.connect() as connection:
            payments = connection.execute(
                "SELECT id,status FROM payments ORDER BY id"
            ).fetchall()
        receipt = self.client.get(f"/finance/payments/{payments[0]['id']}/receipt.pdf")
        self.assertEqual(receipt.status_code, 200)
        self.assertEqual(receipt.mimetype, "application/pdf")
        self.assertTrue(receipt.data.startswith(b"%PDF"))
        self.assertEqual(self.client.get(f"/finance/payments/{payments[1]['id']}/receipt.pdf").status_code, 404)
        self.client.post(f"/finance/payments/{payments[1]['id']}/verify")
        with database.connect() as connection:
            self.assertEqual(
                connection.execute(
                    "SELECT status FROM payments WHERE id=?", (payments[1]["id"],)
                ).fetchone()[0],
                "Recorded",
            )
        self.assertEqual(
            self.client.get(f"/finance/payments/{payments[1]['id']}/receipt.pdf").status_code,
            200,
        )

        for extension, content_type in [
            ("csv", "text/csv"), ("xlsx", "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"),
            ("pdf", "application/pdf"),
        ]:
            response = self.client.get(f"/reports/payments.{extension}")
            self.assertEqual(response.status_code, 200)
            self.assertIn(content_type, response.content_type)
        with database.connect() as connection:
            connection.execute(
                "INSERT INTO students (name,email) VALUES (?,?)",
                ("=1+1", "formula@example.edu"),
            )
        csv_export = self.client.get("/reports/students.csv")
        self.assertIn(b"'=1+1", csv_export.data)
        backup = self.client.get("/admin/backup")
        self.assertEqual(backup.status_code, 200)
        self.assertTrue(backup.data.startswith(b"SQLite format 3"))

    def test_timetable_conflicts_and_submission_review_transition(self):
        with database.connect() as connection:
            department_id = connection.execute("SELECT id FROM departments WHERE code='CSE'").fetchone()["id"]
            professor_id = connection.execute("SELECT id FROM professors WHERE name='Meena'").fetchone()["id"]
            student_id = connection.execute("SELECT id FROM students WHERE name='Ravi'").fetchone()["id"]
            subject_ids = []
            for code in ("TEST101", "TEST102"):
                subject_ids.append(connection.execute(
                    """INSERT INTO subjects (code,name,department_id,semester,academic_year,faculty_id)
                       VALUES (?,?,?,?,?,?)""",
                    (code,code,department_id,1,"2026-27",professor_id),
                ).lastrowid)
            connection.execute(
                """INSERT INTO assignments (subject_id,title,deadline,max_marks)
                   VALUES (?,?,?,?)""",
                (subject_ids[0],"Review task","2027-01-01T12:00",10),
            )
            assignment_id = connection.execute(
                "SELECT id FROM assignments WHERE title='Review task'"
            ).fetchone()["id"]
            submission_id = connection.execute(
                """INSERT INTO submissions (assignment_id,student_id,file_name)
                   VALUES (?,?,?)""",
                (assignment_id,student_id,"review-file.txt"),
            ).lastrowid
        first = self.client.post(
            "/academics/timetable",
            data={
                "subject_id": subject_ids[0], "section": "A", "day_of_week": 0, "period": 1,
                "start_time": "09:00", "end_time": "10:00", "classroom": "R101",
                "faculty_id": professor_id,
            },
        )
        self.assertEqual(first.status_code, 302)
        self.client.post(
            "/academics/timetable",
            data={
                "subject_id": subject_ids[1], "section": "B", "day_of_week": 0, "period": 2,
                "start_time": "09:30", "end_time": "10:30", "classroom": "R102",
                "faculty_id": professor_id,
            },
        )
        with database.connect() as connection:
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM timetable").fetchone()[0], 1)
        self.client.post(
            "/academics/exams",
            data={
                "subject_id": subject_ids[0], "exam_type": "Internal",
                "exam_date": "2027-01-15", "start_time": "13:00", "end_time": "14:00",
                "hall": "H101",
            },
        )
        self.client.post(
            "/academics/exams",
            data={
                "subject_id": subject_ids[1], "exam_type": "Internal",
                "exam_date": "2027-01-15", "start_time": "13:30", "end_time": "14:30",
                "hall": "H102",
            },
        )
        with database.connect() as connection:
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM exams").fetchone()[0], 1)

        self.assertEqual(
            self.client.post(f"/academics/submissions/{submission_id}/start-review").status_code,
            302,
        )
        with database.connect() as connection:
            self.assertEqual(
                connection.execute("SELECT status FROM submissions WHERE id=?", (submission_id,)).fetchone()[0],
                "Under Evaluation",
            )
        self.client.post(
            f"/academics/submissions/{submission_id}/evaluate",
            data={"marks": 8, "feedback": "Good work."},
        )
        with database.connect() as connection:
            self.assertEqual(
                connection.execute("SELECT status FROM submissions WHERE id=?", (submission_id,)).fetchone()[0],
                "Evaluated",
            )


if __name__ == "__main__":
    unittest.main()
