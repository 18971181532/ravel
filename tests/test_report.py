"""有解时的账：表格、备注、以及 `ravel why` 的每一节。"""

import unittest

from ravel import samples
from ravel.registry import Registry, Release, parse_requirements
from ravel.report import (
    Line, display_width, excluders, lines, note_for, pad, render, requirements_on,
    self_conflict, solution_version_allowed, why, why_not,
)
from ravel.solver import Solution, Solver
from ravel.versions import Version


def solved(text, registry=None):
    registry = registry or samples.build()
    return Solver(registry, parse_requirements(text, source="<测试>")).solve()


class WidthTests(unittest.TestCase):
    def test_ascii_counts_one(self):
        self.assertEqual(display_width("abc"), 3)

    def test_chinese_counts_two(self):
        self.assertEqual(display_width("包"), 2)
        self.assertEqual(display_width("版本"), 4)

    def test_mixed(self):
        self.assertEqual(display_width("a包b"), 4)

    def test_pad_fills_to_the_requested_width(self):
        self.assertEqual(display_width(pad("包", 6)), 6)
        self.assertEqual(display_width(pad("abc", 6)), 6)

    def test_pad_never_truncates(self):
        self.assertEqual(pad("abcdef", 2), "abcdef")


class LookupTests(unittest.TestCase):
    def setUp(self):
        self.solution = solved("web ^1.0\nmetrics ^1.0")

    def test_requirements_on_includes_root_and_dependencies(self):
        found = requirements_on(self.solution, "log")
        self.assertEqual(len(found), 2, found)
        self.assertTrue(any("web 1.0.0" in item for item in found))
        self.assertTrue(any("metrics 1.0.0" in item for item in found))

    def test_requirements_on_reports_a_root_line(self):
        found = requirements_on(self.solution, "web")
        self.assertEqual(found, ["项目需求 web ^1.0"])

    def test_requirements_on_empty_when_nobody_asks(self):
        self.assertEqual(requirements_on(self.solution, "nope"), [])

    def test_excluders_names_the_root_requirement(self):
        found = excluders(self.solution, "web", Version.parse("2.1.0"))
        self.assertEqual(found, ["项目需求 web ^1.0"])

    def test_excluders_names_a_dependency(self):
        found = excluders(self.solution, "http", Version.parse("2.0.0"))
        self.assertTrue(any("web 1.0.0 依赖 http ^1" in item for item in found))

    def test_excluders_is_empty_for_an_acceptable_version(self):
        self.assertEqual(excluders(self.solution, "log", Version.parse("1.0.0")), [])

    def test_allowed_range_is_the_intersection(self):
        allowed = solution_version_allowed(self.solution, "http")
        self.assertTrue(allowed.contains(Version.parse("1.8.0")))
        self.assertFalse(allowed.contains(Version.parse("2.0.0")))

    def test_allowed_range_of_an_unknown_package_is_any(self):
        self.assertTrue(solution_version_allowed(self.solution, "nope").is_any)


class WhyNotTests(unittest.TestCase):
    def test_newest_version_says_so(self):
        solution = solved("api ^2.0")
        self.assertEqual(note_for(solution, "web", Version.parse("2.1.0")),
                         "已经是最新版本")

    def test_yanked_version_says_so(self):
        solution = solved("http ^1.0")
        self.assertEqual(why_not(solution, "http", Version.parse("1.9.0")),
                         "这个版本已被撤回")

    def test_root_requirement_is_blamed(self):
        solution = solved("http ^1.0")
        self.assertIn("项目需求", why_not(solution, "http", Version.parse("2.0.0")))

    def test_own_dependency_conflict_is_blamed(self):
        solution = solved("api *\nhttp ^1.0")
        message = why_not(solution, "api", Version.parse("2.0.0"))
        self.assertIn("它自己要 web ^2", message)
        self.assertIn("web 已经定在 1.0.0", message)

    def test_prerelease_needs_to_be_named(self):
        solution = solved("tls ^2.0")
        self.assertIn("预发布", why_not(solution, "tls", Version.parse("2.1.0-rc.1")))

    def test_self_conflict_helper_returns_none_when_fine(self):
        solution = solved("api *\nhttp ^1.0")
        self.assertIsNone(self_conflict(solution, "api", Version.parse("1.0.0")))

    def test_unknown_package(self):
        solution = solved("http ^1.0")
        self.assertEqual(why_not(solution, "nope", Version.parse("1.0.0")),
                         "registry 里没有这个包")


class RenderTests(unittest.TestCase):
    def setUp(self):
        self.solution = solved("web ^1.0\nmetrics ^1.0")

    def test_header_row(self):
        text = render(self.solution)
        self.assertIn("包", text.splitlines()[0])
        self.assertIn("版本", text.splitlines()[0])
        self.assertIn("说明", text.splitlines()[0])

    def test_every_package_appears(self):
        text = render(self.solution)
        for name in self.solution.package_names():
            self.assertIn(name, text)

    def test_summary_line_counts_packages(self):
        self.assertIn(f"共 {len(self.solution)} 个包", render(self.solution))

    def test_summary_mentions_backtracks(self):
        self.assertIn("次回退", render(self.solution))

    def test_version_column_starts_at_the_same_place(self):
        rows = render(self.solution).splitlines()[2:2 + len(self.solution)]
        offsets = set()
        for row, name in zip(rows, self.solution.package_names()):
            version = self.solution.as_dict()[name]
            offsets.add(display_width(row[:row.index(version)]))
        self.assertEqual(len(offsets), 1, rows)

    def test_empty_solution(self):
        registry = samples.build()
        empty = Solution(registry, (), {})
        self.assertIn("没有任何包", render(empty))

    def test_lines_helper(self):
        rows = lines(self.solution)
        self.assertTrue(all(isinstance(row, Line) for row in rows))
        self.assertEqual([row.name for row in rows], self.solution.package_names())


class WhyTests(unittest.TestCase):
    def setUp(self):
        self.solution = solved("api *\nhttp ^1.0")

    def test_shows_the_chosen_version(self):
        self.assertIn("api 1.0.0", why(self.solution, "api"))

    def test_lists_who_asked_for_it(self):
        self.assertIn("谁要它", why(self.solution, "api"))

    def test_lists_the_allowed_range(self):
        self.assertIn("最终允许的区间", why(self.solution, "api"))

    def test_explains_newer_versions(self):
        text = why(self.solution, "api")
        self.assertIn("更新的版本为什么没选", text)
        self.assertIn("2.0.0", text)

    def test_says_when_it_is_the_newest(self):
        solution = solved("api ^2.0")
        self.assertIn("最新版本", why(solution, "api"))

    def test_lists_its_own_dependencies(self):
        text = why(self.solution, "api")
        self.assertIn("它自己要求的", text)
        self.assertIn("web", text)

    def test_case_insensitive(self):
        self.assertIn("api 1.0.0", why(self.solution, "API"))

    def test_unknown_package_raises(self):
        with self.assertRaises(KeyError):
            why(self.solution, "nope")

    def test_package_that_was_not_selected(self):
        registry = Registry.from_dict({"packages": {
            "a": {"releases": [{"version": "1.0.0"}]},
            "b": {"releases": [{"version": "1.0.0"}]},
        }})
        solution = Solver(registry, parse_requirements("a")).solve()
        self.assertIn("没有被选中", why(solution, "b"))

    def test_description_is_shown(self):
        self.assertIn("业务接口层", why(self.solution, "api"))


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
