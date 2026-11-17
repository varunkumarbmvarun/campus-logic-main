# CampusLogic: College Knowledge Base using First-Order Logic

CampusLogic is a local Flask application for college records, role-based academic workflows, and a forward-chaining First-Order Logic knowledge base. It uses SQLite for persistent storage and deliberately supports a defined set of questions rather than claiming unrestricted natural-language understanding.

## Features

- Dashboard statistics backed by the local database.
- Student, professor, course, and department management, including profile details and secure file uploads.
- Enrollments, professor assignments, course offerings, and department memberships synchronized with the logical knowledge base.
- Course records may be organized by semester; existing student, course, and subject values appear as selection/autocomplete suggestions rather than literal `None` values.
- Semester-5 students are automatically enrolled in matching required subjects. HODs and administrators assign one subject from each elective group.
- HODs and administrators can import the verified VTU 2022 Semester-5 catalog for supported branches from the Subjects page; other branches remain available for manual entry until their official subject lists are verified.
- Subjects, internal marks, assignments/submissions and evaluation, student/faculty attendance, fees/payments, notifications with scoped attachments, timetable, and examination scheduling.
- Role-scoped in-app messaging, online/away presence, accepted/declined audio/video calls, and opt-in screen sharing without exposing phone numbers.
- Super/college administrators can pause or restore faculty in-app contact channels; pausing ends their active calls and clears call signaling data.
- Query lab, structured queries, query explanations/history, and an explorer separating asserted facts from derived facts.
- Role-based access for super administrators, college administrators, HODs, faculty, students, and finance staff.
- Role-specific landing pages: administrators see college-wide metrics, faculty see their classes and review queue, and students see their own academic, attendance, assignment, timetable, exam, and fee summaries.
- Audit events, password changes, account activation controls, payment receipt PDFs, CSV/Excel/PDF reports, and a super-admin SQLite backup download.
- Responsive interface with scoped views for student, faculty, HOD, and finance accounts.

## Requirements and installation

Python 3.11 or newer is recommended. Install the dependencies from the project directory:

```powershell
py -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
```

## Bootstrap the first administrator

There are no default credentials. Before the first launch, set a private username and a password of at least 12 characters in the current PowerShell session:

```powershell
$env:BOOTSTRAP_ADMIN_USERNAME = "your-admin-name"
$env:BOOTSTRAP_ADMIN_PASSWORD = "use-a-private-password-of-12-or-more-characters"
$env:SECRET_KEY = "set-a-long-random-secret-for-your-environment"
python app.py
```

The application creates the first super-administrator only when the user table is empty. Bootstrap environment variables do not reset or replace accounts on later launches. Sign in at <http://127.0.0.1:5000>, then use **User accounts** to create role-linked accounts. All accounts can change their own password; only a super administrator can enable/disable users. The final active super-administrator cannot be disabled.

If you already initialized the database without bootstrap credentials, stop the application and relaunch with both bootstrap variables set. If no users exist, initialization will provision the first administrator. Keep `SECRET_KEY` stable across restarts in a deployed environment.

The built-in Flask server is for local development and demonstrations. For deployment, use a production WSGI server, HTTPS, a persistent private `SECRET_KEY`, filesystem/database backups, and appropriate operating-system file permissions. Uploaded files are limited to 8 MB and stored under `instance/uploads`.

## Role overview

- **Super administrator:** all college data, users, security audit, backup, reports, and knowledge-base administration.
- **College administrator:** college-wide management, user administration, reports, audit, and knowledge-base administration.
- **HOD:** department-scoped academic and faculty attendance workflows, department notices, query lab, and reports.
- **Faculty:** assigned-subject records, marks, attendance, assignments/evaluation, scoped reports, and query lab.
- **Student:** own portal/profile, enrolled subjects, own marks/attendance/fees/payments, timetable/exam calendar, and notices.
- **Finance:** fee schedules, payment recording/history, payment receipts, and payment reports.

The **Overview** link opens the appropriate workspace for each role. Faculty data is limited to subjects they teach; faculty can view only enrolled students' academic profiles, while private contact, guardian, and address fields remain hidden. Student accounts only see their own profile and records.

## Faculty communication

Open **Faculty connect** to message eligible contacts. Students see faculty who teach their enrolled subjects; faculty see students enrolled in their subjects. Calls are invitations that the recipient can accept or decline. Users can mute their microphone, toggle their camera, and start/stop screen sharing. Screen sharing is opt-in, call media is not recorded, and call signaling data is removed when a call ends. Administrators can pause/restore faculty channels under **Faculty contact settings**. Messages are stored in the local SQLite database and are not end-to-end encrypted; use HTTPS in any deployment and protect the database and backups.

Local calls work from `http://127.0.0.1:5000` in a browser that supports WebRTC. The default public STUN service may observe connection metadata; calls across restrictive networks may need a TURN service. Configure `WEBRTC_ICE_SERVERS` as a JSON array of ICE server objects in the app's environment; use short-lived TURN credentials and do not commit credentials. Screen sharing and camera/microphone access require a secure browser context (localhost is permitted for development; deployed sites need HTTPS).

## Semester-5 core and elective enrollment

When a student is saved in semester 5, the app enrolls them in matching required subjects for their department and academic year. When a required subject is added later, currently enrolled semester-5 students are enrolled automatically. Electives are not auto-enrolled: a HOD or administrator assigns them from **Subjects**. Assign an elective group to its options (for example, `Professional Elective I`); the app prevents assigning more than one subject from the same group to a student. Department, semester, and academic year must match when enrolling a student.

The **Load verified Semester 5 catalog** form imports the source-backed branch entries currently packaged with the app into the selected department and academic year. Importing is repeat-safe: existing subjects are left unchanged. This package currently verifies Civil Engineering, Computer Science & Engineering, Electrical & Electronics Engineering, Industrial Engineering & Management, Robotics & Automation (mapped to Automation and Robotics through a VTU circular), and Silk Technology. The other VTU branch subject lists are not guessed; consult VTU's [2022 scheme index](https://vtu.ac.in/b-e-scheme-syllabus/) and add them manually until verified catalog entries are added.

## Demonstration data and queries

On first initialization, the app creates a sample knowledge base with Ravi, Ananya, and Kiran; professors Meena and Rahul; AI, DBMS, and Python courses; CSE and ECE; and related enrollments, teaching assignments, memberships, and offerings. Existing records are preserved during additive schema migrations.

Try these questions in **Query lab**:

- Who teaches AI?
- Which students are enrolled in AI?
- Is Ravi associated with CSE?
- Which professors belong to CSE?
- Which courses are offered by CSE?
- Which students study DBMS?
- Which department is associated with Meena?
- Which professors teach courses offered by CSE?

Additional supported questions cover attendance, fees, assignments, subjects, and faculty assignments. The parser intentionally supports specific patterns; unsupported questions receive a clear message. Query answers are calculated from stored facts and inference rules, not canned response text.

## Logical model

Facts use predicates such as `Student(Ravi)`, `Enrolled(Ravi, AI)`, and `OfferedBy(AI, CSE)`. Record edits rebuild record-managed facts while preserving manual assertions. Forward chaining applies declarative rules to a fixed point and prevents duplicates:

1. `Student(x) ∧ Enrolled(x, y) → Studies(x, y)`
2. `Professor(x) → ∃y BelongsTo(x, y)`
3. `Course(x) ∧ Department(y) ∧ OfferedBy(x, y) → CourseOf(x, y)`
4. `Studies(x, y) ∧ OfferedBy(y, z) → AssociatedWith(x, z)`
5. `Teaches(x, y) ∧ OfferedBy(y, z) → AssociatedWith(x, z)`

Rule 2 is treated as an existential constraint rather than an executable witness-generating rule. A professor with no known department is reported as unresolved; the engine does not invent a department.

## Payments and exports

Finance staff can record manual transactions and verify or reject pending entries. **Online Payment** entries remain **Pending Verification** and do not reduce balances until staff verify them; no payment gateway is integrated. PDF receipts are available only for recorded payments. CSV and Excel exports neutralize formula-like text cells.

## Project structure

```text
app.py                  Flask routes, authentication, and app entry point
college_modules.py      Academic, finance, attendance, communication, and reports
database.py             SQLite schema, additive migrations, and sample data
security.py             Role checks, CSRF, and audit helpers
knowledge_base.py       Fact validation, synchronization, and query history
fol_engine.py           Predicate matching and forward chaining
inference_rules.py      Declarative inference rules
query_processor.py      Supported query parsing and evaluation
templates/              Jinja pages
static/css/style.css    Responsive styling
static/js/app.js        Search, query-builder, and UI interactions
tests/                  Logic and Flask integration tests
requirements.txt        Flask, Excel, and PDF dependencies
```

## Tests

Run the automated suite from the project directory:

```powershell
python -m unittest discover -s tests -v
```
