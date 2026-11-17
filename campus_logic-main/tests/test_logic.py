import unittest

from fol_engine import infer
from query_processor import UnsupportedQuery, evaluate_query, parse_query


class ForwardChainingTests(unittest.TestCase):
    def setUp(self):
        self.base_facts = [
            ("Student", "Ravi", ""),
            ("Professor", "Meena", ""),
            ("Course", "AI", ""),
            ("Department", "CSE", ""),
            ("Enrolled", "Ravi", "AI"),
            ("Teaches", "Meena", "AI"),
            ("OfferedBy", "AI", "CSE"),
        ]
        self.engine, self.facts = infer(self.base_facts)
        self.by_key = {fact.key: fact for fact in self.facts}

    def test_derives_studies_and_course_department(self):
        studies = self.by_key[("studies", "ravi", "ai")]
        course_of = self.by_key[("courseof", "ai", "cse")]
        self.assertEqual(studies.rule_id, "R1")
        self.assertEqual(course_of.rule_id, "R3")

    def test_derives_associations_from_study_and_teaching(self):
        association = self.by_key[("associatedwith", "ravi", "cse")]
        professor_association = self.by_key[("associatedwith", "meena", "cse")]
        self.assertEqual(association.rule_id, "R4")
        self.assertEqual(professor_association.rule_id, "R5")

    def test_existential_constraint_does_not_invent_department(self):
        status = self.engine.existential_status()
        self.assertFalse(status["satisfied"])
        self.assertEqual(status["missing_witnesses"], ["Meena"])
        self.assertNotIn(("belongsto", "meena", "unknown"), self.by_key)

    def test_duplicate_derivations_are_prevented(self):
        duplicate_facts = self.base_facts + [("Enrolled", "Ravi", "AI")]
        _, facts = infer(duplicate_facts)
        keys = [fact.key for fact in facts]
        self.assertEqual(len(keys), len(set(keys)))


class QueryProcessorTests(unittest.TestCase):
    def setUp(self):
        base_facts = [
            ("Student", "Ravi", ""),
            ("Student", "Ananya", ""),
            ("Professor", "Meena", ""),
            ("Professor", "Rahul", ""),
            ("Course", "AI", ""),
            ("Course", "DBMS", ""),
            ("Department", "CSE", ""),
            ("Enrolled", "Ravi", "AI"),
            ("Enrolled", "Ananya", "AI"),
            ("Teaches", "Meena", "AI"),
            ("Teaches", "Rahul", "DBMS"),
            ("BelongsTo", "Meena", "CSE"),
            ("OfferedBy", "AI", "CSE"),
        ]
        self.engine, self.facts = infer(base_facts)

    def test_supported_question_patterns_and_inferred_explanation(self):
        query = "Which students study AI?"
        result = evaluate_query(query, self.facts, self.engine)
        self.assertEqual(result["answer"], ["Ravi", "Ananya"])
        self.assertIn("Enrollment implies study", result["rules_applied"])
        self.assertTrue(any("Student(x)" in fact["derivation"] for fact in result["matched_facts"]))

    def test_teaching_query_matches_stored_predicates(self):
        result = evaluate_query("Who teaches AI?", self.facts, self.engine)
        self.assertEqual(result["answer"], ["Meena"])
        self.assertEqual(result["matched_predicates"], ["Teaches"])
        self.assertEqual(result["rules_applied"], [])

    def test_all_eight_supported_queries_return_data_driven_answers(self):
        questions = {
            "Who teaches AI?": ["Meena"],
            "Which students are enrolled in AI?": ["Ravi", "Ananya"],
            "Is Ravi associated with CSE?": True,
            "Which professors belong to CSE?": ["Meena"],
            "Which courses are offered by CSE?": ["AI"],
            "Which students study AI?": ["Ravi", "Ananya"],
            "Which department is associated with Meena?": ["CSE"],
            "Which professors teach courses offered by CSE?": ["Meena"],
        }
        for question, expected in questions.items():
            with self.subTest(question=question):
                result = evaluate_query(question, self.facts, self.engine)
                self.assertEqual(result["answer"], expected)

    def test_query_parser_rejects_unrestricted_language(self):
        with self.assertRaises(UnsupportedQuery):
            parse_query("Tell me everything about the college")

    def test_association_uses_inferred_relation(self):
        result = evaluate_query("Is Ravi associated with CSE?", self.facts, self.engine)
        self.assertIs(result["answer"], True)
        self.assertEqual(
            result["rules_applied"],
            [
                "Enrollment implies study",
                "Students inherit course department association",
            ],
        )
        self.assertTrue(
            any(fact["predicate"] == "Enrolled" for fact in result["matched_facts"])
        )


if __name__ == "__main__":
    unittest.main()
