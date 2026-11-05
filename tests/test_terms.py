"""项与不相容：求解器唯一的两个数据结构。"""

import unittest

from ravel.registry import Requirement
from ravel.terms import (
    CONTRADICTED, INCONCLUSIVE, SATISFIED, Conflict, Dependency,
    Incompatibility, NoVersions, Term,
)
from ravel.versions import ANY, NOTHING, Version, VersionRange, parse_constraint


def term(name, text):
    return Term(name, parse_constraint(text))


class TermTests(unittest.TestCase):
    def test_negate_flips_the_allowed_set(self):
        item = term("a", "^1.0")
        self.assertEqual(item.negate().allowed, parse_constraint("<1.0 || >=2.0"))

    def test_negate_twice_is_identity(self):
        item = term("a", "^1.0")
        self.assertEqual(item.negate().negate(), item)

    def test_negate_of_any_is_nothing(self):
        self.assertTrue(Term("a", ANY).negate().allowed.is_empty)

    def test_negate_of_nothing_is_any(self):
        self.assertTrue(Term("a", NOTHING).negate().allowed.is_any)

    def test_satisfied_when_the_whole_range_fits(self):
        item = term("a", ">=1.0")
        self.assertEqual(item.relation_to(parse_constraint(">=1.5")), SATISFIED)

    def test_satisfied_when_equal(self):
        item = term("a", "^1.0")
        self.assertEqual(item.relation_to(parse_constraint("^1.0")), SATISFIED)

    def test_contradicted_when_disjoint(self):
        item = term("a", "^1.0")
        self.assertEqual(item.relation_to(parse_constraint("^2.0")), CONTRADICTED)

    def test_inconclusive_when_partially_overlapping(self):
        item = term("a", "^1.0")
        self.assertEqual(item.relation_to(parse_constraint(">=1.5")), INCONCLUSIVE)

    def test_empty_range_satisfies_everything(self):
        item = term("a", "^1.0")
        self.assertEqual(item.relation_to(NOTHING), SATISFIED)

    def test_any_range_is_only_satisfied_by_any(self):
        self.assertEqual(Term("a", ANY).relation_to(parse_constraint("1.0.0")), SATISFIED)
        self.assertEqual(Term("a", ANY).relation_to(ANY), SATISFIED)

    def test_difference(self):
        diff = term("a", "^1.0").difference(term("a", ">=1.5"))
        self.assertIsNotNone(diff)
        self.assertTrue(diff.allowed.contains(Version.parse("1.0.0")))
        self.assertFalse(diff.allowed.contains(Version.parse("1.6.0")))

    def test_difference_is_none_when_nothing_is_left(self):
        self.assertIsNone(term("a", ">=1.5, <1.9").difference(term("a", "^1.0")))

    def test_difference_keeps_the_name(self):
        diff = term("a", "^1.0").difference(term("a", ">=1.5"))
        self.assertEqual(diff.name, "a")

    def test_render_exact(self):
        self.assertEqual(term("a", "1.2.3").render(), "a 1.2.3")

    def test_render_range(self):
        self.assertEqual(term("a", "^1.0").render(), "a 在 ^1 里")

    def test_render_uses_the_complement_when_it_is_shorter(self):
        self.assertEqual(term("a", "!=1.5.0").render(), "a 不是 1.5.0")

    def test_render_nothing(self):
        self.assertEqual(Term("a", NOTHING).render(), "没有 a")

    def test_render_any(self):
        self.assertEqual(Term("a", ANY).render(), "a 的任何版本")

    def test_terms_are_hashable(self):
        self.assertEqual(len({term("a", "^1.0"), term("a", "^1.0"), term("b", "^1.0")}), 2)


class IncompatibilityTests(unittest.TestCase):
    def test_terms_are_kept_in_order(self):
        item = Incompatibility([term("a", "^1.0"), term("b", "^2.0")])
        self.assertEqual([t.name for t in item.terms], ["a", "b"])

    def test_duplicate_names_are_intersected(self):
        item = Incompatibility([term("a", ">=1.0"), term("a", "<2.0")])
        self.assertEqual(len(item.terms), 1)
        self.assertEqual(item.terms[0].allowed, parse_constraint(">=1.0 <2.0"))

    def test_contradictory_duplicates_become_empty(self):
        item = Incompatibility([term("a", "^1.0"), term("a", "^2.0")])
        self.assertTrue(item.terms[0].allowed.is_empty)

    def test_len(self):
        self.assertEqual(len(Incompatibility([term("a", "^1.0")])), 1)

    def test_term_for(self):
        item = Incompatibility([term("a", "^1.0"), term("b", "^2.0")])
        self.assertEqual(item.term_for("b").name, "b")
        self.assertIsNone(item.term_for("c"))

    def test_is_failure_only_when_empty(self):
        self.assertTrue(Incompatibility([]).is_failure())
        self.assertFalse(Incompatibility([term("a", "^1.0")]).is_failure())

    def test_render(self):
        item = Incompatibility([term("a", "1.0.0"), term("b", "^2.0")])
        self.assertEqual(item.render(), "a 1.0.0、b 在 ^2 里 不能同时成立")

    def test_render_of_empty(self):
        self.assertEqual(Incompatibility([]).render(), "需求之间互相矛盾")

    def test_sequence_increases(self):
        first = Incompatibility([term("a", "^1.0")])
        second = Incompatibility([term("a", "^1.0")])
        self.assertLess(first.sequence, second.sequence)

    def test_cause_is_kept(self):
        cause = NoVersions("a", parse_constraint("^1.0"))
        self.assertIs(Incompatibility([term("a", "^1.0")], cause).cause, cause)


class CauseTests(unittest.TestCase):
    def test_dependency_records_the_owner(self):
        cause = Dependency("web", Version.parse("1.0.0"),
                           Requirement("http", parse_constraint("^1.0")))
        self.assertEqual(cause.package, "web")
        self.assertEqual(str(cause.version), "1.0.0")
        self.assertEqual(cause.requirement.name, "http")

    def test_no_versions_defaults_to_known(self):
        self.assertTrue(NoVersions("a", ANY).known)
        self.assertFalse(NoVersions("a", ANY, known=False).known)

    def test_conflict_keeps_both_sides(self):
        left = Incompatibility([term("a", "^1.0")])
        right = Incompatibility([term("b", "^1.0")])
        self.assertEqual(Conflict(left, right).left, left)

    def test_causes_are_hashable(self):
        cause = NoVersions("a", parse_constraint("^1.0"))
        self.assertEqual(len({cause, NoVersions("a", parse_constraint("^1.0"))}), 1)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
