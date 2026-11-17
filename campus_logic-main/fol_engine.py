"""A small, data-driven forward-chaining engine for positive Horn clauses."""

from dataclasses import dataclass

from inference_rules import RULES


def _key(value):
    return value.casefold()


@dataclass(frozen=True)
class Fact:
    predicate: str
    arguments: tuple
    source: str = "base"
    rule_id: str = ""
    substitutions: tuple = ()
    premises: tuple = ()

    @property
    def key(self):
        return (self.predicate.casefold(),) + tuple(_key(arg) for arg in self.arguments)


class ForwardChainer:
    """Evaluates supported rules to a fixed point while retaining derivations."""

    def __init__(self, base_facts):
        self.base_facts = [
            fact if isinstance(fact, Fact) else Fact(fact[0], tuple(arg for arg in fact[1:] if arg != ""))
            for fact in base_facts
        ]
        self.facts = {}
        self._by_predicate = {}
        for fact in self.base_facts:
            self._add(fact)

    def _add(self, fact):
        if fact.key in self.facts:
            return False
        self.facts[fact.key] = fact
        self._by_predicate.setdefault(fact.predicate.casefold(), []).append(fact)
        return True

    def add_derived_fact(self, fact):
        if fact.source != "derived":
            raise ValueError("Only inferred facts can be added as derived facts.")
        return self._add(fact)

    @staticmethod
    def _unify(pattern_args, fact_args, substitution):
        if len(pattern_args) != len(fact_args):
            return None
        result = dict(substitution)
        for pattern, value in zip(pattern_args, fact_args):
            if pattern and pattern[0].islower():
                bound = result.get(pattern)
                if bound is not None and _key(bound) != _key(value):
                    return None
                result[pattern] = value
            elif _key(pattern) != _key(value):
                return None
        return result

    def _matches(self, body):
        partial = [(dict(), [])]
        for predicate, pattern_args in body:
            next_partial = []
            candidates = self._by_predicate.get(predicate.casefold(), [])
            for substitution, matched_facts in partial:
                for fact in candidates:
                    unified = self._unify(pattern_args, fact.arguments, substitution)
                    if unified is not None:
                        next_partial.append((unified, matched_facts + [fact]))
            partial = next_partial
            if not partial:
                break
        return partial

    def run(self):
        productive = True
        while productive:
            productive = False
            for rule in RULES:
                if rule.get("existential"):
                    continue
                predicate, patterns = rule["head"]
                for substitution, premises in self._matches(rule["body"]):
                    arguments = tuple(substitution[name] for name in patterns)
                    derived = Fact(
                        predicate,
                        arguments,
                        source="derived",
                        rule_id=rule["id"],
                        substitutions=tuple(sorted(substitution.items())),
                        premises=tuple(fact.key for fact in premises),
                    )
                    productive = self._add(derived) or productive
        return list(self.facts.values())

    def supporting_facts(self, fact):
        """Return the transitive proof premises in dependency-first order."""
        found = {}

        def visit(current):
            for premise_key in current.premises:
                premise = self.facts.get(premise_key)
                if premise is not None and premise.key not in found:
                    visit(premise)
                    found[premise.key] = premise

        visit(fact)
        return list(found.values())

    def explain(self, fact):
        if fact.source == "base":
            return "This predicate is stored directly in the knowledge base."
        rule = next((item for item in RULES if item["id"] == fact.rule_id), None)
        if rule is None:
            return "This fact was derived by a logical rule."
        substitutions = ", ".join(f"{key}={value}" for key, value in fact.substitutions)
        premises = [
            self.facts[key]
            for key in fact.premises
            if key in self.facts
        ]
        premise_text = " and ".join(
            f"{premise.predicate}({', '.join(premise.arguments)})"
            for premise in premises
        )
        return f"{rule['formula']} with {substitutions}; premises: {premise_text}."

    def existential_status(self):
        """Report existential rule satisfaction without creating witness values."""
        professors = self._by_predicate.get("professor", [])
        memberships = self._by_predicate.get("belongsto", [])
        known_members = {_key(f.arguments[0]) for f in memberships if len(f.arguments) == 2}
        missing = [fact.arguments[0] for fact in professors if _key(fact.arguments[0]) not in known_members]
        rule = next(item for item in RULES if item.get("existential"))
        return {
            "rule_id": rule["id"],
            "satisfied": not missing,
            "missing_witnesses": missing,
            "message": (
                "Every known professor has a known department membership."
                if not missing
                else "No department was inferred for: " + ", ".join(missing)
            ),
        }


def infer(base_facts):
    engine = ForwardChainer(base_facts)
    facts = engine.run()
    return engine, facts
