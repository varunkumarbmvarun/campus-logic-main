# CampusLogic: College Knowledge Base Using First-Order Logic

## Project report

**Prepared:** 16 November 2026  
**Project type:** Web-based academic information and symbolic AI system  
**Application:** Flask, SQLite, Python, Jinja templates  

> This report is organized into 15 page-sized sections. Actual printed page count depends on font, margins, and paper size. No student, institution, or supervisor details have been invented; add them to the cover page if required.

\newpage

## Page 1 — Abstract and project overview

Colleges maintain information about students, faculty, subjects, courses, departments, enrollment, attendance, fees, and assignments. These records are often stored in separate forms or databases. A conventional database can answer questions that directly match stored columns, but it does not automatically explain relationships that follow from multiple records. For example, if a student is enrolled in a course and that course is offered by a department, the system can infer that the student is associated with that department.

CampusLogic is a local web application designed to combine college record management with a small, explainable symbolic artificial-intelligence engine. The application stores operational records in SQLite, converts selected records into logical facts, and applies positive Horn-clause rules through forward chaining. Users can query supported questions and inspect which facts and rules contributed to an answer.

The AI component is deliberately bounded. It is not a machine-learning model or a general-purpose chatbot. It uses explicit facts, a finite set of declarative rules, a controlled question parser, and traceable derivations. This design makes its conclusions reproducible and relatively easy to audit.

The wider application includes role-based academic workflows, student/faculty portals, subject and attendance management, finance records, reports, and private faculty communication. In the current implementation, semester-five required subjects can be assigned automatically, elective groups are administered by HODs or administrators, and a source-verified subset of VTU 2022 curricula can be imported.

The report presents the problem definition, AI formulation, knowledge model, algorithms, implementation architecture, visual interface, comparisons, testing, limitations, and future improvements. It describes the implemented system rather than claiming unmeasured performance or capabilities.

**Keywords:** First-Order Logic, knowledge base, Horn clauses, forward chaining, explainable AI, Flask, SQLite, college information system.

\newpage

## Page 2 — Introduction and background

Academic institutions create many related records. A student belongs to a department, enrolls in subjects, submits assignments, attends classes, and may have a fee balance. A professor teaches subjects, may have a department membership, and can assess students. These data are useful individually, but their value increases when the system can connect them.

In a record-only application, a user might need to open several pages or write a database join to answer “Which students study AI?” or “Is Ravi associated with CSE?” The answer may depend on several stored relationships rather than a single field. The system should also make clear why an answer was produced, because academic decisions should not rely on unexplained outputs.

CampusLogic approaches this issue as a knowledge-representation problem. It stores data as typed entities and relationships, represents selected relationships as logical predicates, and applies declared inference rules. If the facts are `Student(Ravi)`, `Enrolled(Ravi, AI)`, and `OfferedBy(AI, CSE)`, rules can derive `Studies(Ravi, AI)` and `AssociatedWith(Ravi, CSE)`.

The application separates three concerns:

1. **Operational records:** forms and SQLite tables hold the authoritative college data.
2. **Knowledge representation:** record-managed and manually asserted facts describe entities and relationships.
3. **Reasoning and presentation:** the inference engine derives consequences, and the query interface displays answers with supporting evidence.

The system also includes ordinary information-system features such as authentication, role checks, audit events, reports, and communication tools. These features help people use the college data but are not themselves the logical reasoning algorithm. This distinction is important in an AI project report: the AI contribution is symbolic reasoning and explanation, not a claim that every application feature is intelligent.

\newpage

## Page 3 — Problem definition

### Problem statement

The problem addressed by this project is the difficulty of consolidating academic records and answering relationship-based questions consistently. Data may be distributed across student, faculty, course, subject, department, attendance, assignment, and finance records. Direct database queries can retrieve explicit values, but users may not know how those records connect or how to interpret the resulting joins.

The project therefore aims to build a role-protected college knowledge system that:

- stores core institutional entities and relationships;
- synchronizes selected records into a logical knowledge base;
- infers additional facts using transparent rules;
- accepts a controlled set of natural-language-style questions;
- returns an answer and a trace of relevant facts and rules; and
- presents management workflows appropriate to different user roles.

### Inputs

Inputs include student, professor, department, course, subject, and HOD records; enrollment, teaching, membership, and offering relationships; and operational events such as attendance, assignment submission, and recorded payments. Authorized administrators may also add carefully validated logical facts.

### Outputs

Outputs include ordinary record pages and reports, supported query answers, derived facts, rule explanations, and warnings for unresolved constraints. For example, a positive fee balance can lead to a pending-fee conclusion, while a professor with no known department is reported as unresolved rather than assigned an invented department.

### Constraints and boundaries

The system supports defined question patterns rather than unrestricted conversation. Its rules can only infer what is supported by stored facts and defined logic. Incorrect or incomplete source data can therefore produce incomplete conclusions. The project is a local development application; production deployment requires a production WSGI server, HTTPS, secure secrets, backups, and appropriate operational controls.

The intended outcome is not to replace college administrators or make autonomous high-stakes decisions. It is to organize records, reduce repetitive lookup, and make simple logical consequences visible and explainable.

\newpage

## Page 4 — Objectives, users, and scope

### Objectives

The main objectives are to provide a usable academic record system, connect related records, demonstrate symbolic reasoning, and show evidence for each inferred conclusion. The interface should allow authorized users to manage records without requiring them to write SQL or logic expressions. It should also keep student and faculty views appropriately scoped.

### Intended users

- **Super administrator:** manages the overall system, user accounts, rules, audit events, and backups.
- **College administrator:** manages college-wide records and reports.
- **Head of Department (HOD):** works with department-scoped academic data.
- **Faculty:** views assigned subjects and their related class workflows.
- **Student:** views their own academic and administrative records.
- **Finance staff:** records and verifies fee transactions.

### Functional scope

The application includes dashboards, record management, relationships, a facts-and-rules explorer, a query lab, query history, role-specific pages, attendance, marks, assignments, fees, notices, exports, and faculty communication. Communication includes private messages, presence status, accepted audio/video calls, and opt-in screen sharing; phone numbers are not shown in that interface.

For academic organization, subjects have semester and academic-year fields. Semester-five required subjects are enrolled automatically when a student or matching subject is entered. HODs or administrators assign electives, and the system prevents a student from being assigned multiple options in the same explicitly named elective group.

The bundled VTU 2022 Semester V catalog currently contains verified data for six branches: Civil Engineering, Computer Science & Engineering, Electrical & Electronics Engineering, Industrial Engineering & Management, Silk Technology, and Robotics & Automation (mapped to Automation and Robotics through a VTU circular). It does not claim complete coverage of all VTU programs. Other branch subject lists must be added from verified official sources or entered manually.

The project is primarily a demonstrator and small-scale college workflow application. It is not a replacement for a campus-wide ERP, a payment gateway, a video conferencing provider, or a full natural-language AI service.

\newpage

## Page 5 — AI formulation: knowledge domain and symbols

CampusLogic uses **symbolic AI**. Its knowledge is represented using entity names, predicates, facts, and logical rules. There is no model training step, learned parameter set, or probabilistic prediction in the core inference engine.

### Domain

The domain contains named college entities such as students, professors, courses, subjects, departments, HODs, assignments, and payments. Examples of constants are `Ravi`, `Meena`, `AI`, and `CSE`. Predicates describe properties or relationships between those constants.

### Example predicates

- `Student(x)`: x is a student.
- `Professor(x)`: x is a professor.
- `Course(x)` and `Department(x)`: x has the corresponding entity type.
- `Enrolled(x, y)`: student x is enrolled in course or subject y.
- `Teaches(x, y)`: professor x teaches course or subject y.
- `OfferedBy(x, y)`: course or subject x is offered by department y.
- `BelongsTo(x, y)`: a student or professor x belongs to department y.
- `Studies(x, y)`: x studies y.
- `AssociatedWith(x, y)`: x is associated with department y.
- `AttendancePercentage(x, p)`: x has attendance percentage p.
- `FeeBalance(x, b)`: x has outstanding fee balance b.

The application also uses predicates such as `LowAttendance`, `PendingFees`, `PendingSubmission`, `HasAttendance`, `Submitted`, and `PaidFees`.

### Variable and constant convention

Lowercase symbols such as `x`, `y`, `z`, `p`, and `b` are variables in rules. Names such as `Ravi`, `AI`, and `CSE` are concrete values. A fact is a ground statement with no unbound variables, for example `Enrolled(Ravi, AI)`.

Predicates have defined arity: a unary predicate accepts one argument, while a relationship predicate accepts two. The knowledge-base validation layer checks supported predicate names, required arguments, and known entity types before accepting manually entered facts.

\newpage

## Page 6 — Knowledge acquisition and representation

The relational database is the operational source of truth. Tables store entities and relationships with identifiers and foreign keys. For reasoning, `refresh_record_facts` rebuilds facts marked as record-managed from those tables. It first removes the prior record-derived inputs and then inserts the current facts. Manually entered facts are preserved, so refreshing operational data does not silently erase user assertions.

Examples of fact synchronization include:

- each student row produces `Student(name)`;
- each professor row produces `Professor(name)`;
- each course row produces `Course(code)`;
- each department row produces `Department(code)`;
- enrollment rows produce `Enrolled(student, course)`;
- teaching assignments produce `Teaches(professor, course)`;
- course offerings produce `OfferedBy(course, department)`;
- department memberships produce `BelongsTo(professor, department)`.

Subject enrollment and teaching information are represented as facts too. Assignment submissions, attendance, internal marks, and recorded payments contribute additional predicates.

The `logical_facts` table distinguishes sources such as `record` and `manual`. This source label supports a key lifecycle rule: facts derived from official application records should be changed through their matching management workflow, while a manual fact can be removed in the facts-and-rules explorer.

Some logical values are calculated from numeric records before rule evaluation. Attendance percentages are computed from attendance events and compared with the configured threshold. Fee balances are calculated from fee schedules and payments with `Recorded` status. Assignment rosters are compared with submission records to identify missing submissions. These calculated facts become inputs to the rule engine.

This design combines a relational database with a logical layer: the database handles persistence, constraints, and ordinary reporting; the fact layer provides a uniform representation for rule matching.

\newpage

## Page 7 — AI rules and logical inference

Inference rules are declared in `inference_rules.py`. A rule contains an identifier, a human-readable name, a formula, a description, and—in executable Horn clauses—a body and a head. The body lists conditions; the head is the conclusion that follows when the conditions match.

The main structural rules include:

1. `Student(x) AND Enrolled(x,y) -> Studies(x,y)`
2. `Professor(x) -> EXISTS y BelongsTo(x,y)` — a constraint, not a witness-generating rule.
3. `Course(x) AND Department(y) AND OfferedBy(x,y) -> CourseOf(x,y)`
4. `Studies(x,y) AND OfferedBy(y,z) -> AssociatedWith(x,z)`
5. `Teaches(x,y) AND OfferedBy(y,z) -> AssociatedWith(x,z)`
6. `BelongsTo(x,y) -> AssociatedWith(x,y)`
7. `Teaches(x,y) AND OfferedBy(y,z) -> FacultyOf(x,z)`

Rule 1 is an example of a Horn clause. If the database contains `Student(Ravi)` and `Enrolled(Ravi, AI)`, the engine can derive `Studies(Ravi, AI)`. If `OfferedBy(AI, CSE)` is also known, Rule 4 can derive `AssociatedWith(Ravi, CSE)`.

The existential professor rule is handled differently. The system checks whether each known professor has a known department membership. If one does not, it reports the missing membership. It does not generate an arbitrary department value. This is an important safeguard: a logical constraint must not be satisfied by inventing unsupported data.

Rules 8–10 reason about operational metrics:

- a positive fee balance implies pending fees;
- attendance below the configured threshold implies low attendance;
- an assigned task with no submission implies a pending submission.

The formulas are displayed in the user interface, making the reasoning knowledge inspectable rather than hidden in a model.

\newpage

## Page 8 — Algorithm implementation: forward chaining

The inference engine in `fol_engine.py` implements a small data-driven forward chainer. It works with positive Horn clauses and repeatedly applies rules until no new fact is produced.

### Processing stages

1. Convert tuple inputs into immutable `Fact` objects.
2. Build an index of facts by case-insensitive predicate name.
3. For each executable rule, match the rule body against candidate facts.
4. Unify variables with fact arguments while checking repeated variable bindings.
5. Construct a candidate conclusion from the rule head and substitutions.
6. Add the conclusion only if its normalized key is not already known.
7. Repeat while at least one new fact was added.
8. Return base and derived facts, retaining rule IDs, substitutions, and premise links.

### Pseudocode

```text
known := all base facts
repeat
    changed := false
    for each non-existential rule:
        for each substitution that satisfies the rule body in known:
            conclusion := instantiate rule head
            if conclusion is not already in known:
                add conclusion with its proof premises
                changed := true
until changed is false
return known
```

### Termination and complexity

Duplicate prevention ensures that an already known fact is not repeatedly inserted. The engine does not create arbitrary existential witnesses, and the set of predicates and constants is finite for a query snapshot. Therefore, repeated application reaches a fixed point.

The engine indexes candidate facts by predicate, which avoids scanning unrelated predicate types for every condition. In the worst case, matching a rule with several body predicates can still produce many combinations; actual work depends on fact counts and join selectivity. This engine is appropriate for the application's small, bounded knowledge base, but a much larger deployment would need optimized joins, an agenda algorithm, or a dedicated rule/graph platform.

\newpage

## Page 9 — Query processing and explanation

The query interface accepts structured questions and a controlled set of text patterns. `query_processor.py` uses regular expressions to recognize supported forms such as “Who teaches AI?”, “Which students study AI?”, and “Is Ravi associated with CSE?” It then normalizes names for case-insensitive comparison.

The parser intentionally rejects unsupported language. It does not send arbitrary prompts to a large language model, and it does not pretend to understand every question. Unsupported questions receive a message directing the user to a supported pattern or structured query builder. A JSON query endpoint offers the same constrained evaluation in API form.

Evaluation depends on the query type. Some questions select explicit base facts, such as `Teaches` or `Enrolled`. Other questions search derived facts or join explicit relations. For “Which students study AI?”, the evaluator selects `Studies` facts and provides their support. For “Is Ravi associated with CSE?”, it can match an `AssociatedWith` fact derived through enrollment, study, and course offering.

Each result can include:

- the normalized query;
- the matched predicates;
- the final answer;
- the explanation text;
- the rules applied;
- the matched base or derived facts; and
- a derivation string showing substitutions and premises.

This is a form of proof trace. Users can distinguish an answer read directly from the database from one inferred by a rule. The query page renders these details so users can inspect how a conclusion was reached.

The engine is deterministic: the same fact snapshot, rules, and supported query produce the same result. This supports repeatable testing and auditability. It also means the system's coverage is bounded by the implemented patterns and rules.

\newpage

## Page 10 — Application architecture and implementation

The application uses a layered design:

```text
Browser and Jinja UI
        |
Flask routes, authentication, role checks, CSRF, validation
        |
SQLite operational tables <--> record-to-fact synchronization
        |
Fact snapshot + numeric metric facts
        |
ForwardChainer <--> supported query parser/evaluator
        |
Answer, evidence, and derivation trace
```

`app.py` provides application setup, authentication, dashboard, query, fact, relationship, and management routes. `college_modules.py` contains academic, finance, attendance, communications, and report workflows. `database.py` declares the SQLite schema, additive migrations, sample data, and fact synchronization. `security.py` supplies security helpers, role checks, CSRF handling, and audit support.

The reasoning logic is separated into `inference_rules.py`, `fol_engine.py`, `knowledge_base.py`, and `query_processor.py`. This separation makes the rule definitions, inference algorithm, fact lifecycle, and question handling easier to explain and test independently.

HTML is rendered with Jinja templates; CSS and JavaScript provide responsive presentation and basic interactions. The database is local SQLite. The sample dataset demonstrates students Ravi, Ananya, and Kiran; professors Meena and Rahul; courses such as AI, DBMS, and Python; and departments such as CSE and ECE.

Role checks restrict access to pages and records. HOD/faculty query behavior is scoped, while students see student-specific portals and records. Separate non-AI workflows include private messages, WebRTC calls, attendance, academic marks, and finance. The reasoning engine operates on the facts supplied by the academic system; it does not handle live call media or make communication decisions.

\newpage

## Page 11 — Visualization and user interface

The application presents knowledge and reasoning through several visual components. The administrative dashboard displays live summary cards for counts and indicators such as students, professors, courses, departments, logical facts, inference rules, subjects, assignments, low attendance, marks, fees, and transactions. These are status cards, not predictive charts.

The **Relationships** page presents enrollment, teaching assignment, department membership, and course offering as labeled binary relations. Each relation is shown with its predicate notation and connected records. This gives users a visual way to maintain the links from which the inference engine can derive additional facts.

The **Facts & rules** explorer separates stored facts from derived facts. Stored facts include a source marker to distinguish record-managed data from manual assertions. Derived rows show the inferred predicate and rule ID. The page also displays the existential department-membership status for professors and the complete declarative rule list.

The **Query lab** provides a structured query builder, supported question patterns, a result card, matched predicates, rules applied, and a matched-facts list. Each matched fact includes its source and derivation explanation. This visualization is especially important: it converts a logical proof path into information users can inspect.

The interface uses tables, badges, cards, and relationship arrows rather than interactive graph layouts. A future graph visualization could show entities as nodes and relations as edges, with color or shape distinguishing base facts from inferred facts. If added, it should preserve filters and role-based access and make the underlying evidence available in text for accessibility.

Visualization in this project is therefore both operational and explanatory. It helps users navigate college data and exposes the internal logic behind conclusions rather than merely showing a final answer.

\newpage

## Page 12 — Comparison with alternative approaches

| Approach | Strengths | Limitations compared with CampusLogic |
|---|---|---|
| Relational database queries only | Mature storage, constraints, and efficient direct retrieval | Relationship logic must be repeated in SQL joins; explanations of inferred conclusions are not built in |
| Symbolic rules and forward chaining | Explicit knowledge, deterministic inference, traceable premises and rules | Requires rules to be authored and maintained; limited to represented facts and supported patterns |
| Machine-learning classifier | Can learn statistical patterns from examples | Requires suitable labeled data; predictions may be probabilistic and less directly explainable; not necessary for the current logical tasks |
| Large-language-model chatbot | Flexible language interaction | May produce unsupported statements; requires controls and grounding; not used by this project |
| Backward chaining | Starts from a goal and searches for supporting facts; may avoid deriving irrelevant facts | Requires goal-directed rule traversal; current system instead computes a forward fixed point for a small fact set |

CampusLogic combines relational persistence with symbolic inference rather than treating them as competitors. SQLite remains the correct place for records, unique constraints, and transaction history. The logical layer is used for consequences that span records and for explanation of why those consequences follow.

There are no measured latency or accuracy benchmark results in this report. The comparison is architectural and qualitative. A fair performance comparison would require a fixed dataset, equivalent query workload, repeated runs, and measured response time and resource use.

The choice of symbolic AI is appropriate when rules are explicit, institutional knowledge is limited, and a user should be able to inspect the reasoning. It is less suitable when the task depends on noisy unstructured text, image recognition, or patterns that cannot be specified as rules. Such tasks would require additional components and evaluation.

\newpage

## Page 13 — Testing and observed results

The project includes unit tests for the logic engine and integration tests for the Flask application. The logic tests check derivation of `Studies` and `CourseOf`, department association through teaching and enrollment, existential constraint behavior, duplicate prevention, supported query answers, and rejection of unsupported questions.

Application tests cover dashboard and query rendering, record management, role restrictions, academic workflows, finance and attendance-related questions, communication routes, form suggestions, migrations, and the VTU catalog import flow. The import test checks that required subjects are enrolled automatically, elective options are not automatically enrolled, and only one choice from an elective group can be assigned.

At the time this report was prepared, the automated suite completed with **34 tests passing**. This is a functional regression result; it is not evidence of production-scale performance or institutional user acceptance.

### Example reasoning trace

Given:

```text
Student(Ravi)
Enrolled(Ravi, AI)
OfferedBy(AI, CSE)
```

The engine applies Rule 1 to derive `Studies(Ravi, AI)`. It then applies Rule 4 to derive `AssociatedWith(Ravi, CSE)`. A query asking whether Ravi is associated with CSE can return “Yes” and display the supporting facts and applied rules.

This example shows the main expected result: not only is a conclusion available, but its supporting chain is inspectable. The data must still be correct; a missing enrollment or offering fact prevents the inference and yields no matching conclusion.

\newpage

## Page 14 — Limitations, privacy, and future work

The current query language supports a defined set of templates. It is not unrestricted natural-language understanding. The rule set is intentionally small, and a fact not represented in the database cannot be inferred. Some attendance, fee, and submission facts use application-defined calculation or closed-world assumptions, which should be documented for users and reviewed if policy changes.

The application is intended for local development and demonstration. The built-in Flask server is not appropriate for production deployment. Production use requires HTTPS, a production WSGI server, secure session secrets, access logging, protected database and file storage, backups, and review of privacy and retention requirements.

Private messaging is stored in the local database and is not end-to-end encrypted. WebRTC media is not recorded by the application, but STUN/TURN providers and network infrastructure can observe metadata. Calls and screen sharing require user consent and a secure browser context. Phone numbers are not exposed in the communication interface.

The VTU catalog is explicitly partial. Only the six named branch entries are packaged as verified data. Other branch lists should be added only after checking the relevant official scheme, syllabus, and correction circulars. The system should never silently substitute a generic template for a branch-specific subject list.

Future work could include: complete official curriculum verification and version tracking; branch/program identifiers on departments; formal rule validation; incremental rather than full recomputation; query suggestions based on stored entities; accessible graph visualization; database-level performance indexes; automated backup checks; and user testing with faculty and administrators. Any introduction of machine learning should be a separate feature with a clearly stated task, dataset, evaluation plan, and human oversight.

\newpage

## Page 15 — Conclusion and references

CampusLogic demonstrates how a college information system can combine ordinary data management with an explainable symbolic AI component. College records are synchronized into predicates; Horn-clause rules derive relationships; a forward-chaining engine repeats inference until reaching a fixed point; and a controlled query processor presents answers with supporting facts and rule explanations.

The design favors transparency over unrestricted language flexibility. It avoids fabricating missing departments, validates manual facts, prevents duplicate derivations, and distinguishes stored evidence from inferred conclusions. The web application adds role-based academic and administrative workflows around that knowledge model.

The main conclusion is that First-Order Logic and forward chaining are useful for a bounded set of institutional relationships when rules are clear and explanations matter. Their effectiveness depends on accurate records, carefully maintained rules, and transparent limitations. The current system is a demonstrator and a foundation for further validation, not a production campus ERP or a general AI assistant.

### Project references

- [README and setup guide](./README.md)
- [Flask routes and application setup](./app.py)
- [Academic and communication workflows](./college_modules.py)
- [SQLite schema and fact synchronization](./database.py)
- [Forward-chaining engine](./fol_engine.py)
- [Declarative rules](./inference_rules.py)
- [Fact validation and query history](./knowledge_base.py)
- [Query parsing and evaluation](./query_processor.py)
- [Logic and application tests](./tests/)
- [Official VTU 2022 scheme index](https://vtu.ac.in/b-e-scheme-syllabus/)
