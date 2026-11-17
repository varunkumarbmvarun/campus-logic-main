"""Persistence and validation for the explicitly stored FOL knowledge base."""

import sqlite3

from database import connect
from fol_engine import Fact, ForwardChainer


UNARY_PREDICATES = {
    "Student", "Professor", "Course", "Department", "Subject", "HOD", "Assignment",
    "Payment", "Fee",
}
RELATION_TYPES = {
    "Enrolled": (("Student",), ("Course", "Subject")),
    "Teaches": (("Professor",), ("Course", "Subject")),
    "BelongsTo": (("Student", "Professor", "HOD"), ("Department",)),
    "OfferedBy": (("Course", "Subject"), ("Department",)),
    "AssignedHOD": (("HOD",), ("Department",)),
    "HasInternalMarks": (("Student",), ("Subject",)),
    "HasAttendance": (("Student",), ("Subject",)),
    "Attends": (("Student",), ("Subject",)),
    "Submitted": (("Student",), ("Assignment",)),
    "HasAttendance": (("Student",), ("Subject",)),
    "PaidFees": (("Student",), ("Payment",)),
}
PREDICATES = UNARY_PREDICATES | set(RELATION_TYPES)


class KnowledgeBaseError(ValueError):
    pass


def list_base_facts():
    with connect() as connection:
        return [dict(row) for row in connection.execute(
            "SELECT id, predicate, arg1, arg2, source FROM logical_facts ORDER BY predicate, arg1, arg2"
        )]


def inference_snapshot():
    rows = list_base_facts()
    base_facts = [
        (row["predicate"], row["arg1"], row["arg2"])
        for row in rows
    ]
    base_facts.extend(_metric_facts())
    engine = ForwardChainer(
        base_facts
    )
    facts = engine.run()
    return rows, engine, facts


def _metric_facts():
    facts = []
    with connect() as connection:
        threshold = float(connection.execute(
            "SELECT setting_value FROM app_settings WHERE setting_key='attendance_threshold'"
        ).fetchone()[0])
        attendance_rows = connection.execute(
            """SELECT s.name, COUNT(*) AS conducted,
                      SUM(CASE WHEN a.status IN ('Present','On Duty') THEN 1 ELSE 0 END) AS attended
               FROM attendance a JOIN students s ON s.id=a.student_id
               GROUP BY s.id"""
        ).fetchall()
        for row in attendance_rows:
            percentage = 100.0 * row["attended"] / row["conducted"] if row["conducted"] else 0.0
            facts.append(("AttendancePercentage", row["name"], f"{percentage:.2f}"))
            if percentage < threshold:
                facts.append(("BelowThreshold", f"{percentage:.2f}", ""))
        balances = connection.execute(
            """SELECT s.name, f.id,
                      (f.tuition+f.examination+f.laboratory+f.library+f.transport+f.hostel+f.other-f.concession)
                      - COALESCE(SUM(CASE WHEN p.status='Recorded' THEN p.amount ELSE 0 END), 0) AS balance
               FROM fees f JOIN students s ON s.id=f.student_id
               LEFT JOIN payments p ON p.fee_id=f.id
               GROUP BY f.id"""
        ).fetchall()
        for row in balances:
            balance = max(0.0, float(row["balance"]))
            facts.append(("FeeBalance", row["name"], f"{balance:.2f}"))
            if balance > 0:
                facts.append(("PositiveBalance", f"{balance:.2f}", ""))
        for row in connection.execute(
            """SELECT a.id,a.title,st.name FROM assignments a
               JOIN subject_enrollments se ON se.subject_id=a.subject_id
               JOIN students st ON st.id=se.student_id"""
        ):
            facts.append(("AssignmentAssigned", row["name"], row["title"]))
            submitted = connection.execute(
                "SELECT 1 FROM submissions WHERE assignment_id=? AND student_id IN "
                "(SELECT id FROM students WHERE name=?)",
                (row["id"], row["name"]),
            ).fetchone()
            if not submitted:
                facts.append(("NoSubmission", row["name"], row["title"]))
    return facts


def add_fact(predicate, arg1, arg2=""):
    predicate = (predicate or "").strip()
    arg1 = (arg1 or "").strip()
    arg2 = (arg2 or "").strip()
    canonical_predicate = next(
        (known for known in PREDICATES if known.casefold() == predicate.casefold()), None
    )
    if canonical_predicate is None:
        raise KnowledgeBaseError("Choose one of the supported predicates.")
    predicate = canonical_predicate
    if not arg1 or (predicate in RELATION_TYPES and not arg2):
        raise KnowledgeBaseError("Every predicate argument is required.")
    if predicate in UNARY_PREDICATES and arg2:
        raise KnowledgeBaseError(f"{predicate} takes exactly one argument.")

    with connect() as connection:
        if predicate in UNARY_PREDICATES:
            if connection.execute(
                "SELECT 1 FROM logical_facts WHERE predicate=? AND arg1=?",
                (predicate, arg1),
            ).fetchone():
                raise KnowledgeBaseError(f"{predicate}({arg1}) already exists.")
        else:
            allowed_types = RELATION_TYPES[predicate]
            for argument, type_names in zip((arg1, arg2), allowed_types):
                matches_type = any(
                    connection.execute(
                        "SELECT 1 FROM logical_facts WHERE predicate=? AND arg1=?",
                        (type_name, argument),
                    ).fetchone()
                    for type_name in type_names
                )
                if not matches_type:
                    raise KnowledgeBaseError(
                        f"{argument} must match a known {' or '.join(type_name.lower() for type_name in type_names)}."
                    )
        try:
            connection.execute(
                "INSERT INTO logical_facts (predicate, arg1, arg2, source) VALUES (?, ?, ?, 'manual')",
                (predicate, arg1, arg2),
            )
        except sqlite3.IntegrityError as exc:
            raise KnowledgeBaseError("That fact already exists.") from exc


def remove_fact(fact_id):
    with connect() as connection:
        fact = connection.execute(
            "SELECT predicate, arg1, arg2 FROM logical_facts WHERE id=? AND source='manual'",
            (fact_id,),
        ).fetchone()
        if fact and fact["predicate"] in UNARY_PREDICATES:
            reference_queries = {
                "Student": (
                    "SELECT predicate FROM logical_facts WHERE predicate='Enrolled' AND arg1=? LIMIT 1",
                    (fact["arg1"],),
                ),
                "Professor": (
                    "SELECT predicate FROM logical_facts WHERE arg1=? AND predicate IN ('Teaches', 'BelongsTo') LIMIT 1",
                    (fact["arg1"],),
                ),
                "Course": (
                    "SELECT predicate FROM logical_facts WHERE (predicate IN ('Enrolled', 'Teaches') AND arg2=?) OR (predicate='OfferedBy' AND arg1=?) LIMIT 1",
                    (fact["arg1"], fact["arg1"]),
                ),
                "Department": (
                    "SELECT predicate FROM logical_facts WHERE predicate IN ('BelongsTo', 'OfferedBy') AND arg2=? LIMIT 1",
                    (fact["arg1"],),
                ),
            }
            query, parameters = reference_queries[fact["predicate"]]
            references = connection.execute(
                query, parameters
            ).fetchone()
            if references:
                raise KnowledgeBaseError(
                    f"Remove related {references['predicate']} facts before removing this entity fact."
                )
        cursor = connection.execute(
            "DELETE FROM logical_facts WHERE id=? AND source='manual'", (fact_id,)
        )
        if cursor.rowcount == 0:
            raise KnowledgeBaseError(
                "Only manually added facts can be removed here. Edit the related record instead."
            )


def save_query(query_text, result):
    with connect() as connection:
        connection.execute(
            "INSERT INTO query_history (query_text, result) VALUES (?, ?)",
            (query_text, result),
        )


def query_history(limit=12):
    with connect() as connection:
        return [dict(row) for row in connection.execute(
            "SELECT query_text, result, created_at FROM query_history ORDER BY id DESC LIMIT ?",
            (limit,),
        )]
