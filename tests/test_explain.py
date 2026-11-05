"""推导过程的说人话版本：编号要自洽，措辞要能读，不许漏出内部占位名。"""

import unittest

from ravel import samples
from ravel.explain import Explanation, Step, derivation, explain, explain_or_fail
from ravel.registry import parse_requirements
from ravel.solver import ROOT, SolveFailure, Solver


def fail_on(text, registry=None):
    registry = registry or samples.build()
    requirements = parse_requirements(text, source="<测试>")
    try:
        Solver(registry, requirements).solve()
    except SolveFailure as exc:
        return explain(exc, registry, requirements), exc
    raise AssertionError("这组需求本该无解")


class StepShapeTests(unittest.TestCase):
    def setUp(self):
        self.explanation, _ = fail_on("web ^2.0\nhttp ^1.0")

    def test_steps_are_numbered_from_one(self):
        numbers = [step.number for step in self.explanation.steps]
        self.assertEqual(numbers, list(range(1, len(numbers) + 1)))

    def test_parents_always_point_backwards(self):
        for step in self.explanation.steps:
            for parent in step.parents:
                self.assertLess(parent, step.number,
                                f"第 {step.number} 条引用了后面的第 {parent} 条")

    def test_only_derived_steps_have_parents(self):
        for step in self.explanation.steps:
            if step.parents:
                self.assertIn("可得", step.body)
                self.assertTrue(step.is_derived)
            else:
                self.assertNotIn("可得", step.body)

    def test_every_step_has_text(self):
        for step in self.explanation.steps:
            self.assertTrue(step.body.strip())

    def test_step_is_frozen(self):
        step = Step(1, "x")
        with self.assertRaises(Exception):
            step.number = 2


class WordingTests(unittest.TestCase):
    def setUp(self):
        self.explanation, self.error = fail_on("web ^2.0\nhttp ^1.0")

    def test_no_internal_placeholder_leaks(self):
        self.assertNotIn(ROOT, self.explanation.render())
        self.assertNotIn("__", self.explanation.render())

    def test_root_requirements_keep_the_original_spelling(self):
        text = self.explanation.render()
        self.assertIn("web ^2.0", text)
        self.assertIn("http ^1.0", text)

    def test_dependency_lines_name_both_sides(self):
        text = self.explanation.render()
        self.assertIn("web 2.0.0 依赖 http ^2", text)

    def test_final_step_says_there_is_no_solution(self):
        self.assertIn("没有任何一组版本能同时满足全部需求", self.explanation.steps[-1].body)

    def test_render_has_a_conclusion(self):
        self.assertIn("结论：", self.explanation.render())

    def test_render_lists_suggestions(self):
        text = self.explanation.render()
        self.assertIn("可以试试：", text)
        self.assertTrue(any("放宽" in item or "去掉" in item
                            for item in self.explanation.suggestions))

    def test_headline_mentions_the_requirements(self):
        self.assertIn("web ^2.0", self.explanation.headline())

    def test_stats_are_carried_over(self):
        self.assertIsNotNone(self.explanation.stats)
        self.assertGreater(self.explanation.stats.conflicts, 0)

    def test_root_requirements_are_collected(self):
        names = [str(item) for item in self.explanation.root_requirements]
        self.assertIn("web ^2.0", names)
        self.assertIn("http ^1.0", names)


class UnknownPackageTests(unittest.TestCase):
    def setUp(self):
        self.explanation, _ = fail_on("nope ^1.0")

    def test_says_the_package_is_missing(self):
        self.assertIn("registry 里根本没有 nope 这个包", self.explanation.render())

    def test_suggests_adding_it(self):
        joined = " ".join(self.explanation.suggestions)
        self.assertIn("nope", joined)

    def test_still_hides_the_placeholder(self):
        self.assertNotIn(ROOT, self.explanation.render())


class SuggestionTests(unittest.TestCase):
    def test_relaxing_a_requirement_is_offered(self):
        explanation, _ = fail_on("web ^2.0\nhttp ^1.0")
        joined = " ".join(explanation.suggestions)
        self.assertIn("放宽", joined)

    def test_suggestions_are_capped(self):
        explanation, _ = fail_on("web ^2.0\nhttp ^1.0")
        self.assertLessEqual(len(explanation.suggestions), 3)

    def test_suggestions_are_computed_not_guessed(self):
        # 建议必须真的能解出来，否则就是骗人
        explanation, _ = fail_on("web ^2.0\nhttp ^1.0")
        self.assertTrue(explanation.suggestions)
        relaxed = parse_requirements("web\nhttp ^1.0")
        self.assertTrue(Solver(samples.build(), relaxed).solve())

    def test_no_registry_means_no_suggestions_beyond_missing_packages(self):
        requirements = parse_requirements("web ^2.0\nhttp ^1.0", source="<测试>")
        try:
            Solver(samples.build(), requirements).solve()
        except SolveFailure as exc:
            explanation = explain(exc)
        self.assertEqual(explanation.suggestions, [])


class DerivationTests(unittest.TestCase):
    def test_derivation_of_a_simple_failure(self):
        _, error = fail_on("nope ^1.0")
        steps = derivation(error.incompatibility)
        self.assertEqual(len(steps), 3)

    def test_explain_or_fail_rejects_solvable_input(self):
        with self.assertRaises(ValueError):
            explain_or_fail(samples.build(), parse_requirements("web ^1.0"))

    def test_explain_or_fail_returns_an_explanation(self):
        explanation = explain_or_fail(samples.build(),
                                      parse_requirements("web ^2.0\nhttp ^1.0"))
        self.assertIsInstance(explanation, Explanation)

    def test_deeper_conflict_has_more_steps(self):
        shallow, _ = fail_on("nope ^1.0")
        deep, _ = fail_on("api ^2.0\nhttp ^1.0")
        self.assertGreater(len(deep.steps), len(shallow.steps))


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
