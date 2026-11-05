"""求解器：五个内置场景、回退、预发布、以及各种失败形态。"""

import unittest

from ravel import samples
from ravel.errors import RegistryError, SolveError
from ravel.registry import Registry, parse_requirements
from ravel.solver import (
    ROOT, Assignment, PartialSolution, SolveFailure, Solver, resolve,
)
from ravel.terms import Incompatibility, Term
from ravel.versions import Version, VersionRange, parse_constraint


def build(packages):
    return Registry.from_dict({"packages": packages}, source="<测试>")


def solve(packages, text):
    return Solver(build(packages), parse_requirements(text, source="<测试>")).solve()


def failure(source, text):
    try:
        if isinstance(source, Registry):
            Solver(source, parse_requirements(text, source="<测试>")).solve()
        else:
            solve(source, text)
    except SolveFailure as exc:
        return exc
    raise AssertionError("这组需求本该无解")


CHAIN = {
    "a": {"releases": [{"version": "1.0.0"}, {"version": "2.0.0"}]},
    "b": {"releases": [{"version": "1.0.0", "dependencies": {"a": "^1.0"}},
                       {"version": "2.0.0", "dependencies": {"a": "^2.0"}}]},
}


class ScenarioTests(unittest.TestCase):
    def setUp(self):
        self.registry = samples.build()

    def run_scenario(self, name):
        text = next(item[1] for item in samples.SCENARIOS if item[0] == name)
        return Solver(self.registry, parse_requirements(text, source=name)).solve()

    def test_basic(self):
        solution = self.run_scenario("basic")
        self.assertEqual(solution.as_dict(), {
            "http": "1.8.0", "log": "1.2.0", "metrics": "1.0.0", "web": "1.0.0"})

    def test_basic_needs_no_backtracking(self):
        self.assertEqual(self.run_scenario("basic").stats.backtracks, 0)

    def test_backtrack_picks_the_older_api(self):
        solution = self.run_scenario("backtrack")
        self.assertEqual(str(solution.version_of("api")), "1.0.0")
        self.assertEqual(str(solution.version_of("http")), "1.8.0")

    def test_backtrack_actually_backtracks(self):
        solution = self.run_scenario("backtrack")
        self.assertGreater(solution.stats.backtracks, 0)
        self.assertGreater(solution.stats.conflicts, 0)

    def test_conflict_has_no_solution(self):
        with self.assertRaises(SolveFailure):
            self.run_scenario("conflict")

    def test_unknown_package_has_no_solution(self):
        with self.assertRaises(SolveFailure):
            self.run_scenario("unknown")

    def test_chain_pulls_eight_packages(self):
        solution = self.run_scenario("chain")
        self.assertEqual(len(solution), 8)
        self.assertEqual(str(solution.version_of("web")), "2.1.0")

    def test_prerelease_is_skipped_unless_named(self):
        solution = self.run_scenario("prerelease")
        self.assertEqual(str(solution.version_of("tls")), "2.0.0")

    def test_every_scenario_is_reachable_by_name(self):
        for name, text, note in samples.SCENARIOS:
            with self.subTest(name=name):
                self.assertTrue(text)
                self.assertTrue(note)
                parse_requirements(text, source=name)


class SelectionTests(unittest.TestCase):
    def test_newest_compatible_version_wins(self):
        solution = solve({"a": {"releases": [{"version": "1.0.0"},
                                             {"version": "1.1.0"},
                                             {"version": "1.2.0"}]}}, "a ^1.0")
        self.assertEqual(str(solution.version_of("a")), "1.2.0")

    def test_yanked_versions_are_skipped(self):
        solution = solve({"a": {"releases": [
            {"version": "1.0.0"},
            {"version": "2.0.0", "yanked": True}]}}, "a *")
        self.assertEqual(str(solution.version_of("a")), "1.0.0")

    def test_all_versions_yanked_means_no_solution(self):
        with self.assertRaises(SolveFailure):
            solve({"a": {"releases": [{"version": "1.0.0", "yanked": True}]}}, "a *")

    def test_exact_pin(self):
        solution = solve({"a": {"releases": [{"version": "1.0.0"},
                                             {"version": "2.0.0"}]}}, "a 1.0.0")
        self.assertEqual(str(solution.version_of("a")), "1.0.0")

    def test_transitive_dependency_is_pulled_in(self):
        solution = solve(CHAIN, "b ^2.0")
        self.assertEqual(solution.as_dict(), {"a": "2.0.0", "b": "2.0.0"})

    def test_older_b_requires_older_a(self):
        solution = solve(CHAIN, "b ^1.0")
        self.assertEqual(solution.as_dict(), {"a": "1.0.0", "b": "1.0.0"})

    def test_deep_chain(self):
        packages = {}
        for index in range(12):
            deps = {} if index == 11 else {f"p{index + 1}": "^1.0"}
            packages[f"p{index}"] = {"releases": [{"version": "1.0.0", "dependencies": deps}]}
        solution = solve(packages, "p0 ^1.0")
        self.assertEqual(len(solution), 12)

    def test_cycle_between_two_packages(self):
        packages = {
            "a": {"releases": [{"version": "1.0.0", "dependencies": {"b": "^1.0"}}]},
            "b": {"releases": [{"version": "1.0.0", "dependencies": {"a": "^1.0"}}]},
        }
        solution = solve(packages, "a ^1.0")
        self.assertEqual(solution.as_dict(), {"a": "1.0.0", "b": "1.0.0"})

    def test_diamond_dependency(self):
        packages = {
            "base": {"releases": [{"version": "1.0.0"}, {"version": "2.0.0"}]},
            "left": {"releases": [{"version": "1.0.0", "dependencies": {"base": "^1.0"}}]},
            "right": {"releases": [{"version": "1.0.0", "dependencies": {"base": "^1.0"}}]},
        }
        solution = solve(packages, "left ^1.0\nright ^1.0")
        self.assertEqual(str(solution.version_of("base")), "1.0.0")

    def test_version_is_chosen_once_for_shared_dependency(self):
        packages = {
            "base": {"releases": [{"version": "1.0.0"}, {"version": "2.0.0"}]},
            "left": {"releases": [{"version": "1.0.0", "dependencies": {"base": "^2.0"}}]},
            "right": {"releases": [{"version": "1.0.0", "dependencies": {"base": "^2.0"}}]},
        }
        solution = solve(packages, "left ^1.0\nright ^1.0")
        self.assertEqual(solution.as_dict(), {"base": "2.0.0", "left": "1.0.0",
                                              "right": "1.0.0"})

    def test_requirements_are_all_installed(self):
        solution = solve(CHAIN, "a ^1.0\nb ^1.0")
        self.assertEqual(set(solution.package_names()), {"a", "b"})

    def test_determinism(self):
        first = solve(CHAIN, "b *").as_dict()
        second = solve(CHAIN, "b *").as_dict()
        self.assertEqual(first, second)

    def test_solving_twice_with_the_same_solver_object(self):
        solver = Solver(samples.build(), parse_requirements("web ^1.0"))
        self.assertEqual(solver.solve().as_dict(), solver.solve().as_dict())


class PrereleaseTests(unittest.TestCase):
    def setUp(self):
        self.packages = {"a": {"releases": [{"version": "1.0.0"},
                                            {"version": "2.0.0-rc.1"},
                                            {"version": "2.0.0"}]}}

    def test_prerelease_is_skipped_by_a_plain_range(self):
        self.assertEqual(str(solve(self.packages, "a ^2.0").version_of("a")), "2.0.0")

    def test_prerelease_is_skipped_by_a_wildcard(self):
        self.assertEqual(str(solve(self.packages, "a *").version_of("a")), "2.0.0")

    def test_naming_the_prerelease_lets_it_in(self):
        solution = solve({"a": {"releases": [{"version": "2.0.0-rc.1"}]}}, "a ^2.0.0-rc.1")
        self.assertEqual(str(solution.version_of("a")), "2.0.0-rc.1")

    def test_prerelease_lower_bound_only_covers_its_own_series(self):
        packages = {"a": {"releases": [{"version": "2.0.0-rc.1"},
                                       {"version": "2.1.0-rc.1"}]}}
        solution = solve(packages, "a ^2.0.0-rc.1")
        self.assertEqual(str(solution.version_of("a")), "2.0.0-rc.1")

    def test_prerelease_compare_earlier_than_its_release(self):
        packages = {"a": {"releases": [{"version": "2.0.0-rc.1"}]}}
        with self.assertRaises(SolveFailure):
            solve(packages, "a >=2.0.0")


class FailureTests(unittest.TestCase):
    def test_missing_package(self):
        exc = failure(CHAIN, "nope ^1.0")
        self.assertIsNotNone(exc.incompatibility)

    def test_impossible_range(self):
        with self.assertRaises(SolveFailure):
            solve({"a": {"releases": [{"version": "1.0.0"}]}}, "a ^2.0")

    def test_duplicate_requirement_lines_are_rejected(self):
        # 同一个包在需求里写两遍，多半是手误，报错比悄悄取交集好
        with self.assertRaises(RegistryError):
            solve({"a": {"releases": [{"version": "1.0.0"}]}}, "a ^1.0\na ^1.0")

    def test_dependency_on_missing_package(self):
        packages = {"a": {"releases": [{"version": "1.0.0",
                                        "dependencies": {"ghost": "^1.0"}}]}}
        with self.assertRaises(SolveFailure):
            solve(packages, "a ^1.0")

    def test_conflicting_dependencies(self):
        packages = {
            "base": {"releases": [{"version": "1.0.0"}, {"version": "2.0.0"}]},
            "left": {"releases": [{"version": "1.0.0", "dependencies": {"base": "^1.0"}}]},
            "right": {"releases": [{"version": "1.0.0", "dependencies": {"base": "^2.0"}}]},
        }
        with self.assertRaises(SolveFailure):
            solve(packages, "left ^1.0\nright ^1.0")

    def test_no_requirements_is_an_error(self):
        with self.assertRaises(SolveError):
            Solver(samples.build(), [])

    def test_failure_keeps_stats(self):
        exc = failure(samples.build(), "web ^2.0\nhttp ^1.0")
        self.assertGreater(exc.stats.conflicts, 0)
        self.assertGreater(exc.stats.steps, 0)

    def test_failure_incompatibility_never_leaks_the_root_name(self):
        exc = failure(samples.build(), "nope ^1.0")
        self.assertNotIn(ROOT, exc.incompatibility.render())

    def test_deep_failure_still_terminates(self):
        packages = {}
        for index in range(10):
            nxt = f"p{index + 1}"
            deps = {} if index == 9 else {nxt: "^1.0"}
            packages[f"p{index}"] = {"releases": [{"version": "1.0.0", "dependencies": deps}]}
        with self.assertRaises(SolveFailure):
            solve(packages, "p0 ^2.0")


class SolutionApiTests(unittest.TestCase):
    def setUp(self):
        self.solution = solve(CHAIN, "b ^2.0")

    def test_contains(self):
        self.assertIn("a", self.solution)
        self.assertNotIn("nope", self.solution)

    def test_len(self):
        self.assertEqual(len(self.solution), 2)

    def test_items_are_sorted(self):
        self.assertEqual([name for name, _ in self.solution.items()], ["a", "b"])

    def test_package_names(self):
        self.assertEqual(self.solution.package_names(), ["a", "b"])

    def test_version_of(self):
        self.assertEqual(self.solution.version_of("a"), Version(2, 0, 0))
        self.assertIsNone(self.solution.version_of("nope"))

    def test_dependencies_of(self):
        pairs = self.solution.dependencies_of("b")
        self.assertEqual(pairs[0][0], "a")
        self.assertEqual(self.solution.dependencies_of("nope"), [])

    def test_as_dict(self):
        self.assertEqual(self.solution.as_dict(), {"a": "2.0.0", "b": "2.0.0"})

    def test_registry_and_requirements_are_kept(self):
        self.assertEqual(self.solution.registry.stats(), (2, 4))
        self.assertEqual(len(self.solution.requirements), 1)

    def test_stats_add_up(self):
        stats = self.solution.stats
        self.assertEqual(stats.decisions, 2)
        self.assertGreaterEqual(stats.derivations, 2)
        self.assertGreater(stats.incompatibilities, 0)

    def test_resolve_helper(self):
        solution = resolve(build(CHAIN), parse_requirements("b ^1.0"))
        self.assertEqual(solution.as_dict(), {"a": "1.0.0", "b": "1.0.0"})


class PartialSolutionTests(unittest.TestCase):
    def setUp(self):
        self.partial = PartialSolution()
        self.partial.add_root(ROOT, Version(0, 0, 0))

    def test_root_is_not_a_decision_level(self):
        self.assertEqual(self.partial.decision_level, 0)
        self.assertIn(ROOT, self.partial.decisions)

    def test_decisions_raise_the_level(self):
        self.partial.add_decision("a", Version(1, 0, 0))
        self.assertEqual(self.partial.decision_level, 1)
        self.partial.add_decision("b", Version(1, 0, 0))
        self.assertEqual(self.partial.decision_level, 2)

    def test_allowed_starts_unconstrained(self):
        self.assertTrue(self.partial.allowed("a").is_any)

    def test_allowed_is_the_intersection(self):
        cause = Incompatibility([Term("a", VersionRange.exact(Version(1, 0, 0)))])
        self.partial.derive(Term("a", parse_constraint(">=1.0")), cause)
        self.partial.derive(Term("a", parse_constraint("<2.0")), cause)
        self.assertEqual(self.partial.allowed("a"), parse_constraint(">=1.0 <2.0"))

    def test_relation_is_inconclusive_for_unknown_packages(self):
        self.assertEqual(self.partial.relation(Term("a", parse_constraint("^1.0"))),
                         "inconclusive")

    def test_relation_becomes_decisive_after_a_derivation(self):
        cause = Incompatibility([Term("a", VersionRange.exact(Version(1, 0, 0)))])
        self.partial.derive(Term("a", parse_constraint("^1.0")), cause)
        self.assertEqual(self.partial.relation(Term("a", parse_constraint("^1.0"))),
                         "satisfied")
        self.assertEqual(self.partial.relation(Term("a", parse_constraint("^2.0"))),
                         "contradicted")

    def test_backtrack_drops_later_levels(self):
        self.partial.add_decision("a", Version(1, 0, 0))
        self.partial.add_decision("b", Version(1, 0, 0))
        self.partial.backtrack(1)
        self.assertEqual(self.partial.decision_level, 1)
        self.assertIn("a", self.partial.decisions)
        self.assertNotIn("b", self.partial.decisions)
        self.assertEqual(len(self.partial.assignments), 2)

    def test_backtrack_to_zero_keeps_only_the_root(self):
        self.partial.add_decision("a", Version(1, 0, 0))
        self.partial.backtrack(0)
        self.assertEqual(list(self.partial.decisions), [ROOT])

    def test_backtrack_to_a_higher_level_is_a_no_op(self):
        self.partial.add_decision("a", Version(1, 0, 0))
        self.partial.backtrack(5)
        self.assertIn("a", self.partial.decisions)

    def test_satisfier_finds_the_assignment_that_settled_it(self):
        cause = Incompatibility([Term("a", VersionRange.exact(Version(1, 0, 0)))])
        first = self.partial.derive(Term("a", parse_constraint(">=1.0")), cause)
        second = self.partial.derive(Term("a", parse_constraint("<2.0")), cause)
        self.assertIs(self.partial.satisfier(Term("a", parse_constraint(">=1.0"))), first)
        self.assertIs(self.partial.satisfier(Term("a", parse_constraint("<2.0"))), second)

    def test_satisfier_of_an_unsatifiable_term_is_none(self):
        self.assertIsNone(self.partial.satisfier(
            Term("a", VersionRange.nothing())))

    def test_assignments_for(self):
        cause = Incompatibility([Term("a", VersionRange.exact(Version(1, 0, 0)))])
        self.partial.derive(Term("a", parse_constraint("^1.0")), cause)
        self.assertEqual(len(self.partial.assignments_for("a")), 1)
        self.assertEqual(len(self.partial.assignments_for("b")), 0)

    def test_assignment_flags(self):
        cause = Incompatibility([Term("a", VersionRange.exact(Version(1, 0, 0)))])
        derived = self.partial.derive(Term("a", parse_constraint("^1.0")), cause)
        decided = self.partial.add_decision("b", Version(1, 0, 0))
        self.assertFalse(derived.is_decision)
        self.assertTrue(decided.is_decision)
        self.assertEqual(derived.name, "a")

    def test_assignment_difference(self):
        cause = Incompatibility([Term("a", VersionRange.exact(Version(1, 0, 0)))])
        assignment = self.partial.derive(Term("a", parse_constraint("^1.0")), cause)
        rest = assignment.difference(Term("a", parse_constraint(">=1.5")))
        self.assertTrue(rest.allowed.contains(Version(1, 0, 0)))


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
