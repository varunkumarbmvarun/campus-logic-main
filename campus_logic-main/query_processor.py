"""Reliable parser and evaluator for the application's supported query patterns."""

import re

from fol_engine import Fact
from inference_rules import RULES


PATTERNS = [
    ("who_teaches", re.compile(r"who\s+teaches\s+(.+?)\??", re.I)),
    ("who_teaches", re.compile(r"(?:which|what)\s+faculty\s+members\s+teach\s+(.+?)\??", re.I)),
    ("hod_of_department", re.compile(r"who\s+is\s+the\s+hod\s+of\s+(.+?)\??", re.I)),
    ("low_attendance", re.compile(r"which\s+students\s+have\s+low\s+attendance\??", re.I)),
    ("pending_fees", re.compile(r"which\s+students\s+have\s+pending\s+fees\??", re.I)),
    ("pending_submissions", re.compile(r"which\s+students\s+have\s+not\s+submitted\s+assignments\??", re.I)),
    ("students_in_department_subjects", re.compile(r"which\s+students\s+are\s+enrolled\s+in\s+subjects\s+offered\s+by\s+(.+?)\??", re.I)),
    ("students_enrolled", re.compile(r"(?:which|what)\s+students\s+are\s+enrolled\s+in\s+(.+?)\??", re.I)),
    ("is_associated", re.compile(r"is\s+(.+?)\s+associated\s+with\s+(.+?)\??", re.I)),
    ("professors_in_department", re.compile(r"(?:which|what)\s+professors\s+(?:belong\s+to|are\s+in)\s+(.+?)\??", re.I)),
    ("courses_offered", re.compile(r"(?:which|what)\s+courses\s+are\s+offered\s+by\s+(.+?)\??", re.I)),
    ("students_study", re.compile(r"(?:which|what)\s+students\s+study\s+(.+?)\??", re.I)),
    ("department_of_professor", re.compile(r"(?:which|what)\s+department\s+is\s+associated\s+with\s+(.+?)\??", re.I)),
    ("professors_teaching_department", re.compile(r"(?:which|what)\s+professors\s+teach\s+courses\s+offered\s+by\s+(.+?)\??", re.I)),
]

BUILDERS = {
    "who_teaches": "Who teaches {value}?",
    "students_enrolled": "Which students are enrolled in {value}?",
    "is_associated": "Is {person} associated with {department}?",
    "professors_in_department": "Which professors belong to {value}?",
    "courses_offered": "Which courses are offered by {value}?",
    "students_study": "Which students study {value}?",
    "department_of_professor": "Which department is associated with {value}?",
    "professors_teaching_department": "Which professors teach courses offered by {value}?",
    "hod_of_department": "Who is the HOD of {value}?",
    "low_attendance": "Which students have low attendance?",
    "pending_fees": "Which students have pending fees?",
    "pending_submissions": "Which students have not submitted assignments?",
    "students_in_department_subjects": "Which students are enrolled in subjects offered by {value}?",
}


class UnsupportedQuery(ValueError):
    pass


def _normalize(value):
    return value.strip().rstrip("?.! ").casefold()


def parse_query(text):
    if not isinstance(text, str) or not text.strip():
        raise UnsupportedQuery("Enter a question using one of the supported patterns.")
    cleaned = text.strip()
    for query_type, pattern in PATTERNS:
        match = pattern.fullmatch(cleaned)
        if match:
            return {"type": query_type, "terms": tuple(part.strip() for part in match.groups())}
    raise UnsupportedQuery(
        "That question pattern is not supported. Use a suggested question or the structured query builder."
    )


def _rule(rule_id):
    return next(rule for rule in RULES if rule["id"] == rule_id)


def evaluate_query(query, facts, engine):
    parsed = parse_query(query) if isinstance(query, str) else query
    values = parsed["terms"]
    values_normalized = tuple(_normalize(value) for value in values)
    base = [fact for fact in facts if fact.source == "base"]
    matches = []
    predicates = set()
    applied_rules = set()
    explanation = ""
    qtype = parsed["type"]

    def add(fact):
        if fact not in matches:
            matches.append(fact)
            predicates.add(fact.predicate)
            if fact.source == "derived":
                applied_rules.add(fact.rule_id)

    def add_with_support(fact):
        add(fact)
        for supporting_fact in engine.supporting_facts(fact):
            add(supporting_fact)

    if qtype == "who_teaches":
        course = values_normalized[0]
        candidates = [fact for fact in base if fact.predicate == "Teaches" and _normalize(fact.arguments[1]) == course]
        for fact in candidates:
            add_with_support(fact)
        answer = [fact.arguments[0] for fact in matches]
        predicates.add("Teaches")
        explanation = "Professor names are selected from stored Teaches(professor, course) facts matching the requested course."
    elif qtype == "students_enrolled":
        course = values_normalized[0]
        candidates = [fact for fact in base if fact.predicate == "Enrolled" and _normalize(fact.arguments[1]) == course]
        for fact in candidates:
            add_with_support(fact)
        answer = [fact.arguments[0] for fact in matches]
        predicates.add("Enrolled")
        explanation = "Student names are selected from stored Enrolled(student, course) facts matching the requested course."
    elif qtype == "is_associated":
        person, department = values_normalized
        candidates = [
            fact for fact in facts
            if fact.predicate == "AssociatedWith"
            and _normalize(fact.arguments[0]) == person
            and _normalize(fact.arguments[1]) == department
        ]
        for fact in candidates:
            add_with_support(fact)
        answer = bool(candidates)
        predicates.add("AssociatedWith")
        explanation = "The engine checks for AssociatedWith(person, department), including facts derived from enrollment/study or teaching and course offerings."
    elif qtype == "professors_in_department":
        department = values_normalized[0]
        candidates = [fact for fact in base if fact.predicate == "BelongsTo" and _normalize(fact.arguments[1]) == department]
        for fact in candidates:
            add(fact)
        answer = [fact.arguments[0] for fact in matches]
        predicates.add("BelongsTo")
        explanation = "Professor names are selected from stored BelongsTo(professor, department) facts."
    elif qtype == "courses_offered":
        department = values_normalized[0]
        candidates = [fact for fact in base if fact.predicate == "OfferedBy" and _normalize(fact.arguments[1]) == department]
        for fact in candidates:
            add(fact)
        answer = [fact.arguments[0] for fact in matches]
        predicates.add("OfferedBy")
        explanation = "Course codes are selected from stored OfferedBy(course, department) facts."
    elif qtype == "students_study":
        course = values_normalized[0]
        candidates = [fact for fact in facts if fact.predicate == "Studies" and _normalize(fact.arguments[1]) == course]
        for fact in candidates:
            add_with_support(fact)
        answer = list(dict.fromkeys(fact.arguments[0] for fact in candidates))
        predicates.update(("Studies", "Student", "Enrolled"))
        explanation = "Studies(student, course) is derived by Rule 1 from Student(student) and Enrolled(student, course)."
    elif qtype == "department_of_professor":
        professor = values_normalized[0]
        candidates = [
            fact for fact in facts
            if fact.predicate in {"BelongsTo", "AssociatedWith"}
            and _normalize(fact.arguments[0]) == professor
        ]
        for fact in candidates:
            add_with_support(fact)
        answer = list(dict.fromkeys(fact.arguments[1] for fact in candidates))
        predicates.update(("BelongsTo", "AssociatedWith"))
        explanation = "Departments come from explicit BelongsTo facts and any AssociatedWith facts derived through courses taught and offered."
    elif qtype == "professors_teaching_department":
        department = values_normalized[0]
        candidates = [
            (teach, offered)
            for teach in base if teach.predicate == "Teaches"
            for offered in base if offered.predicate == "OfferedBy"
            and _normalize(teach.arguments[1]) == _normalize(offered.arguments[0])
            and _normalize(offered.arguments[1]) == department
        ]
        for teach, offered in candidates:
            add(teach)
            add(offered)
        answer = list(dict.fromkeys(teach.arguments[0] for teach, _ in candidates))
        predicates.update(("Teaches", "OfferedBy"))
        explanation = "The engine joins Teaches(professor, course) with OfferedBy(course, department) using the same course."
    elif qtype == "hod_of_department":
        department = values_normalized[0]
        candidates = [
            fact for fact in base
            if fact.predicate == "AssignedHOD"
            and _normalize(fact.arguments[1]) == department
        ]
        for fact in candidates:
            add(fact)
        answer = [fact.arguments[0] for fact in candidates]
        predicates.add("AssignedHOD")
        explanation = "The HOD is read from the department's explicit AssignedHOD(HOD, department) fact."
    elif qtype == "low_attendance":
        candidates = [fact for fact in facts if fact.predicate == "LowAttendance"]
        for fact in candidates:
            add_with_support(fact)
        answer = list(dict.fromkeys(fact.arguments[0] for fact in candidates))
        predicates.update(("AttendancePercentage", "BelowThreshold", "LowAttendance"))
        explanation = "Attendance percentages are calculated from recorded class sessions, compared with the configured minimum, then inferred by Rule 9."
    elif qtype == "pending_fees":
        candidates = [fact for fact in facts if fact.predicate == "PendingFees"]
        for fact in candidates:
            add_with_support(fact)
        answer = list(dict.fromkeys(fact.arguments[0] for fact in candidates))
        predicates.update(("FeeBalance", "PositiveBalance", "PendingFees"))
        explanation = "Recorded fee schedules and verified payments determine each balance; positive balances derive PendingFees by Rule 8."
    elif qtype == "pending_submissions":
        candidates = [fact for fact in facts if fact.predicate == "PendingSubmission"]
        for fact in candidates:
            add_with_support(fact)
        answer = list(dict.fromkeys(fact.arguments[0] for fact in candidates))
        predicates.update(("AssignmentAssigned", "NoSubmission", "PendingSubmission"))
        explanation = "Students on a subject's roster without a submission record are materialized as pending and inferred by Rule 10."
    elif qtype == "students_in_department_subjects":
        department = values_normalized[0]
        offerings = [
            fact for fact in base
            if fact.predicate == "OfferedBy"
            and _normalize(fact.arguments[1]) == department
        ]
        offered_codes = {_normalize(fact.arguments[0]) for fact in offerings}
        enrollments = [
            fact for fact in base
            if fact.predicate == "Enrolled"
            and _normalize(fact.arguments[1]) in offered_codes
        ]
        for fact in offerings + enrollments:
            add(fact)
        answer = list(dict.fromkeys(fact.arguments[0] for fact in enrollments))
        predicates.update(("Enrolled", "OfferedBy"))
        explanation = "The query joins Enrolled(student, subject) with OfferedBy(subject, department) on subject code."
    else:
        raise UnsupportedQuery("Choose a supported query type.")

    rule_names = [_rule(rule_id)["name"] for rule_id in sorted(applied_rules)]
    return {
        "query": query if isinstance(query, str) else _render_query(parsed),
        "parsed": parsed,
        "matched_predicates": sorted(predicates),
        "rules_applied": rule_names,
        "answer": answer,
        "display_answer": (
            "Yes" if answer is True else "No" if answer is False
            else ", ".join(answer) if answer else "No matching facts found."
        ),
        "explanation": explanation,
        "matched_facts": [
            {
                "predicate": fact.predicate,
                "arguments": list(fact.arguments),
                "source": fact.source,
                "rule_id": fact.rule_id or None,
                "derivation": engine.explain(fact),
            }
            for fact in matches
        ],
    }


def _render_query(parsed):
    query_type = parsed["type"]
    terms = parsed["terms"]
    if query_type == "is_associated":
        return f"Is {terms[0]} associated with {terms[1]}?"
    if query_type in {"low_attendance", "pending_fees", "pending_submissions"}:
        return BUILDERS[query_type]
    return BUILDERS[query_type].format(value=terms[0])


def build_query(query_type, values):
    if query_type not in BUILDERS:
        raise UnsupportedQuery("Choose a supported structured query.")
    if query_type in {"low_attendance", "pending_fees", "pending_submissions"}:
        return BUILDERS[query_type]
    if query_type == "is_associated":
        person = (values.get("person") or "").strip()
        department = (values.get("department") or "").strip()
        if not person or not department:
            raise UnsupportedQuery("Provide both a person and a department.")
        return BUILDERS[query_type].format(person=person, department=department)
    value = (values.get("value") or "").strip()
    if not value:
        raise UnsupportedQuery("Enter a value for the selected query.")
    return BUILDERS[query_type].format(value=value)
